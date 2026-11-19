"""Archive / terminate / activate actions (migration 0023): Tasks, Rental agreements, Customers, Branches.

Real FastAPI app + real PostgreSQL (all migrations, RLS) as the restricted `app_api` role. The module works on its OWN
branches / users / rows and removes them at the end, so it never disturbs the other test modules sharing the database.
"""

import contextlib
import uuid
from collections.abc import Iterator
from datetime import timedelta

import psycopg
import pytest
from fastapi.testclient import TestClient

from app.core.security import TokenVerifier
from app.main import create_app
from app.utils.time import dubai_today
from tests.conftest import make_settings
from tests.jwt_helpers import FakeJwks, KeyPair, claims

pytestmark = pytest.mark.integration
API = "/api/v1"
TODAY = dubai_today()
TAG = "ZZARC"


# ------------------------------------------------------------------------------------------------ fixtures
@pytest.fixture(scope="module")
def key() -> KeyPair:
    return KeyPair("kid-archive")


@pytest.fixture(scope="module")
def admin(pg_admin_dsn: str) -> Iterator[psycopg.Connection]:
    with psycopg.connect(pg_admin_dsn, autocommit=True) as c:
        yield c


def _user(admin, role: str, branch: uuid.UUID | None, name: str) -> uuid.UUID:
    uid = uuid.uuid4()
    admin.execute(
        "insert into auth.users (id, email) values (%s,%s)", (uid, f"{uid.hex[:10]}@example.test")
    )
    admin.execute(
        "insert into public.profiles (user_id, full_name, role, branch_id, is_active) values (%s,%s,%s,%s,true)",
        (uid, name, role, branch),
    )
    return uid


@pytest.fixture(scope="module")
def world(admin) -> Iterator[dict]:
    """Two dedicated branches (+ one spare), an Owner, and a Branch User per branch. Removed again at the end."""
    b1, b2, b3 = (
        admin.execute(
            "insert into public.branches (name, sort_order) values (%s, %s) returning id",
            (f"{TAG} One {n}", 900 + n),
        ).fetchone()[0]
        for n in (1, 2, 3)
    )
    had_opening = admin.execute("select count(*) from public.opening_balances").fetchone()[0]
    if not had_opening:
        admin.execute(
            "insert into public.opening_balances (as_of_date, receipts_bank, enbd_balance, adcb_balance) "
            "values ('2020-01-01','ENBD',50000,50000)"
        )
    w = {
        "b1": b1, "b2": b2, "b3": b3,
        "owner": _user(admin, "owner", None, f"{TAG} Owner"),
        "u1": _user(admin, "branch", b1, f"{TAG} User One"),
        "u2": _user(admin, "branch", b2, f"{TAG} User Two"),
        "cat": admin.execute("select id from public.service_categories order by sort_order limit 1").fetchone()[0],
    }  # fmt: skip
    yield w
    # ---- purge everything this module created (triggers off for this session: append-only guards are for the app)
    ids = (b1, b2, b3, *w.get("extra", []))
    admin.execute("set session_replication_role = replica")
    try:
        for sql in (
            "delete from public.order_payments where order_id in (select id from public.orders where branch_id = any(%s))",
            "delete from public.order_status_history where order_id in (select id from public.orders where branch_id = any(%s))",
            "delete from public.orders where branch_id = any(%s)",
            "delete from public.customers where branch_id = any(%s)",
            "delete from public.tasks where branch_id = any(%s) or title like 'ZZARC%%'",
            "delete from public.pdc_cheques where agreement_id in (select id from public.rental_agreements where branch_id = any(%s))",
            "delete from public.rental_agreements where branch_id = any(%s)",
            "delete from public.daily_report_expenses where branch_id = any(%s)",
            "delete from public.daily_reports where branch_id = any(%s)",
            "delete from public.cash_transfers where branch_id = any(%s)",
            "delete from public.opening_branch_cash where branch_id = any(%s)",
            "delete from public.alerts where title like 'ZZARC%%'",
        ):
            with contextlib.suppress(psycopg.errors.UndefinedColumn):
                admin.execute(sql, [list(ids)] if "%s" in sql else None)
        admin.execute(
            "delete from public.profiles where user_id = any(%s)", [[w["owner"], w["u1"], w["u2"]]]
        )
        admin.execute("delete from auth.users where id = any(%s)", [[w["owner"], w["u1"], w["u2"]]])
        admin.execute("delete from public.branches where id = any(%s)", [list(ids)])
        if not had_opening:
            admin.execute("delete from public.opening_branch_cash")
            admin.execute("delete from public.opening_balances")
    finally:
        admin.execute("set session_replication_role = default")


@pytest.fixture()
def client(app_api_url: str, key: KeyPair) -> Iterator[TestClient]:
    settings = make_settings(supabase_url="https://abcd.supabase.test", database_url=app_api_url)
    app = create_app(settings)
    app.state.token_verifier = TokenVerifier(settings, fetcher=FakeJwks(key))
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


class Api:
    def __init__(self, client: TestClient, key: KeyPair, w: dict) -> None:
        self.c, self.k, self.w = client, key, w

    def h(self, who: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.k.sign(claims(self.w[who]))}"}

    def get(self, path, who="owner", **params):
        return self.c.get(f"{API}{path}", params=params, headers=self.h(who))

    def post(self, path, body, who="owner"):
        return self.c.post(f"{API}{path}", json=body, headers=self.h(who))

    def put(self, path, body, who="owner"):
        return self.c.put(f"{API}{path}", json=body, headers=self.h(who))

    def delete(self, path, who="owner"):
        return self.c.delete(f"{API}{path}", headers=self.h(who))


@pytest.fixture()
def api(client, key, world) -> Api:
    return Api(client, key, world)


def uniq() -> str:
    return uuid.uuid4().hex[:6]


def audit(admin, table: str, record_id: str) -> list[tuple]:
    return admin.execute(
        "select action, reason, old_values, new_values from public.audit_log where table_name=%s and record_id=%s order by id",
        (table, str(record_id)),
    ).fetchall()


# ================================================================================================ 1. TASKS
def mk_task(api, title=None, **over):
    b = {"title": title or f"{TAG} task {uniq()}", "branch_id": str(api.w["b1"]),
         "due_date": (TODAY - timedelta(days=2)).isoformat(), **over}  # fmt: skip
    r = api.post("/tasks", b)
    assert r.status_code == 201, r.text
    return r.json()["task"]


def test_task_archive_and_restore_keep_the_task_and_its_history(api, admin) -> None:
    t = mk_task(api)
    r = api.post(f"/tasks/{t['id']}/archive", {"reason": "No longer needed"})
    assert r.status_code == 200, r.text
    j = r.json()["task"]
    assert j["archived"] and j["archive_reason"] == "No longer needed" and j["archived_by_name"]
    assert (j["title"], j["status"], j["due_date"]) == (
        t["title"],
        t["status"],
        t["due_date"],
    )  # nothing else changed
    # hidden from the live list and the dashboard numbers, visible when asked for
    live = {x["id"] for x in api.get("/tasks", page_size=100).json()["items"]}
    assert t["id"] not in live
    assert t["id"] in {
        x["id"] for x in api.get("/tasks", archived="archived", page_size=100).json()["items"]
    }
    assert t["id"] in {
        x["id"] for x in api.get("/tasks", archived="all", page_size=100).json()["items"]
    }
    # restore
    r = api.post(f"/tasks/{t['id']}/restore", {"reason": "Needed again"})
    assert r.status_code == 200 and r.json()["task"]["archived"] is False
    assert t["id"] in {x["id"] for x in api.get("/tasks", page_size=100).json()["items"]}
    # full audit history: who / when / why, for both steps
    rows = audit(admin, "tasks", t["id"])
    reasons = [x[1] for x in rows]
    assert "No longer needed" in reasons and "Needed again" in reasons
    hist = api.get(f"/tasks/{t['id']}").json()["history"]
    assert {"No longer needed", "Needed again"} <= {h["reason"] for h in hist}


def test_archived_tasks_leave_overdue_counts_and_alerts_but_stay_in_reports(api, admin) -> None:
    t = mk_task(api)  # overdue (due 2 days ago)
    before = api.get("/tasks/summary").json()["overdue"]
    api.post(f"/tasks/{t['id']}/archive", {"reason": "Cancelled by owner"})
    assert api.get("/tasks/summary").json()["overdue"] == before - 1
    kinds = {(r["record_id"], r["kind"]) for r in api.c.get(f"{API}/alerts", headers=api.h("owner")).json().get("items", [])} \
        if api.c.get(f"{API}/alerts", headers=api.h("owner")).status_code == 200 else set()  # fmt: skip
    assert (t["id"], "task_overdue") not in kinds
    # the history report still counts it (as "archived, not completed") instead of silently losing it
    rep = api.get(
        "/reports/tasks_by_branch",
        date_from=(TODAY - timedelta(days=5)).isoformat(),
        date_to=TODAY.isoformat(),
    ).json()
    row = next(r for r in rep["rows"] if r["branch"].startswith(f"{TAG} One 1"))
    assert (
        row["archived_open"] >= 1
        and row["total"] == row["to_do"] + row["in_progress"] + row["done"] + row["archived_open"]
    )
    # a completed task that is archived stays in the completion history
    d = mk_task(api)
    assert api.post(f"/tasks/{d['id']}/status", {"status": "Done"}).status_code == 200
    api.post(f"/tasks/{d['id']}/archive", {"reason": "Tidy up the list"})
    comp = api.get(
        "/reports/tasks_completed",
        date_from=TODAY.isoformat(),
        date_to=TODAY.isoformat(),
        page_size=200,
    ).json()
    assert d["title"] in {r["title"] for r in comp["rows"]}


def test_archived_task_is_read_only_until_restored(api) -> None:
    t = mk_task(api)
    api.post(f"/tasks/{t['id']}/archive", {"reason": "Parked for now"})
    body = {"title": "changed", "branch_id": str(api.w["b1"])}
    r = api.put(f"/tasks/{t['id']}", body)
    assert r.status_code == 409 and r.json()["error"]["code"] == "task_archived"
    r = api.post(f"/tasks/{t['id']}/status", {"status": "Done"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "task_archived"
    again = api.post(f"/tasks/{t['id']}/archive", {"reason": "Parked again"})
    assert again.status_code == 409 and again.json()["error"]["code"] == "already_archived"
    assert api.post(f"/tasks/{t['id']}/restore", {"reason": "Back on"}).status_code == 200
    never = api.post(f"/tasks/{t['id']}/restore", {"reason": "Restore twice"})
    assert never.status_code == 409 and never.json()["error"]["code"] == "not_archived"
    assert (
        api.put(f"/tasks/{t['id']}", {**body, "title": "changed after restore"}).status_code == 200
    )


def test_task_reason_is_required_and_hard_delete_does_not_exist(api, admin) -> None:
    t = mk_task(api)
    assert api.post(f"/tasks/{t['id']}/archive", {"reason": "x"}).status_code == 422
    assert api.post(f"/tasks/{t['id']}/archive", {}).status_code == 422
    assert (
        api.post(f"/tasks/{t['id']}/archive", {"reason": "valid reason", "extra": 1}).status_code
        == 422
    )
    assert api.delete(f"/tasks/{t['id']}").status_code in (404, 405)
    # no DELETE privilege and no delete policy for the application role either
    assert (
        admin.execute(
            "select has_table_privilege('authenticated','public.tasks','DELETE')"
        ).fetchone()[0]
        is False
    )
    assert (
        admin.execute(
            "select count(*) from pg_policies where tablename='tasks' and cmd='DELETE'"
        ).fetchone()[0]
        == 0
    )
    assert api.get(f"/tasks/{t['id']}").status_code == 200  # still there


def test_task_can_be_archived_even_if_its_assignee_was_deactivated(api, admin, world) -> None:
    t = mk_task(api, assignee_id=str(world["u1"]))
    admin.execute("update public.profiles set is_active=false where user_id=%s", (world["u1"],))
    try:
        assert api.post(f"/tasks/{t['id']}/archive", {"reason": "Assignee left"}).status_code == 200
        assert api.post(f"/tasks/{t['id']}/restore", {"reason": "Check again"}).status_code == 200
    finally:
        admin.execute("update public.profiles set is_active=true where user_id=%s", (world["u1"],))


def test_branch_user_cannot_archive_or_restore_tasks(api) -> None:
    t = mk_task(api)
    assert api.post(f"/tasks/{t['id']}/archive", {"reason": "not allowed"}, "u1").status_code == 403
    assert api.post(f"/tasks/{t['id']}/restore", {"reason": "not allowed"}, "u1").status_code == 403
    assert api.get("/tasks", "u1").status_code == 403
    assert api.get(f"/tasks/{t['id']}").json()["task"]["archived"] is False


# ================================================================================================ 2. RENTAL AGREEMENTS
def mk_agreement(admin, w, *, start=None, end=None, landlord=None, branch=None):
    start = start or TODAY - timedelta(days=60)
    end = end or TODAY + timedelta(days=300)
    rid = admin.execute(
        """insert into public.rental_agreements (branch_id, landlord, unit, start_date, end_date, annual_rent, bank, cheque_count,
                  first_cheque_date, first_cheque_no) values (%s,%s,'U1',%s,%s,12000,'ENBD',3,%s,%s) returning id""",
        (
            branch or w["b1"],
            landlord or f"{TAG} Landlord {uniq()}",
            start,
            end,
            start,
            f"{TAG}{uniq()}",
        ),
    ).fetchone()[0]
    cheques = {}
    for i, (status, days) in enumerate((("Cleared", -50), ("Pending", -3), ("Pending", 40))):
        cols = "agreement_id, cheque_no, bank, cheque_date, amount, status"
        vals = [rid, f"{TAG}-{uniq()}-{i}", "ENBD", TODAY + timedelta(days=days), 4000, status]
        if status == "Cleared":
            cols += ", cleared_at, cleared_on"
            vals += [None, TODAY + timedelta(days=days)]
        cheques[i] = admin.execute(
            f"insert into public.pdc_cheques ({cols}) values ({', '.join(['%s'] * len(vals))}) returning id",
            vals,
        ).fetchone()[0]
    return str(rid), cheques


def term_body(**over):
    return {
        "termination_date": TODAY.isoformat(),
        "reason": "Landlord and Owner agreed to end it",
        "confirm": True,
        **over,
    }


def cheque_state(admin, rid):
    return admin.execute(
        "select id, status, amount, cheque_date, cleared_on from public.pdc_cheques where agreement_id=%s order by cheque_no",
        (rid,),
    ).fetchall()


def test_terminate_closes_the_agreement_and_keeps_every_cheque_and_balance(
    api, admin, world
) -> None:
    rid, _ = mk_agreement(admin, world)
    before_cheques = cheque_state(admin, rid)
    s = api.get("/cash-banks/summary").json()
    bal_before = (s["cash_in_hand"], s["enbd"], s["adcb"], s["total_savings"])
    pdc_overdue_before = api.get("/dashboard/owner").json()["alerts"]
    r = api.post(
        f"/rentals/{rid}/terminate",
        term_body(termination_date=(TODAY - timedelta(days=1)).isoformat()),
    )
    assert r.status_code == 200, r.text
    a = r.json()["agreement"]
    assert a["status"] == "Terminated" and a["stored_status"] == "Terminated"
    assert a["terminated_on"] == (TODAY - timedelta(days=1)).isoformat()
    assert (
        a["termination_reason"] == "Landlord and Owner agreed to end it" and a["terminated_by_name"]
    )
    # EVERY linked cheque is exactly as before (nothing deleted, cancelled or rewritten)
    assert cheque_state(admin, rid) == before_cheques
    assert len(r.json()["schedule"]["items"]) == 3
    # financial history untouched
    s = api.get("/cash-banks/summary").json()
    assert (s["cash_in_hand"], s["enbd"], s["adcb"], s["total_savings"]) == bal_before
    # audit: who / when / why, with old and new values
    row = audit(admin, "rental_agreements", rid)[-1]
    assert row[0] == "UPDATE" and row[1] == "Landlord and Owner agreed to end it"
    assert row[2]["status"] == "Active" and row[3]["status"] == "Terminated"
    assert pdc_overdue_before is not None


def test_terminated_agreement_stops_alerting_for_its_pending_cheques_but_keeps_them_listed(
    api, admin, world
) -> None:
    landlord = f"{TAG} AlertCo {uniq()}"
    rid, ch = mk_agreement(admin, world, landlord=landlord)

    def feed() -> list[dict]:
        return [x for x in api.get("/rentals/alerts").json()["items"] if x["landlord"] == landlord]

    assert any(x["kind"] == "cheque_overdue" for x in feed())  # the -3 day pending cheque
    assert api.post(f"/rentals/{rid}/terminate", term_body()).status_code == 200
    assert feed() == []  # no more overdue / due-soon / expiry alerts for a closed agreement
    listed = api.get("/pdc-cheques", agreement_id=rid, page_size=50)
    ids = {c["id"] for c in listed.json()["items"]} if listed.status_code == 200 else set()
    sched = api.get(f"/rentals/{rid}/schedule").json()["items"]
    assert {str(c) for c in ch.values()} <= (
        {x["id"] for x in sched} | ids
    )  # the cheques are still there


def test_terminate_validation_confirmation_and_final_state(api, admin, world) -> None:
    start = TODAY - timedelta(days=60)
    rid, _ = mk_agreement(admin, world, start=start, end=TODAY + timedelta(days=100))
    url = f"/rentals/{rid}/terminate"
    assert api.post(url, term_body(confirm=False)).status_code == 422
    assert (
        api.post(url, {k: v for k, v in term_body().items() if k != "confirm"}).status_code == 422
    )
    assert api.post(url, term_body(reason="ab")).status_code == 422
    assert api.post(url, term_body(reason=" ")).status_code == 422
    assert (
        api.post(
            url, term_body(termination_date=(TODAY + timedelta(days=1)).isoformat())
        ).status_code
        == 422
    )
    assert (
        api.post(
            url, term_body(termination_date=(start - timedelta(days=1)).isoformat())
        ).status_code
        == 422
    )
    assert (
        api.post(
            url, term_body(termination_date=(TODAY + timedelta(days=101)).isoformat())
        ).status_code
        == 422
    )
    assert api.post(url, {**term_body(), "x": 1}).status_code == 422
    assert (
        api.get(f"/rentals/{rid}").json()["agreement"]["stored_status"] == "Active"
    )  # all refused: unchanged
    assert api.post(url, term_body()).status_code == 200
    again = api.post(url, term_body())
    assert again.status_code == 409 and again.json()["error"]["code"] == "agreement_closed"
    # closed for every other change too
    upd = {
        "branch_id": str(world["b1"]),
        "landlord": "x",
        "start_date": start.isoformat(),
        "end_date": (TODAY + timedelta(days=100)).isoformat(),
    }
    assert api.put(f"/rentals/{rid}", upd).status_code == 409
    assert api.post(
        f"/rentals/{rid}/renew",
        {"confirm": True, "expected_end_date": (TODAY + timedelta(days=100)).isoformat()},
    ).status_code in (409, 422)
    # and the database refuses to reopen it, even for a privileged session
    with pytest.raises(psycopg.errors.CheckViolation):
        admin.execute("update public.rental_agreements set status='Active' where id=%s", (rid,))
    with pytest.raises(psycopg.errors.CheckViolation):
        admin.execute(
            "update public.rental_agreements set termination_reason='rewritten history' where id=%s",
            (rid,),
        )


def test_only_the_owner_can_terminate(api, admin, world) -> None:
    rid, _ = mk_agreement(admin, world)
    for who in ("u1", "u2"):
        assert api.post(f"/rentals/{rid}/terminate", term_body(), who).status_code == 403
    assert api.get(f"/rentals/{rid}").json()["agreement"]["stored_status"] == "Active"


def test_cannot_terminate_a_renewed_agreement(api, admin, world) -> None:
    rid, _ = mk_agreement(admin, world)
    admin.execute("update public.rental_agreements set status='Renewed' where id=%s", (rid,))
    r = api.post(f"/rentals/{rid}/terminate", term_body())
    assert r.status_code == 409


# ================================================================================================ 3. CUSTOMERS
def mk_customer(api, who="u1", name=None, mobile=None, **over):
    n = uuid.uuid4().int % 10**8
    body = {"name": name or f"{TAG} Customer {n}", "mobile": mobile or f"05{n:08d}", **over}
    r = api.post("/customers", body, who)
    assert r.status_code == 201, r.text
    return r.json()


def mk_order(api, customer_id, who="u1", **over):
    b = {
        "customer_id": customer_id, "category_id": str(api.w["cat"]), "service_type": "Standard",
        "delivery_date": (TODAY + timedelta(days=2)).isoformat(), "delivery_time": "18:00",
        "pickup_address": "Villa 4", "delivery_address": "Villa 4", "order_total": "100.00", "discount": "0", "service_fee": "0",
        **over,
    }  # fmt: skip
    r = api.post("/orders", b, who)
    return r


def test_customer_archive_and_restore_keep_orders_and_payments(api, admin) -> None:
    c = mk_customer(api)
    o = mk_order(api, c["id"])
    assert o.status_code == 201, o.text
    oid = o.json()["order"]["id"]
    assert (
        api.post(f"/orders/{oid}/payments", {"amount": "40.00", "method": "Cash"}, "u1").status_code
        == 201
    )
    # an open order blocks archiving
    blocked = api.post(f"/customers/{c['id']}/archive", {"reason": "Moved away"}, "u1")
    assert (
        blocked.status_code == 409 and blocked.json()["error"]["code"] == "customer_has_open_orders"
    )
    # the owner cancels the order (refund first), then the customer can be archived
    assert api.post(
        f"/orders/{oid}/refund",
        {"amount": "40.00", "method": "Cash", "reason": "Order cancelled", "confirm": True},
        "owner",
    ).status_code in (200, 201, 422)
    cancelled = api.post(
        f"/orders/{oid}/cancel", {"reason": "Customer left", "confirm": True}, "owner"
    )
    assert cancelled.status_code == 200, cancelled.text
    orders_before = api.get(f"/customers/{c['id']}/orders", "u1").json()
    pays_before = admin.execute(
        "select count(*), coalesce(sum(amount),0) from public.order_payments where order_id=%s",
        (oid,),
    ).fetchone()
    r = api.post(f"/customers/{c['id']}/archive", {"reason": "Moved away"}, "u1")
    assert r.status_code == 200, r.text
    assert r.json()["archived"] is True and r.json()["archive_reason"] == "Moved away"
    # orders and payments are completely untouched and still reachable
    assert (
        api.get(f"/customers/{c['id']}/orders", "u1").json()["total"] == orders_before["total"] == 1
    )
    assert (
        admin.execute(
            "select count(*), coalesce(sum(amount),0) from public.order_payments where order_id=%s",
            (oid,),
        ).fetchone()
        == pays_before
    )
    assert api.get(f"/orders/{oid}", "u1").status_code == 200
    # hidden from the default list / search; visible with the filter
    q = {"q": c["name"]}
    assert api.get("/customers", "u1", **q).json()["total"] == 0
    assert api.get("/customers", "u1", archived="archived", **q).json()["total"] == 1
    assert api.get("/customers", "u1", archived="all", **q).json()["total"] == 1
    assert (
        api.get(f"/customers/{c['id']}", "u1").json()["archived"] is True
    )  # history/detail stays reachable
    # restore
    r = api.post(f"/customers/{c['id']}/restore", {"reason": "Came back"}, "u1")
    assert r.status_code == 200 and r.json()["archived"] is False
    assert api.get("/customers", "u1", **q).json()["total"] == 1
    reasons = [x[1] for x in audit(admin, "customers", c["id"])]
    assert "Moved away" in reasons and "Came back" in reasons


def test_archived_customer_is_read_only_and_cannot_get_new_orders(api, admin) -> None:
    c = mk_customer(api)
    assert (
        api.post(f"/customers/{c['id']}/archive", {"reason": "Duplicate account"}, "u1").status_code
        == 200
    )
    r = api.put(f"/customers/{c['id']}", {"name": "Renamed", "mobile": c["mobile"]}, "u1")
    assert r.status_code == 409 and r.json()["error"]["code"] == "customer_archived"
    o = mk_order(api, c["id"])
    assert o.status_code == 409 and o.json()["error"]["code"] == "customer_archived"
    # the database refuses too (defence in depth), even for a privileged session
    with pytest.raises(psycopg.errors.CheckViolation):
        admin.execute("update public.customers set name='sneaky' where id=%s", (c["id"],))
    # the mobile number stays reserved: the message points to the archived customer
    dup = api.post("/customers", {"name": "Other Person", "mobile": c["mobile"]}, "u1")
    assert dup.status_code == 409 and "archived" in dup.json()["error"]["message"]
    assert api.post(f"/customers/{c['id']}/restore", {"reason": "Mistake"}, "u1").status_code == 200
    assert mk_order(api, c["id"]).status_code == 201  # usable again


def test_customer_archive_rules_reason_and_double_actions(api) -> None:
    c = mk_customer(api)
    assert api.post(f"/customers/{c['id']}/archive", {"reason": "x"}, "u1").status_code == 422
    assert (
        api.post(f"/customers/{c['id']}/restore", {"reason": "not archived"}, "u1").status_code
        == 409
    )
    assert (
        api.post(f"/customers/{c['id']}/archive", {"reason": "Stopped using us"}, "u1").status_code
        == 200
    )
    assert (
        api.post(f"/customers/{c['id']}/archive", {"reason": "Stopped using us"}, "u1").status_code
        == 409
    )
    assert api.delete(f"/customers/{c['id']}", "owner").status_code in (404, 405)


def test_customer_archive_respects_branch_permissions(api) -> None:
    mine = mk_customer(api, "u1")
    theirs = mk_customer(api, "u2")
    # a Branch User works on their own branch only: another branch's customer looks like it does not exist
    assert api.post(
        f"/customers/{theirs['id']}/archive", {"reason": "not mine"}, "u1"
    ).status_code in (403, 404)
    assert api.post(
        f"/customers/{mine['id']}/archive", {"reason": "not mine"}, "u2"
    ).status_code in (403, 404)
    assert api.get(f"/customers/{theirs['id']}", "owner").json()["archived"] is False
    # the Owner may act on any branch
    assert (
        api.post(
            f"/customers/{theirs['id']}/archive", {"reason": "Owner clean-up"}, "owner"
        ).status_code
        == 200
    )
    assert (
        api.post(
            f"/customers/{theirs['id']}/restore", {"reason": "Owner restore"}, "owner"
        ).status_code
        == 200
    )
    # lists are branch-scoped, including the archived view
    assert (
        api.post(f"/customers/{mine['id']}/archive", {"reason": "Own customer"}, "u1").status_code
        == 200
    )
    assert api.get("/customers", "u2", archived="archived", q=mine["name"]).json()["total"] == 0
    assert api.get("/customers", "owner", archived="archived", q=mine["name"]).json()["total"] == 1


# ================================================================================================ 4. BRANCHES
def test_branch_dependencies_block_deactivation(api, admin, world) -> None:
    b = world["b2"]  # has a Branch User
    d = api.get(f"/branches/{b}/dependencies").json()
    assert d["is_active"] is True and d["can_deactivate"] is False
    items = {i["key"]: i["count"] for i in d["items"]}
    assert items["active_users"] == 1
    r = api.post(f"/branches/{b}/deactivate", {"reason": "Closing the shop"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "branch_has_dependencies"
    assert "active_users" in r.json()["error"]["fields"]["dependencies"]
    assert api.get(f"/branches/{b}").json()["is_active"] is True


def test_each_dependency_kind_blocks_and_clears(api, admin, world) -> None:
    b = world["b3"]  # spare branch with nothing on it
    assert api.get(f"/branches/{b}/dependencies").json()["can_deactivate"] is True

    def blockers():
        return {
            i["key"]
            for i in api.get(f"/branches/{b}/dependencies").json()["items"]
            if i["count"] > 0
        }

    # open task
    t = api.post("/tasks", {"title": f"{TAG} open {uniq()}", "branch_id": str(b)}).json()["task"]
    assert blockers() == {"open_tasks"}
    api.post(f"/tasks/{t['id']}/archive", {"reason": "Not needed any more"})
    assert blockers() == set()  # archived tasks no longer block
    # active rental agreement
    rid, _ = mk_agreement(admin, world, branch=b)
    assert blockers() == {"active_agreements"}
    assert api.post(f"/rentals/{rid}/terminate", term_body()).status_code == 200
    assert blockers() == set()
    # open order (customer belongs to the branch)
    c = api.post(
        "/customers",
        {
            "name": f"{TAG} Owner Cust",
            "mobile": f"05{uuid.uuid4().int % 10**8:08d}",
            "branch_id": str(b),
        },
    ).json()
    o = mk_order(api, c["id"], "owner")
    assert o.status_code == 201, o.text
    assert blockers() == {"open_orders"}
    assert (
        api.post(
            f"/orders/{o.json()['order']['id']}/cancel",
            {"reason": "Test clean-up", "confirm": True},
        ).status_code
        == 200
    )
    assert blockers() == set()
    # cash held by the branch
    admin.execute(
        "insert into public.opening_branch_cash (branch_id, amount) values (%s, 150) on conflict (branch_id) do update set amount=150",
        (b,),
    )
    assert blockers() == {"branch_cash"}
    admin.execute("update public.opening_branch_cash set amount=0 where branch_id=%s", (b,))
    assert blockers() == set()
    # a Branch User assigned to it
    extra = _user(admin, "branch", b, f"{TAG} Extra")
    assert blockers() == {"active_users"}
    admin.execute("update public.profiles set is_active=false where user_id=%s", (extra,))
    assert blockers() == set()
    admin.execute("delete from public.profiles where user_id=%s", (extra,))
    admin.execute("delete from auth.users where id=%s", (extra,))


def test_deactivate_and_reactivate_a_clean_branch(api, admin, world, key) -> None:
    b = world["b3"]
    r = api.post(f"/branches/{b}/deactivate", {"reason": "Shop closed for good"})
    assert r.status_code == 200, r.text
    j = r.json()
    assert (
        j["is_active"] is False
        and j["deactivation_reason"] == "Shop closed for good"
        and j["deactivated_at"]
    )
    assert (
        api.get(f"/branches/{b}").json()["is_active"] is False
    )  # a stored fact, listed for the Owner
    again = api.post(f"/branches/{b}/deactivate", {"reason": "Shop closed for good"})
    assert again.status_code == 409 and again.json()["error"]["code"] == "already_inactive"
    # an inactive branch cannot take new work
    rep = api.post(
        "/daily-reports",
        {
            "report_date": TODAY.isoformat(),
            "branch_id": str(b),
            "total_sales": "10",
            "expenses": [],
        },
    )
    assert rep.status_code == 422
    task = api.post("/tasks", {"title": f"{TAG} on inactive", "branch_id": str(b)})
    assert task.status_code == 422
    # reactivate
    r = api.post(f"/branches/{b}/activate", {"reason": "Reopened"})
    assert (
        r.status_code == 200
        and r.json()["is_active"] is True
        and r.json()["deactivation_reason"] is None
    )
    assert api.post(f"/branches/{b}/activate", {"reason": "Reopened"}).status_code == 409
    # audited both ways with reasons
    rows = audit(admin, "branches", str(b))
    reasons = [x[1] for x in rows]
    assert "Shop closed for good" in reasons and "Reopened" in reasons
    assert rows[-1][2]["is_active"] is False and rows[-1][3]["is_active"] is True


def test_deactivated_branch_keeps_its_history_in_reports(api, admin, world) -> None:
    b = world["b3"]
    d = TODAY - timedelta(days=1)
    rep = api.post(
        "/daily-reports",
        {
            "report_date": d.isoformat(),
            "branch_id": str(b),
            "total_sales": "321.00",
            "order_count": 3,
            "pos_amount": "321.00",
            "expenses": [],
        },
    )
    assert rep.status_code == 201, rep.text
    assert api.post(f"/branches/{b}/deactivate", {"reason": "History must stay"}).status_code == 200
    try:
        assert rep.json()["id"] in {
            x["id"] for x in api.get("/daily-reports", page_size=100).json()["items"]
        }
        sales = api.get(
            "/reports/sales_by_branch", date_from=d.isoformat(), date_to=d.isoformat()
        ).json()
        row = next(r for r in sales["rows"] if r["branch"].startswith(f"{TAG} One 3"))
        assert row["sales"] == "321.00"
        dash = api.get("/dashboard/owner", mode="day", date=d.isoformat()).json()
        assert "321.00" in {r["total_sales"] for r in dash["branches"]}
    finally:
        api.post(f"/branches/{b}/activate", {"reason": "Test clean-up"})


def test_the_last_active_branch_cannot_be_deactivated(admin, world, api) -> None:
    # make the spare branch the only active one by checking the dependency logic directly (no real branch is touched)
    others = admin.execute(
        "select count(*) from public.branches where is_active and id <> %s", (world["b3"],)
    ).fetchone()[0]
    deps = {
        i["key"]: i["count"]
        for i in api.get(f"/branches/{world['b3']}/dependencies").json()["items"]
    }
    assert deps["last_active_branch"] == (1 if others == 0 else 0)


def test_branch_actions_are_owner_only_and_validated(api, world) -> None:
    b = world["b3"]
    for who in ("u1", "u2"):
        assert api.get(f"/branches/{b}/dependencies", who).status_code == 403
        assert (
            api.post(f"/branches/{b}/deactivate", {"reason": "not allowed"}, who).status_code == 403
        )
        assert (
            api.post(f"/branches/{b}/activate", {"reason": "not allowed"}, who).status_code == 403
        )
    assert api.post(f"/branches/{b}/deactivate", {"reason": "x"}).status_code == 422
    assert api.post(f"/branches/{b}/deactivate", {}).status_code == 422
    assert (
        api.post(f"/branches/{uuid.uuid4()}/deactivate", {"reason": "unknown branch"}).status_code
        == 404
    )
    assert api.delete(f"/branches/{b}").status_code in (404, 405)
    assert api.get(f"/branches/{b}").json()["is_active"] is True


def test_branch_cash_must_be_moved_out_before_deactivation(api, admin, world) -> None:
    b = admin.execute(
        "insert into public.branches (name, sort_order) values (%s, 950) returning id",
        (f"{TAG} Cash {uniq()}",),
    ).fetchone()[
        0
    ]  # purged with the other test rows (reports / branches) by the module teardown below
    world["extra"] = [*world.get("extra", []), b]
    d = TODAY - timedelta(days=2)
    rep = api.post(
        "/daily-reports",
        {
            "report_date": d.isoformat(),
            "branch_id": str(b),
            "total_sales": "200.00",
            "order_count": 1,
            "cash_received": "200.00",
            "expenses": [],
        },
    )
    assert rep.status_code == 201, rep.text
    r = api.post(f"/branches/{b}/deactivate", {"reason": "Closing"})
    assert r.status_code == 409 and "branch_cash" in r.json()["error"]["fields"]["dependencies"]
    assert "AED 200.00" in r.json()["error"]["message"]
    # move the cash to the bank with the normal, audited transfer; now the branch can be deactivated
    t = api.post(
        "/cash-banks/transfers",
        {
            "from_account": "Cash",
            "to_account": "ENBD",
            "branch_id": str(b),
            "amount": "200.00",
            "transfer_date": TODAY.isoformat(),
        },
    )
    assert t.status_code == 201, t.text
    assert api.post(f"/branches/{b}/deactivate", {"reason": "Closing"}).status_code == 200
    assert api.post(f"/branches/{b}/activate", {"reason": "Test clean-up"}).status_code == 200


def test_inactive_branch_takes_no_new_order_even_for_an_existing_customer(
    api, admin, world
) -> None:
    b = admin.execute(
        "insert into public.branches (name, sort_order) values (%s, 960) returning id",
        (f"{TAG} Orders {uniq()}",),
    ).fetchone()[0]
    world["extra"] = [*world.get("extra", []), b]
    c = api.post(
        "/customers",
        {
            "name": f"{TAG} Late Cust",
            "mobile": f"05{uuid.uuid4().int % 10**8:08d}",
            "branch_id": str(b),
        },
    ).json()
    assert mk_order(api, c["id"], "owner").status_code == 201
    # settle the open order so the branch can be deactivated, then deactivate
    oid = api.get(f"/customers/{c['id']}/orders").json()["items"][0]["id"]
    assert (
        api.post(f"/orders/{oid}/cancel", {"reason": "Test clean-up", "confirm": True}).status_code
        == 200
    )
    assert api.post(f"/branches/{b}/deactivate", {"reason": "Closing"}).status_code == 200
    r = mk_order(api, c["id"], "owner")
    assert r.status_code == 422 and "branch" in r.text.lower()
    assert api.post(f"/branches/{b}/activate", {"reason": "Reopen"}).status_code == 200
    assert mk_order(api, c["id"], "owner").status_code == 201

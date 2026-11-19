"""Owner edits (daily reports, Branch Users), branch-wise PDF export, itemised shop expenses and the Shop Expenses tab.
Real FastAPI app + real PostgreSQL (all migrations, RLS) as the restricted `app_api` role; Supabase Auth is an
httpx MockTransport. Dates live in 2025-12 so they never collide with other modules sharing the database."""

import json
import uuid
from collections.abc import Iterator
from datetime import date

import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient

from app.core.security import TokenVerifier
from app.main import create_app
from app.services import user_provisioning_service as ups
from app.utils.time import dubai_today
from tests.conftest import make_settings
from tests.jwt_helpers import FakeJwks, KeyPair, claims

pytestmark = pytest.mark.integration
API = "/api/v1"
STRONG = "Str0ng-Passw0rd!"


@pytest.fixture(scope="module")
def key() -> KeyPair:
    return KeyPair("kid-owner-edit")


@pytest.fixture(scope="module")
def admin(pg_admin_dsn: str) -> Iterator[psycopg.Connection]:
    with psycopg.connect(pg_admin_dsn, autocommit=True) as c:
        yield c


@pytest.fixture(scope="module")
def bid(admin) -> dict[str, uuid.UUID]:
    return {n: i for i, n in admin.execute("select id, name from public.branches")}


def _user(admin, role: str, branch: uuid.UUID | None, name: str | None = None) -> uuid.UUID:
    uid = uuid.uuid4()
    admin.execute(
        "insert into auth.users (id, email) values (%s,%s)", (uid, f"{uid.hex[:10]}@example.test")
    )
    admin.execute(
        "insert into public.profiles (user_id, full_name, role, branch_id, is_active) values (%s,%s,%s,%s,true)",
        (uid, name or f"{role} edit-test", role, branch),
    )
    return uid


@pytest.fixture(scope="module")
def owner(admin) -> uuid.UUID:
    return _user(admin, "owner", None, "Edit Owner")


@pytest.fixture(scope="module")
def main_user(admin, bid) -> uuid.UUID:
    return _user(admin, "branch", bid["ALSAFA MAIN"], "Main Preparer")


@pytest.fixture(scope="module")
def jernas_user(admin, bid) -> uuid.UUID:
    return _user(admin, "branch", bid["JERNAS"], "Jernas Preparer")


class FakeAuth:
    """In-memory Supabase Auth admin API. Records every call; refuses duplicate emails like the real one."""

    def __init__(self) -> None:
        self.emails: dict[str, str] = {}
        self.calls: list[tuple[str, str, dict]] = []
        self.passwords: dict[str, str] = {}

    def __call__(self, req: httpx.Request) -> httpx.Response:
        uid = req.url.path.rsplit("/", 1)[-1]
        body = json.loads(req.content) if req.content else {}
        self.calls.append((req.method, req.url.path, body))
        if req.method == "GET":
            return httpx.Response(200, json={"id": uid, "email": self.emails.get(uid)})
        if "email" in body and body["email"] in {e for u, e in self.emails.items() if u != uid}:
            return httpx.Response(422, json={"error_code": "email_exists", "msg": "exists"})
        if "email" in body:
            self.emails[uid] = body["email"]
        if "password" in body:
            self.passwords[uid] = body["password"]
        return httpx.Response(200, json={"id": uid})


@pytest.fixture()
def auth(monkeypatch) -> FakeAuth:
    fake = FakeAuth()
    real = ups.build_auth_admin
    monkeypatch.setattr(
        ups,
        "build_auth_admin",
        lambda settings, transport=None: real(settings, httpx.MockTransport(fake)),
    )
    return fake


@pytest.fixture()
def client(app_api_url: str, key: KeyPair) -> Iterator[TestClient]:
    settings = make_settings(
        supabase_url="https://abcd.supabase.test",
        supabase_service_role_key="svc-key-for-tests",
        database_url=app_api_url,
    )
    app = create_app(settings)
    app.state.token_verifier = TokenVerifier(settings, fetcher=FakeJwks(key))
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def h(key: KeyPair, uid: uuid.UUID) -> dict[str, str]:
    return {"Authorization": f"Bearer {key.sign(claims(uid))}"}


def err(r) -> str:
    return r.json()["error"]["code"]


def L(
    category: str,
    description: str,
    amount: str,
    status: str = "Paid",
    account: str | None = "Cash",
    **kw,
):
    """One itemised expense row as the API expects it."""
    d: dict[str, object] = {
        "category": category,
        "description": description,
        "amount": amount,
        "payment_status": status,
    }
    if account:
        d["payment_account"] = account
    d.update(kw)
    return d


def payload(d: date, branch: uuid.UUID | None = None, **over: object) -> dict[str, object]:
    base: dict[str, object] = {
        "report_date": d.isoformat(),
        "total_sales": "1000.00",
        "order_count": 10,
        "cash_received": "600.00",
        "pos_amount": "200.00",
        "bank_transfer": "100.00",
        "link_payment": "50.00",
        "expenses": [L("Food", "Staff lunch", "200.00")],
        "shop_savings": "100.00",  # total shop expenses = 200 itemised + 100 savings = 300
    }
    if branch is not None:
        base["branch_id"] = str(branch)
    base.update(over)
    return base


def edit_body(d: date, branch: uuid.UUID, **over: object) -> dict[str, object]:
    b = payload(d, branch)
    b["reason"] = "Corrected after checking the till slip"
    b.update(over)
    return b


def submit(client, key, uid, d, branch=None, **over) -> dict:
    r = client.post(f"{API}/daily-reports", json=payload(d, branch, **over), headers=h(key, uid))
    assert r.status_code == 201, r.text
    return r.json()


def audit_rows(admin, report_id: str) -> list[tuple]:
    return admin.execute(
        "select action, actor_user_id, reason, old_values, new_values from public.audit_log"
        " where table_name='daily_reports' and record_id=%s order by id",
        (report_id,),
    ).fetchall()


# ===================================================== 2. Daily report editing =====================================
def test_owner_edits_report_recalculates_and_audits_who_when_why(
    client, key, owner, bid, admin
) -> None:
    d = date(2025, 12, 3)
    r = submit(client, key, owner, d, bid["SATWA"])
    assert r["total_received"] == "950.00" and r["difference"] == "50.00"
    e = client.put(
        f"{API}/daily-reports/{r['id']}",
        json=edit_body(
            d, bid["SATWA"], cash_received="650.00", expenses=[L("Food", "Staff lunch", "220.00")]
        ),
        headers=h(key, owner),
    )
    assert e.status_code == 200, e.text
    j = e.json()
    assert (
        j["total_received"] == "1000.00" and j["difference"] == "0.00" and j["check"] == "Matches"
    )
    assert j["shop_expenses_excl_savings"] == "220.00" and j["shop_expenses"] == "320.00"
    assert [x["amount"] for x in j["expenses"]] == ["220.00"]
    rows = audit_rows(admin, r["id"])
    assert [x[0] for x in rows] == ["INSERT", "UPDATE"]
    action, actor, reason, old, new = rows[1]
    assert actor == owner and reason == "Corrected after checking the till slip"
    assert (
        old["cash_received"] == "600.00" or float(old["cash_received"]) == 600.0
    )  # original preserved
    assert float(new["cash_received"]) == 650.0
    hist = client.get(f"{API}/daily-reports/{r['id']}/history", headers=h(key, owner)).json()
    assert hist[0]["actor_name"] == "Edit Owner" and hist[0]["reason"].startswith("Corrected")
    assert {"field": "Cash received", "old": "600.00", "new": "650.00"} in hist[0]["changes"]


def test_edit_can_change_date_and_branch_and_dashboard_follows(
    client, key, owner, bid, admin
) -> None:
    d1, d2 = date(2025, 12, 4), date(2025, 12, 5)
    r = submit(client, key, owner, d1, bid["SATWA"])
    e = client.put(
        f"{API}/daily-reports/{r['id']}",
        json=edit_body(d2, bid["MIRDIF"]),
        headers=h(key, owner),
    )
    assert e.status_code == 200 and e.json()["report_date"] == d2.isoformat()
    assert e.json()["branch_name"] == "MIRDIF"
    day = client.get(
        f"{API}/daily-reports",
        params={"date_from": d1.isoformat(), "date_to": d1.isoformat()},
        headers=h(key, owner),
    ).json()
    assert day["total"] == 0 or all(x["id"] != r["id"] for x in day["items"])


def test_edit_cannot_create_duplicate_branch_date(client, key, owner, bid) -> None:
    d = date(2025, 12, 6)
    a = submit(client, key, owner, d, bid["KARAMA"])
    b = submit(client, key, owner, date(2025, 12, 7), bid["KARAMA"])
    e = client.put(
        f"{API}/daily-reports/{b['id']}", json=edit_body(d, bid["KARAMA"]), headers=h(key, owner)
    )
    assert e.status_code == 409 and err(e) == "duplicate_report"
    assert client.get(f"{API}/daily-reports/{a['id']}", headers=h(key, owner)).status_code == 200
    again = client.get(f"{API}/daily-reports/{b['id']}", headers=h(key, owner)).json()
    assert again["report_date"] == "2025-12-07"  # unchanged after the refused edit


def test_edit_validation_future_date_reason_and_rules(client, key, owner, bid) -> None:
    d = date(2025, 12, 8)
    r = submit(client, key, owner, d, bid["NDL"])
    url = f"{API}/daily-reports/{r['id']}"
    future = dubai_today().replace(year=dubai_today().year + 1)
    assert (
        client.put(url, json=edit_body(future, bid["NDL"]), headers=h(key, owner)).status_code
        == 422
    )
    assert (
        client.put(
            url, json=edit_body(d, bid["NDL"], reason="x"), headers=h(key, owner)
        ).status_code
        == 422
    )
    no_reason = edit_body(d, bid["NDL"])
    del no_reason["reason"]
    assert client.put(url, json=no_reason, headers=h(key, owner)).status_code == 422
    bad_lines = [L("Food", "x", "5", status="Unpaid", account=None)]  # unpaid needs a supplier
    for over in (
        {"total_sales": "0"},
        {"shop_expenses": "300.00"},  # the old total is no longer accepted: it is computed
        {"cash_received": "-1"},
        {"expenses": bad_lines},
    ):
        assert (
            client.put(
                url, json=edit_body(d, bid["NDL"], **over), headers=h(key, owner)
            ).status_code
            == 422
        )
    bad_branch = client.put(url, json=edit_body(d, uuid.uuid4()), headers=h(key, owner))
    assert bad_branch.status_code == 422
    unchanged = client.get(url, headers=h(key, owner)).json()
    assert unchanged["total_sales"] == "1000.00"


def test_branch_user_cannot_edit_delete_or_read_history(
    client, key, main_user, owner, bid, admin
) -> None:
    d = date(2025, 12, 9)
    r = submit(client, key, main_user, d)
    url = f"{API}/daily-reports/{r['id']}"
    mine = edit_body(d, bid["ALSAFA MAIN"], total_sales="9999.00")
    assert client.put(url, json=mine, headers=h(key, main_user)).status_code == 403
    assert client.get(f"{url}/history", headers=h(key, main_user)).status_code == 403
    assert (
        client.post(
            f"{url}/void", json={"reason": "nope nope"}, headers=h(key, main_user)
        ).status_code
        == 403
    )
    assert client.delete(url, headers=h(key, main_user)).status_code in (404, 405)
    assert (
        admin.execute(
            "select total_sales from public.daily_reports where id=%s", (r["id"],)
        ).fetchone()[0]
        == 1000
    )
    # and the database itself refuses a Branch User, even bypassing the API layer
    with pytest.raises(psycopg.errors.Error):
        admin.execute("set role app_api")
        try:
            admin.execute(
                "select public.edit_daily_report(%s,%s,%s,1,1,1,0,0,0,0,'[]'::jsonb,%s)",
                (r["id"], d, bid["ALSAFA MAIN"], "no way"),
            )
        finally:
            admin.execute("reset role")


def test_edit_database_function_requires_owner_claims(admin, bid, app_api_url, main_user) -> None:
    from sqlalchemy import text  # noqa: PLC0415

    from app.core.security import TokenClaims  # noqa: PLC0415
    from app.database.session import Database  # noqa: PLC0415

    r = admin.execute(
        "insert into public.daily_reports (report_date, branch_id, total_sales, created_by) values (%s,%s,500,%s) returning id",
        (date(2025, 12, 10), bid["ALSAFA MAIN"], main_user),
    ).fetchone()[0]
    db = Database(make_settings(database_url=app_api_url))
    gen = db.session_for(TokenClaims(main_user, None, None, None, 9999999999, None))
    s = next(gen)
    with pytest.raises(Exception, match="owner only"):
        s.execute(
            text(
                "select public.edit_daily_report(:i,:d,:b,600,1,600,0,0,0,0,cast('[]' as jsonb),:r)"
            ),
            {"i": r, "d": date(2025, 12, 10), "b": bid["ALSAFA MAIN"], "r": "attempt"},
        )
    s.rollback()
    gen.close()
    db.dispose()
    assert (
        admin.execute("select total_sales from public.daily_reports where id=%s", (r,)).fetchone()[
            0
        ]
        == 500
    )


def test_branch_report_owner_edit_of_voided_is_404(client, key, owner, bid) -> None:
    d = date(2025, 12, 11)
    r = submit(client, key, owner, d, bid["BURDUBAI"])
    assert (
        client.post(
            f"{API}/daily-reports/{r['id']}/void",
            json={"reason": "wrong day"},
            headers=h(key, owner),
        ).status_code
        == 204
    )
    assert (
        client.put(
            f"{API}/daily-reports/{r['id']}",
            json=edit_body(d, bid["BURDUBAI"]),
            headers=h(key, owner),
        ).status_code
        == 404
    )


# ===================================================== 4/5. Itemised shop expenses =================================
def test_itemised_expenses_plus_savings_are_the_shop_total_and_never_double_counted(
    client, key, owner, bid, admin
) -> None:
    d = date(2025, 12, 12)
    lines = [
        L("Repairs & Maintenance", "Gas cylinder", "45.50", remarks="Cash purchase"),
        L("Stationery", "Courier", "20.00", account="ENBD"),
    ]
    r = submit(client, key, owner, d, bid["JERNAS"], expenses=lines, shop_savings="100.00")
    assert r["shop_expenses"] == "165.50"  # 65.50 itemised + 100 savings
    assert r["shop_expenses_excl_savings"] == "65.50"  # savings are NOT an expense
    assert r["expenses_paid"] == "65.50" and r["expenses_outstanding"] == "0.00"
    assert r["cash_paid_expenses"] == "45.50" and r["bank_paid_expenses"] == "20.00"
    assert r["net_cash"] == "454.50"  # 600 cash received - 45.50 paid in cash - 100 savings
    assert [i["description"] for i in r["expenses"]] == ["Gas cylinder", "Courier"]
    dash = client.get(
        f"{API}/dashboard/owner",
        params={"mode": "day", "date": d.isoformat()},
        headers=h(key, owner),
    ).json()
    assert dash["kpis"]["shop_expenses_excl_savings"] == "65.50"
    assert dash["kpis"]["total_shop_expenses"] == "165.50"
    assert dash["kpis"]["total_savings"] == "100.00"
    assert dash["kpis"]["shop_expenses_paid"] == "65.50"
    tab = client.get(
        f"{API}/expenses/shop",
        params={"date_from": d.isoformat(), "date_to": d.isoformat()},
        headers=h(key, owner),
    ).json()
    assert tab["total"] == 2 and tab["totals"]["total"] == "65.50"
    first = tab["items"][0]
    assert (
        first["branch_name"] == "JERNAS"
        and first["recorded_by"] == "Edit Owner"
        and first["expense_date"] == d.isoformat()
    )
    assert (
        admin.execute(
            "select count(*) from public.head_office_expenses where expense_date=%s", (d,)
        ).fetchone()[0]
        == 0
    )


@pytest.mark.parametrize(
    "items",
    [
        [L("Food", "  ", "5")],
        [L("Food", "x", "0")],
        [L("Food", "x", "-3")],
        [L("Food", "x", "1.234")],
        [L("Savings", "x", "5")],  # savings is not a category
        [L("Other (Legacy)", "x", "5")],  # legacy category is never accepted as input
        [L("Food", "x", "5", account=None)],  # paid needs an account
        [L("Food", "x", "5", amount_paid="4")],  # paid must be paid in full
        [L("Food", "x", "5", status="Unpaid", account=None)],  # credit needs a supplier
        [
            L(
                "Food",
                "x",
                "5",
                status="Unpaid",
                account=None,
                amount_paid="1",
                supplier_id=str(uuid.uuid4()),
            )
        ],
        [L("Food", "x", "5", status="Partial", amount_paid="5", supplier_id=str(uuid.uuid4()))],
        [L("Food", "x", "5", status="Partial", amount_paid="0", supplier_id=str(uuid.uuid4()))],
        [L("Food", "x", "5", payment_account="Bitcoin")],
        [L("Food", "d" * 201, "5")],
        [{**L("Food", "x", "5"), "surprise": 1}],
    ],
)
def test_expense_rows_are_validated(client, key, owner, bid, items) -> None:
    r = client.post(
        f"{API}/daily-reports",
        json=payload(date(2025, 12, 13), bid["AL BARSHA"], expenses=items),
        headers=h(key, owner),
    )
    assert r.status_code == 422, r.text


def test_database_freezes_the_legacy_other_expenses_column(admin, bid) -> None:
    with pytest.raises(psycopg.errors.CheckViolation):
        admin.execute(
            "insert into public.daily_reports (report_date, branch_id, total_sales, other_expenses)"
            " values (%s,%s,100,%s::jsonb)",
            (
                date(2025, 12, 14),
                bid["AL BARSHA"],
                json.dumps([{"description": "x", "amount": "11.00"}]),
            ),
        )


def test_owner_edit_replaces_expense_rows_and_keeps_history(client, key, owner, bid, admin) -> None:
    d = date(2025, 12, 15)
    r = submit(client, key, owner, d, bid["AL JADDAF"], expenses=[L("Stationery", "Tape", "10.00")])
    e = client.put(
        f"{API}/daily-reports/{r['id']}",
        json=edit_body(
            d,
            bid["AL JADDAF"],
            expenses=[
                L("Stationery", "Tape", "12.00", remarks="x2"),
                L("Stationery", "Ink", "8.00"),
            ],
        ),
        headers=h(key, owner),
    ).json()
    assert e["shop_expenses_excl_savings"] == "20.00" and len(e["expenses"]) == 2
    hist = client.get(f"{API}/daily-reports/{r['id']}/history", headers=h(key, owner)).json()
    change = next(c for c in hist[0]["changes"] if c["field"] == "Shop expenses")
    assert change["old"] == "110.00" and change["new"] == "120.00"
    # the replaced row is kept as history (voided with the reason), never overwritten
    old = admin.execute(
        "select amount, voided_at is not null, void_reason from public.daily_report_expenses"
        " where report_id=%s order by created_at, line_no",
        (r["id"],),
    ).fetchall()
    assert [(float(a), v) for a, v, _ in old] == [(10.0, True), (12.0, False), (8.0, False)]
    assert old[0][2].startswith("Daily report corrected")


# ===================================================== 5. Shop Expenses tab ========================================
def test_shop_expenses_tab_filters_search_and_permissions(
    client, key, owner, main_user, bid
) -> None:
    d = date(2025, 12, 16)
    submit(
        client,
        key,
        main_user,
        d,
        expenses=[L("Raw Material", "Zebra rope", "7.00", remarks="storage")],
    )
    submit(client, key, owner, d, bid["SATWA"], expenses=[L("Petrol", "Lamp", "9.00")])
    p = {"date_from": d.isoformat(), "date_to": d.isoformat()}
    allr = client.get(f"{API}/expenses/shop", params=p, headers=h(key, owner)).json()
    assert allr["total"] == 2 and allr["totals"]["total"] == "16.00"
    by_branch = client.get(
        f"{API}/expenses/shop", params={**p, "branch_id": str(bid["SATWA"])}, headers=h(key, owner)
    ).json()
    assert [i["description"] for i in by_branch["items"]] == ["Lamp"]
    by_cat = client.get(
        f"{API}/expenses/shop", params={**p, "category": "Raw Material"}, headers=h(key, owner)
    ).json()
    assert [i["description"] for i in by_cat["items"]] == ["Zebra rope"]
    srch = client.get(
        f"{API}/expenses/shop", params={**p, "q": "ZEBRA"}, headers=h(key, owner)
    ).json()
    assert [i["description"] for i in srch["items"]] == ["Zebra rope"] and srch["items"][0][
        "remarks"
    ] == "storage"
    assert (
        client.get(
            f"{API}/expenses/shop", params={**p, "q": "storage"}, headers=h(key, owner)
        ).json()["total"]
        == 1
    )
    assert (
        client.get(
            f"{API}/expenses/shop",
            params={"date_from": "2025-12-20", "date_to": "2025-12-01"},
            headers=h(key, owner),
        ).status_code
        == 422
    )
    assert (
        client.get(
            f"{API}/expenses/shop", params={**p, "category": "Gym"}, headers=h(key, owner)
        ).status_code
        == 422
    )
    assert (
        client.get(f"{API}/expenses/shop", params=p, headers=h(key, main_user)).status_code == 403
    )


def test_voided_report_items_leave_the_shop_expenses_tab(client, key, owner, bid) -> None:
    d = date(2025, 12, 17)
    r = submit(client, key, owner, d, bid["KARAMA"], expenses=[L("Other", "Gone soon", "3.00")])
    p = {"date_from": d.isoformat(), "date_to": d.isoformat()}
    assert client.get(f"{API}/expenses/shop", params=p, headers=h(key, owner)).json()["total"] == 1
    client.post(
        f"{API}/daily-reports/{r['id']}/void",
        json={"reason": "duplicate entry"},
        headers=h(key, owner),
    )
    assert client.get(f"{API}/expenses/shop", params=p, headers=h(key, owner)).json()["total"] == 0


# ===================================================== 3. PDF export ================================================
def pdf_params(d1: date, d2: date | None = None, **kw: object) -> dict[str, str]:
    p = {"date_from": d1.isoformat(), "date_to": (d2 or d1).isoformat()}
    p.update({k: str(v) for k, v in kw.items()})
    return p


def test_owner_pdf_all_branches_individual_and_consolidated(
    client, key, owner, main_user, bid
) -> None:
    d = date(2025, 12, 18)
    submit(client, key, main_user, d)
    submit(
        client,
        key,
        owner,
        d,
        bid["SATWA"],
        expenses=[L("Repairs & Maintenance", "Gas", "5.00")],
    )
    for layout in ("individual", "consolidated"):
        r = client.get(
            f"{API}/daily-reports/export/pdf",
            params=pdf_params(d, layout=layout),
            headers=h(key, owner),
        )
        assert r.status_code == 200, r.text
        assert r.headers["content-type"] == "application/pdf" and r.content.startswith(b"%PDF-")
        assert r.headers["content-disposition"].startswith(
            'attachment; filename="alsafa-daily-reports-all-branches-2025-12-18'
        )
        assert r.headers["cache-control"] == "no-store"
    one = client.get(
        f"{API}/daily-reports/export/pdf",
        params=pdf_params(d, branch_id=bid["SATWA"]),
        headers=h(key, owner),
    )
    assert one.status_code == 200 and "satwa" in one.headers["content-disposition"]


def test_pdf_branch_user_only_own_branch(client, key, main_user, jernas_user, bid) -> None:
    d = date(2025, 12, 19)
    submit(client, key, main_user, d)
    submit(client, key, jernas_user, d)
    own = client.get(
        f"{API}/daily-reports/export/pdf", params=pdf_params(d), headers=h(key, main_user)
    )
    assert own.status_code == 200 and "alsafa-main" in own.headers["content-disposition"]
    same = client.get(
        f"{API}/daily-reports/export/pdf",
        params=pdf_params(d, branch_id=bid["ALSAFA MAIN"]),
        headers=h(key, main_user),
    )
    assert same.status_code == 200
    other = client.get(
        f"{API}/daily-reports/export/pdf",
        params=pdf_params(d, branch_id=bid["JERNAS"]),
        headers=h(key, main_user),
    )
    assert other.status_code == 403 and err(other) == "branch_access_denied"


def _pdf_text(data: bytes) -> str:
    import io  # noqa: PLC0415

    from pypdf import PdfReader  # noqa: PLC0415

    return " ".join(" ".join(pg.extract_text().split()) for pg in PdfReader(io.BytesIO(data)).pages)


def test_pdf_contains_the_required_fields_and_only_permitted_data(
    client, key, main_user, jernas_user, owner, bid
) -> None:
    d = date(2025, 12, 20)
    submit(
        client,
        key,
        main_user,
        d,
        total_sales="1234.50",
        cash_received="1000.00",
        expenses=[L("Stationery", "Zip ties", "4.00", remarks="box")],
    )
    submit(client, key, jernas_user, d, total_sales="9876.50", cash_received="9000.00")
    mine = _pdf_text(
        client.get(
            f"{API}/daily-reports/export/pdf",
            params=pdf_params(d, layout="individual"),
            headers=h(key, main_user),
        ).content
    )
    for needle in (
        "ALSAFA Heritage Laundry",
        "ALSAFA MAIN",
        "20-Dec-2025",
        "AED 1,234.50",
        "Number of Orders",
        "Cash Received",
        "POS Received",
        "Bank Transfers",
        "Payment Link Collections",
        "Total Collections",
        "Shop Expenses",
        "Savings",
        "Not Yet Received",
        "Reconciliation Difference",
        "Submission Date and Time",
        "(Dubai)",
        "Prepared By",
        "Main Preparer",
        "Zip ties",
    ):
        assert needle in mine, needle
    assert "9,876.50" not in mine and "JERNAS" not in mine
    everything = _pdf_text(
        client.get(
            f"{API}/daily-reports/export/pdf",
            params=pdf_params(d, layout="consolidated"),
            headers=h(key, owner),
        ).content
    )
    assert "9,876.50" in everything and "1,234.50" in everything and "GRAND TOTAL" in everything
    assert "Jernas Preparer" in everything and "Main Preparer" in everything


def test_pdf_validation_and_empty_selection(client, key, owner) -> None:
    u = f"{API}/daily-reports/export/pdf"
    assert client.get(u, params={"date_to": "2025-12-01"}, headers=h(key, owner)).status_code == 422
    assert (
        client.get(
            u, params=pdf_params(date(2025, 12, 5), date(2025, 12, 1)), headers=h(key, owner)
        ).status_code
        == 422
    )
    assert (
        client.get(
            u, params=pdf_params(date(2024, 1, 1), date(2025, 12, 1)), headers=h(key, owner)
        ).status_code
        == 422
    )
    assert (
        client.get(
            u, params=pdf_params(date(2025, 12, 5), layout="weird"), headers=h(key, owner)
        ).status_code
        == 422
    )
    empty = client.get(u, params=pdf_params(date(2025, 1, 1)), headers=h(key, owner))
    assert empty.status_code == 200 and empty.content.startswith(b"%PDF-")
    assert client.get(u, params=pdf_params(date(2025, 12, 5))).status_code == 401


# ===================================================== 1. User editing ============================================
def _profile(admin, uid):
    return admin.execute(
        "select full_name, branch_id, is_active, must_change_password from public.profiles where user_id=%s",
        (uid,),
    ).fetchone()


def test_owner_edits_branch_user_details_and_audit_records_it(
    client, key, owner, bid, admin, auth
) -> None:
    uid = _user(admin, "branch", bid["NDL"], "Before Name")
    auth.emails[str(uid)] = "before@example.test"
    got = client.get(f"{API}/users/{uid}", headers=h(key, owner))
    assert got.status_code == 200 and got.json()["email"] == "before@example.test"
    r = client.patch(
        f"{API}/users/{uid}",
        json={
            "full_name": "After Name",
            "email": "After@Example.test",
            "branch_id": str(bid["KARAMA"]),
            "is_active": False,
        },
        headers=h(key, owner),
    )
    assert r.status_code == 200, r.text
    j = r.json()
    assert (
        j["full_name"] == "After Name"
        and j["branch_id"] == str(bid["KARAMA"])
        and j["is_active"] is False
    )
    assert j["email"] == "after@example.test"
    assert _profile(admin, uid)[:3] == ("After Name", bid["KARAMA"], False)
    ev = admin.execute(
        "select actor_user_id, old_values, new_values from public.audit_log where table_name='profiles' and record_id=%s order by id",
        (str(uid),),
    ).fetchall()
    assert any(
        a == owner and o["full_name"] == "Before Name" and n["full_name"] == "After Name"
        for a, o, n in ev
    )
    assert any(
        n.get("email") == "after@example.test" and o.get("email") == "before@example.test"
        for _a, o, n in ev
    )


def test_password_reset_goes_to_auth_only_and_is_never_stored_logged_or_returned(
    client, key, owner, bid, admin, auth, caplog
) -> None:
    caplog.set_level("DEBUG")
    uid = _user(admin, "branch", bid["MIRDIF"], "Pw User")
    auth.emails[str(uid)] = "pw@example.test"
    r = client.patch(
        f"{API}/users/{uid}",
        json={"password": STRONG, "confirm_password": STRONG},
        headers=h(key, owner),
    )
    assert r.status_code == 200, r.text
    assert STRONG not in r.text and "password" not in {
        k for k in r.json() if k != "must_change_password"
    }
    assert auth.passwords[str(uid)] == STRONG
    assert _profile(admin, uid)[3] is True  # must change it on first login
    assert STRONG not in caplog.text
    dump = json.dumps(
        [
            list(map(str, row))
            for row in admin.execute(
                "select * from public.audit_log where table_name='profiles'"
            ).fetchall()
        ]
    ) + json.dumps(
        [list(map(str, row)) for row in admin.execute("select * from public.profiles").fetchall()]
    )
    assert STRONG not in dump
    assert (
        admin.execute(
            "select count(*) from public.audit_log where table_name='profiles' and record_id=%s and new_values->>'event'='Password reset by Owner'",
            (str(uid),),
        ).fetchone()[0]
        == 1
    )


@pytest.mark.parametrize(
    "bad",
    [
        {"password": "short1A", "confirm_password": "short1A"},
        {"password": "alllowercase123", "confirm_password": "alllowercase123"},
        {"password": STRONG, "confirm_password": STRONG + "x"},
        {"password": STRONG},
        {"email": "not-an-email"},
        {"role": "owner"},
        {"full_name": "   "},
    ],
)
def test_user_update_validation_never_echoes_the_password(
    client, key, owner, bid, admin, auth, bad
) -> None:
    uid = _user(admin, "branch", bid["NDL"], "Val User")
    auth.emails[str(uid)] = "val@example.test"
    r = client.patch(f"{API}/users/{uid}", json=bad, headers=h(key, owner))
    assert r.status_code == 422, r.text
    for v in ("short1A", "alllowercase123", STRONG):
        assert v not in r.text
    assert not [c for c in auth.calls if c[0] == "PUT"]


def test_duplicate_email_is_refused_and_nothing_changes(
    client, key, owner, bid, admin, auth
) -> None:
    a, b = _user(admin, "branch", bid["NDL"], "Dup A"), _user(admin, "branch", bid["NDL"], "Dup B")
    auth.emails[str(a)], auth.emails[str(b)] = "a@example.test", "b@example.test"
    r = client.patch(
        f"{API}/users/{b}",
        json={"email": "A@example.test", "full_name": "Should Not Save"},
        headers=h(key, owner),
    )
    assert r.status_code == 409, r.text
    assert _profile(admin, b)[0] == "Dup B" and auth.emails[str(b)] == "b@example.test"


def test_weak_password_check_uses_email_and_invalid_branch_is_refused(
    client, key, owner, bid, admin, auth
) -> None:
    uid = _user(admin, "branch", bid["NDL"], "Branch Check")
    auth.emails[str(uid)] = "checkuser@example.test"
    r = client.patch(
        f"{API}/users/{uid}",
        json={"password": "Checkuser-123456", "confirm_password": "Checkuser-123456"},
        headers=h(key, owner),
    )
    assert r.status_code == 422 and "password" in r.json()["error"]["fields"]
    r = client.patch(
        f"{API}/users/{uid}", json={"branch_id": str(uuid.uuid4())}, headers=h(key, owner)
    )
    assert r.status_code == 422
    admin.execute("update public.branches set is_active=false where name='SALES TEAM'")
    try:
        r = client.patch(
            f"{API}/users/{uid}", json={"branch_id": str(bid["SALES TEAM"])}, headers=h(key, owner)
        )
        assert r.status_code == 422
    finally:
        admin.execute("update public.branches set is_active=true where name='SALES TEAM'")
    assert _profile(admin, uid)[1] == bid["NDL"]


def test_nothing_to_change_and_unknown_user(client, key, owner, bid, admin, auth) -> None:
    uid = _user(admin, "branch", bid["NDL"], "Same Name")
    assert (
        client.patch(
            f"{API}/users/{uid}", json={"full_name": "Same Name"}, headers=h(key, owner)
        ).status_code
        == 422
    )
    assert (
        client.patch(
            f"{API}/users/{uuid.uuid4()}", json={"full_name": "x"}, headers=h(key, owner)
        ).status_code
        == 404
    )


def test_branch_user_cannot_edit_anyone_including_themselves(
    client, key, main_user, jernas_user, bid, admin, auth
) -> None:
    for target in (jernas_user, main_user):
        for body in (
            {"full_name": "Hacked"},
            {"branch_id": str(bid["JERNAS"])},
            {"is_active": False},
            {"password": STRONG, "confirm_password": STRONG},
            {"email": "x@example.test"},
        ):
            r = client.patch(f"{API}/users/{target}", json=body, headers=h(key, main_user))
            assert r.status_code == 403, (body, r.text)
    assert client.get(f"{API}/users/{jernas_user}", headers=h(key, main_user)).status_code == 403
    assert _profile(admin, main_user)[:3] == ("Main Preparer", bid["ALSAFA MAIN"], True)
    assert auth.calls == []  # the Auth admin API was never reached


def test_owner_accounts_cannot_be_edited_through_this_endpoint(
    client, key, owner, admin, auth
) -> None:
    other_owner = _user(admin, "owner", None, "Second Owner")
    r = client.patch(f"{API}/users/{other_owner}", json={"is_active": False}, headers=h(key, owner))
    assert r.status_code == 422
    r = client.patch(f"{API}/users/{owner}", json={"full_name": "Me"}, headers=h(key, owner))
    assert r.status_code == 422
    assert _profile(admin, other_owner)[2] is True


def test_deactivated_user_is_locked_out(client, key, owner, bid, admin, auth) -> None:
    uid = _user(admin, "branch", bid["NDL"], "Lock Out")
    auth.emails[str(uid)] = "lock@example.test"
    assert client.get(f"{API}/auth/profile", headers=h(key, uid)).status_code == 200
    assert (
        client.patch(
            f"{API}/users/{uid}", json={"is_active": False}, headers=h(key, owner)
        ).status_code
        == 200
    )
    assert client.get(f"{API}/daily-reports", headers=h(key, uid)).status_code in (401, 403)
    assert (
        client.patch(
            f"{API}/users/{uid}", json={"is_active": True}, headers=h(key, owner)
        ).status_code
        == 200
    )
    assert client.get(f"{API}/auth/profile", headers=h(key, uid)).status_code == 200

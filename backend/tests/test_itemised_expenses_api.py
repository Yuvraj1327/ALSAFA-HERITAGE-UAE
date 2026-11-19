"""Itemised shop expenses end to end: Daily Reports -> supplier ledger -> Cash & Banks -> Dashboard -> Reports.

Real FastAPI app + real PostgreSQL (all migrations incl. 0021, RLS) as the restricted `app_api` role; Supabase Auth is
mocked at the JWT level. Every test starts from the same hand-computed base:

    opening (as at 2026-01-31): ALSAFA MAIN cash 500, ENBD 1,000, ADCB 1,000 (receipts bank ENBD), POS pending 0,
    one supplier "Fresh Chem LLC" with no opening dues. Reports are dated 2026-02-xx.
"""

import uuid
from collections.abc import Iterator
from datetime import date
from decimal import Decimal as D

import psycopg
import pytest
from fastapi.testclient import TestClient

from app.core import calculations as calc
from app.core.security import TokenVerifier
from app.main import create_app
from tests.conftest import make_settings
from tests.jwt_helpers import FakeJwks, KeyPair, claims

pytestmark = pytest.mark.integration
API = "/api/v1"
D1 = date(2026, 2, 2)
D2 = date(2026, 2, 3)


# ------------------------------------------------------------------------------------------------ fixtures
@pytest.fixture(scope="module")
def key() -> KeyPair:
    return KeyPair("kid-itemised")


@pytest.fixture(scope="module")
def admin(pg_admin_dsn: str) -> Iterator[psycopg.Connection]:
    with psycopg.connect(pg_admin_dsn, autocommit=True) as c:
        yield c


@pytest.fixture(scope="module")
def bid(admin) -> dict[str, uuid.UUID]:
    return {n: i for i, n in admin.execute("select id, name from public.branches")}


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
def owner(admin) -> uuid.UUID:
    return _user(admin, "owner", None, "Itemised Owner")


@pytest.fixture(scope="module")
def main_user(admin, bid) -> uuid.UUID:
    return _user(admin, "branch", bid["ALSAFA MAIN"], "Itemised Main")


@pytest.fixture(scope="module")
def jernas_user(admin, bid) -> uuid.UUID:
    return _user(admin, "branch", bid["JERNAS"], "Itemised Jernas")


WIPE = (
    "supplier_entries", "pos_settlements", "cash_transfers", "head_office_expenses", "daily_report_expenses",
    "daily_reports", "opening_branch_cash", "opening_balances", "suppliers",
)  # fmt: skip


@pytest.fixture(autouse=True)
def base(admin, bid) -> Iterator[uuid.UUID]:
    for t in WIPE:
        admin.execute(f"delete from public.{t}")
    admin.execute(
        "insert into public.opening_balances (as_of_date, receipts_bank, enbd_balance, adcb_balance) "
        "values ('2026-01-31','ENBD',1000,1000)"
    )
    admin.execute(
        "insert into public.opening_branch_cash (branch_id, amount) values (%s, 500)",
        (bid["ALSAFA MAIN"],),
    )
    sid = admin.execute(
        "insert into public.suppliers (name) values ('Fresh Chem LLC') returning id"
    ).fetchone()[0]
    yield sid
    for t in WIPE:
        admin.execute(f"delete from public.{t}")


@pytest.fixture()
def sup(base) -> uuid.UUID:
    return base


@pytest.fixture()
def client(app_api_url: str, key: KeyPair) -> Iterator[TestClient]:
    settings = make_settings(supabase_url="https://abcd.supabase.test", database_url=app_api_url)
    app = create_app(settings)
    app.state.token_verifier = TokenVerifier(settings, fetcher=FakeJwks(key))
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


class Api:
    def __init__(self, client: TestClient, key: KeyPair) -> None:
        self.c, self.k = client, key

    def h(self, uid: uuid.UUID) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.k.sign(claims(uid))}"}

    def get(self, uid, path, **params):
        return self.c.get(f"{API}{path}", params=params, headers=self.h(uid))

    def post(self, uid, path, body):
        return self.c.post(f"{API}{path}", json=body, headers=self.h(uid))

    def put(self, uid, path, body):
        return self.c.put(f"{API}{path}", json=body, headers=self.h(uid))


@pytest.fixture()
def api(client, key) -> Api:
    return Api(client, key)


def L(category, description, amount, status="Paid", account="Cash", **kw):
    d = {
        "category": category,
        "description": description,
        "amount": amount,
        "payment_status": status,
    }
    if account:
        d["payment_account"] = account
    d.update(kw)
    return d


def report_body(d=D1, branch=None, **over):
    b = {
        "report_date": d.isoformat(), "total_sales": "1000.00", "order_count": 10, "cash_received": "600.00",
        "pos_amount": "200.00", "bank_transfer": "100.00", "link_payment": "100.00", "shop_savings": "0.00",
        "expenses": [],
    }  # fmt: skip
    if branch is not None:
        b["branch_id"] = str(branch)
    b.update(over)
    return b


def submit(api, who, **over):
    r = api.post(who, "/daily-reports", report_body(**over))
    assert r.status_code == 201, r.text
    return r.json()


def balances(api, owner):
    j = api.get(owner, "/cash-banks/summary").json()
    return tuple(D(j[k]) for k in ("cash_in_hand", "enbd", "adcb", "pos_pending", "total_savings"))


def supplier(api, owner, sid):
    return api.get(owner, f"/suppliers/{sid}").json()


# ------------------------------------------------------------------------------------------------ 1. rows + totals
def test_all_six_categories_multiple_rows_and_automatic_totals(api, owner, main_user, bid) -> None:
    cats = ["Food", "Raw Material", "Stationery", "Repairs & Maintenance", "Petrol", "Other"]
    lines = [L(c, f"{c} item", "10.00") for c in cats] + [L("Food", "second food row", "5.50")]
    r = submit(api, main_user, expenses=lines, shop_savings="20.00")
    assert [x["category"] for x in r["expenses"]] == cats + ["Food"]
    assert r["shop_expenses_excl_savings"] == "65.50"  # sum of itemised amounts
    assert r["shop_expenses"] == "85.50"  # itemised + savings (computed, never typed)
    assert r["shop_savings"] == "20.00"
    assert [x["outstanding"] for x in r["expenses"]] == ["0.00"] * 7
    # sales reconciliation is untouched by expenses: 600 + 200 + 100 + 100 = 1000
    assert (
        r["total_received"] == "1000.00" and r["difference"] == "0.00" and r["check"] == "Matches"
    )
    assert r["cash_received"] == "600.00"  # gross cash stays the original figure
    assert r["net_cash"] == "514.50"  # 600 - 65.50 paid in cash - 20 savings
    # cash 500 + 600 - 65.50 - 20; savings pool 20
    assert balances(api, owner) == (
        D("1014.50"),
        D("1200.00"),
        D("1000.00"),
        D("200.00"),
        D("20.00"),
    )


def test_savings_is_a_separate_field_never_a_category(api, main_user) -> None:
    r = api.post(main_user, "/daily-reports", report_body(expenses=[L("Savings", "x", "5")]))
    assert r.status_code == 422


# ------------------------------------------------------------------------------------------------ 2. supplier flow
def test_unpaid_partial_paid_supplier_expenses_create_dues_and_payments(
    api, owner, main_user, sup, admin
) -> None:
    lines = [
        L("Raw Material", "paid line", "100.00", "Paid", "ENBD", supplier_id=str(sup)),
        L("Raw Material", "unpaid line", "200.00", "Unpaid", None, supplier_id=str(sup)),
        L(
            "Petrol",
            "partial line",
            "300.00",
            "Partial",
            "Cash",
            amount_paid="120.00",
            supplier_id=str(sup),
        ),
    ]
    r = submit(api, main_user, expenses=lines)
    s = supplier(api, owner, sup)
    assert (s["new_dues"], s["total_paid"], s["outstanding"]) == ("600.00", "220.00", "380.00")
    assert r["expenses_paid"] == "220.00" and r["expenses_outstanding"] == "380.00"
    assert [x["outstanding"] for x in r["expenses"]] == ["0.00", "200.00", "180.00"]
    # unpaid moves nothing; partial moves only what was paid; supplier-linked paid is deducted ONCE
    cash, enbd, adcb, *_ = balances(api, owner)
    assert cash == D("500") + D("600") - D("120")
    assert enbd == D("1000") + D("200") - D("100")
    assert adcb == D("1000")
    # every ledger row is traceable to its expense line, exactly once
    entries = api.get(owner, f"/suppliers/{sup}/entries", page_size=100).json()["items"]
    assert len(entries) == 5 and all(e["source_report_id"] == r["id"] for e in entries)
    assert {e["source_expense_id"] for e in entries} == {x["id"] for x in r["expenses"]}
    per_line = {}
    for e in entries:
        per_line.setdefault(e["source_expense_id"], []).append(e["kind"])
    assert sorted(len(v) for v in per_line.values()) == [1, 2, 2]
    assert (
        max(v.count("due") for v in per_line.values()) == 1
        and max(v.count("payment") for v in per_line.values()) == 1
    )


def test_subsequent_supplier_payment_updates_outstanding_and_accounts(
    api, owner, main_user, sup
) -> None:
    submit(
        api,
        main_user,
        expenses=[L("Raw Material", "credit", "200.00", "Unpaid", None, supplier_id=str(sup))],
    )
    assert supplier(api, owner, sup)["outstanding"] == "200.00"
    assert balances(api, owner)[0] == D("1100")  # nothing left cash yet
    p = api.post(
        owner,
        f"/suppliers/{sup}/payments",
        {"amount": "50.00", "paid_from": "ADCB", "payment_date": D2.isoformat()},
    )
    assert p.status_code == 201, p.text
    s = supplier(api, owner, sup)
    assert (s["new_dues"], s["total_paid"], s["outstanding"]) == ("200.00", "50.00", "150.00")
    assert balances(api, owner)[2] == D("950")
    over = api.post(owner, f"/suppliers/{sup}/payments", {"amount": "151.00", "paid_from": "ADCB"})
    assert over.status_code == 422 and over.json()["error"]["code"] == "exceeds_outstanding"


def test_supplier_outstanding_formula_opening_plus_dues_minus_payments(
    api, owner, main_user, sup, admin
) -> None:
    admin.execute("update public.suppliers set amount_owed = 900 where id = %s", (sup,))
    submit(
        api,
        main_user,
        expenses=[
            L(
                "Other",
                "credit",
                "300.00",
                "Partial",
                "Cash",
                amount_paid="100.00",
                supplier_id=str(sup),
            )
        ],
    )
    s = supplier(api, owner, sup)
    assert (s["opening_outstanding"], s["new_dues"], s["total_paid"], s["outstanding"]) == (
        "900.00",
        "300.00",
        "100.00",
        "1100.00",
    )
    assert calc.supplier_outstanding(D("900"), D("300"), D("100")) == D("1100.00")


def test_generated_supplier_entry_cannot_be_voided_directly(api, owner, main_user, sup) -> None:
    submit(
        api,
        main_user,
        expenses=[L("Food", "credit", "30.00", "Unpaid", None, supplier_id=str(sup))],
    )
    eid = api.get(owner, f"/suppliers/{sup}/entries").json()["items"][0]["id"]
    r = api.post(owner, f"/suppliers/entries/{eid}/void", {"reason": "try to void"})
    assert r.status_code == 422, r.text
    assert supplier(api, owner, sup)["outstanding"] == "30.00"


def test_expense_before_the_opening_date_cannot_be_supplier_linked(api, main_user, sup) -> None:
    r = api.post(
        main_user,
        "/daily-reports",
        report_body(
            d=date(2026, 1, 31),
            expenses=[L("Food", "x", "5", "Unpaid", None, supplier_id=str(sup))],
        ),
    )
    assert r.status_code == 422, r.text


def test_inactive_or_unknown_supplier_is_refused(api, main_user, sup, admin) -> None:
    unknown = api.post(
        main_user,
        "/daily-reports",
        report_body(expenses=[L("Food", "x", "5", "Unpaid", None, supplier_id=str(uuid.uuid4()))]),
    )
    assert unknown.status_code == 422
    admin.execute("update public.suppliers set is_active=false where id=%s", (sup,))
    inactive = api.post(
        main_user,
        "/daily-reports",
        report_body(expenses=[L("Food", "x", "5", "Unpaid", None, supplier_id=str(sup))]),
    )
    assert inactive.status_code == 422 and "inactive" in inactive.text


# ------------------------------------------------------------------------------------------------ 3. cash & banks
def test_non_supplier_paid_expenses_deduct_from_their_account_exactly_once(
    api, owner, main_user
) -> None:
    submit(api, main_user, cash_received="0.00", pos_amount="0.00", bank_transfer="0.00", link_payment="1000.00",
           expenses=[L("Food", "cash", "40.00"), L("Food", "enbd", "25.00", account="ENBD"), L("Petrol", "adcb", "10.00", account="ADCB")])  # fmt: skip
    cash, enbd, adcb, *_ = balances(api, owner)
    assert (cash, enbd, adcb) == (D("460"), D("1000") + D("1000") - D("25"), D("990"))
    tx = api.get(
        owner,
        "/cash-banks/transactions",
        date_from=D1.isoformat(),
        date_to=D1.isoformat(),
        page_size=100,
    ).json()
    shop = [t for t in tx["items"] if t["txn_type"] == "Shop expenses"]
    assert sorted((t["account"], t["amount"]) for t in shop) == [
        ("ADCB", "-10.00"),
        ("Cash", "-40.00"),
        ("ENBD", "-25.00"),
    ]


def test_ledger_has_one_outflow_per_supplier_linked_paid_line(api, owner, main_user, sup) -> None:
    submit(
        api,
        main_user,
        expenses=[
            L("Stationery", "paid to supplier", "80.00", "Paid", "Cash", supplier_id=str(sup))
        ],
    )
    tx = api.get(
        owner,
        "/cash-banks/transactions",
        date_from=D1.isoformat(),
        date_to=D1.isoformat(),
        page_size=100,
    ).json()
    out = [t for t in tx["items"] if D(t["amount"]) < 0]
    assert [(t["txn_type"], t["amount"], t["account"]) for t in out] == [
        ("Supplier payment", "-80.00", "Cash")
    ]
    assert balances(api, owner)[0] == D("1020")


def test_branch_cash_follows_the_branch_that_paid(api, owner, main_user, jernas_user, bid) -> None:
    submit(api, main_user, expenses=[L("Food", "main", "50.00")], cash_received="100.00")
    submit(api, jernas_user, expenses=[L("Food", "jernas", "30.00")], cash_received="100.00")
    rows = {r["branch_name"]: r for r in api.get(owner, "/cash-banks/branches").json()["rows"]}
    assert rows["ALSAFA MAIN"]["closing"] == "550.00"  # 500 + 100 - 50
    assert rows["JERNAS"]["closing"] == "70.00"  # 0 + 100 - 30
    total = api.get(owner, "/cash-banks/branches").json()["total"]
    assert total == "620.00" == api.get(owner, "/cash-banks/summary").json()["cash_in_hand"]


# ------------------------------------------------------------------------------------------------ 4. dashboard + reports
def test_dashboard_and_reports_agree_with_cash_banks(api, owner, main_user, sup) -> None:
    submit(api, main_user, shop_savings="40.00", expenses=[
        L("Food", "cash food", "100.00"),
        L("Raw Material", "credit", "200.00", "Unpaid", None, supplier_id=str(sup)),
        L("Petrol", "partial", "300.00", "Partial", "ENBD", amount_paid="120.00", supplier_id=str(sup)),
    ])  # fmt: skip
    dash = api.get(owner, "/dashboard/owner", mode="day", date=D1.isoformat()).json()
    k = dash["kpis"]
    assert k["total_shop_expenses"] == "640.00"  # 600 itemised + 40 savings
    assert k["shop_expenses_excl_savings"] == "600.00"
    assert k["shop_expenses_paid"] == "220.00"  # 100 + 120
    assert k["shop_expenses_unpaid"] == "200.00"
    assert k["shop_partial_payments"] == "120.00"
    assert k["shop_expenses_outstanding"] == "380.00"
    assert k["supplier_outstanding"] == "380.00"
    assert k["total_savings"] == "40.00"
    # profit counts expenses when incurred (credit included), savings are not an expense
    assert k["sales_minus_expenses"] == "400.00"  # 1000 - 600
    assert k["net_cash_period"] == "460.00"  # 600 cash - 100 cash paid - 40 savings
    cash, enbd, adcb, pos, sav = balances(api, owner)
    assert (
        dash["balances"]["cash_in_hand"] == f"{cash:.2f}" and k["net_cash_in_hand"] == f"{cash:.2f}"
    )
    assert dash["balances"]["enbd"] == f"{enbd:.2f}" and dash["balances"]["adcb"] == f"{adcb:.2f}"
    assert (cash, enbd) == (
        D("500") + D("600") - D("100") - D("40"),
        D("1000") + D("200") - D("120"),
    )
    # Reports & Analytics use the same numbers
    ov = api.get(
        owner, "/reports/overview", date_from=D1.isoformat(), date_to=D1.isoformat()
    ).json()["kpis"]
    assert ov["total_shop_expenses"] == "640.00" and ov["shop_expenses_outstanding"] == "380.00"
    assert ov["sales_minus_expenses"] == "400.00" and ov["supplier_outstanding"] == "380.00"
    # Branch & Date view
    bd = api.get(
        owner, "/branch-date-view", date_from=D1.isoformat(), date_to=D1.isoformat()
    ).json()
    t = bd["totals"]
    assert (t["shop_paid"], t["shop_unpaid"], t["shop_outstanding"], t["net_cash"]) == (
        "220.00",
        "200.00",
        "380.00",
        "460.00",
    )
    assert bd["reports"][0]["expenses"][1]["supplier_name"] == "Fresh Chem LLC"


def test_category_branch_supplier_status_breakdowns(
    api, owner, main_user, jernas_user, sup
) -> None:
    submit(
        api,
        main_user,
        expenses=[
            L("Food", "a", "10.00"),
            L("Petrol", "b", "20.00", "Unpaid", None, supplier_id=str(sup)),
        ],
    )
    submit(api, jernas_user, expenses=[L("Food", "c", "5.00", account="ENBD")])
    s = api.get(
        owner, "/expenses/shop/summary", date_from=D1.isoformat(), date_to=D1.isoformat()
    ).json()
    assert (
        s["totals"]["total"] == "35.00"
        and s["totals"]["paid"] == "15.00"
        and s["totals"]["outstanding"] == "20.00"
    )
    assert {g["label"]: g["total"] for g in s["by_category"]} == {
        "Food": "15.00",
        "Petrol": "20.00",
    }
    assert {g["label"]: g["total"] for g in s["by_branch"]} == {
        "ALSAFA MAIN": "30.00",
        "JERNAS": "5.00",
    }
    assert {g["label"]: g["total"] for g in s["by_supplier"]} == {
        "No supplier": "15.00",
        "Fresh Chem LLC": "20.00",
    }
    assert {g["label"]: g["total"] for g in s["by_status"]} == {"Paid": "15.00", "Unpaid": "20.00"}
    assert [g["label"] for g in s["by_date"]] == [D1.isoformat()]
    only = api.get(
        owner,
        "/expenses/shop",
        date_from=D1.isoformat(),
        date_to=D1.isoformat(),
        supplier_id=str(sup),
        status="Unpaid",
    ).json()
    assert (
        only["total"] == 1
        and only["items"][0]["supplier_name"] == "Fresh Chem LLC"
        and only["totals"]["unpaid_amount"] == "20.00"
    )


NEW_REPORTS = ["shop_expenses_by_category", "shop_expense_items", "shop_expenses_by_supplier", "shop_expense_payments",
               "shop_expenses_by_branch", "shop_expenses_by_date"]  # fmt: skip


def test_new_reports_run_and_exports_match_the_screen(api, owner, main_user, sup) -> None:
    submit(api, main_user, shop_savings="10.00", expenses=[
        L("Food", "cash food", "100.00"), L("Petrol", "credit", "50.00", "Unpaid", None, supplier_id=str(sup))])  # fmt: skip
    p = {"date_from": D1.isoformat(), "date_to": D1.isoformat()}
    cat = api.get(owner, "/reports/shop_expenses_by_category", **p).json()
    assert {r["label"]: (r["total"], r["paid"], r["outstanding"]) for r in cat["rows"]} == {
        "Food": ("100.00", "100.00", "0.00"),
        "Petrol": ("50.00", "0.00", "50.00"),
    }
    assert {s["label"]: s["value"] for s in cat["summary"]}["Outstanding (AED)"] == "50.00"
    # existing header-based reports gain Paid / Outstanding columns and still exclude savings
    by_branch = api.get(owner, "/reports/shop_expenses_by_branch", **p).json()
    assert {s["label"]: s["value"] for s in by_branch["summary"]} == {
        "Total shop expenses (AED)": "160.00", "Shop savings (AED)": "10.00", "Expenses excluding savings (AED)": "150.00",
        "Paid (AED)": "100.00", "Outstanding (AED)": "50.00"}  # fmt: skip
    by_date = api.get(owner, "/reports/shop_expenses_by_date", **p).json()
    assert {s["label"]: s["value"] for s in by_date["summary"]}["Paid (AED)"] == "100.00"
    assert {s["label"]: s["value"] for s in by_date["summary"]}["Outstanding (AED)"] == "50.00"
    items = api.get(owner, "/reports/shop_expense_items", **p).json()
    assert items["total"] == 2 and {r["description"] for r in items["rows"]} == {
        "cash food",
        "credit",
    }
    assert (
        api.get(owner, "/reports/shop_expense_items", category="Petrol", **p).json()["total"] == 1
    )
    assert api.get(owner, "/reports/shop_expense_items", status="Partial", **p).json()["total"] == 0
    assert api.get(owner, "/reports/shop_expense_items", category="Nope", **p).status_code == 422
    for key in NEW_REPORTS:
        screen = api.get(owner, f"/reports/{key}", page_size=200, **p).json()
        csv = api.get(owner, f"/reports/{key}/export", format="csv", **p)
        xlsx = api.get(owner, f"/reports/{key}/export", format="xlsx", **p)
        assert csv.status_code == 200 and xlsx.status_code == 200, key
        assert len(csv.text.strip().splitlines()) >= screen["total"] + 1, (
            key
        )  # header + every screen row
    charts = {c["key"]: c for c in api.get(owner, "/reports/charts", **p).json()["charts"]}
    assert {
        pt["label"]: pt["values"]["Amount"] for pt in charts["shop_expense_categories"]["points"]
    } == {"Food": "100.00", "Petrol": "50.00"}


# ------------------------------------------------------------------------------------------------ 5. edit + void
def _edit(api, owner, rid, branch, **over):
    b = report_body(branch=branch, **over)
    b["reason"] = "Corrected after checking the slips"
    return api.put(owner, f"/daily-reports/{rid}", b)


def test_owner_edit_reconciles_lines_and_supplier_ledger(
    api, owner, main_user, sup, bid, admin
) -> None:
    r = submit(api, main_user, expenses=[L("Raw Material", "orig", "200.00", "Partial", "ENBD", amount_paid="50.00", supplier_id=str(sup)),
                                          L("Food", "cash", "10.00")], shop_savings="5.00")  # fmt: skip
    assert supplier(api, owner, sup)["outstanding"] == "150.00"
    e = _edit(api, owner, r["id"], bid["ALSAFA MAIN"],
              expenses=[L("Raw Material", "now fully paid", "200.00", "Paid", "ENBD", supplier_id=str(sup))], shop_savings="5.00")  # fmt: skip
    assert e.status_code == 200, e.text
    j = e.json()
    assert (
        j["shop_expenses"] == "205.00"
        and j["expenses_outstanding"] == "0.00"
        and len(j["expenses"]) == 1
    )
    s = supplier(api, owner, sup)
    assert (s["new_dues"], s["total_paid"], s["outstanding"]) == ("200.00", "200.00", "0.00")
    # ENBD: 1000 + 200 (bank) + 100 + 100... receipts: bank 100 + link 100 -> +200; minus 200 paid
    cash, enbd, *_ = balances(api, owner)
    assert enbd == D("1000") + D("200") - D("200")
    assert cash == D("500") + D("600") - D("5")  # the 10 cash expense is gone after the edit
    # no duplicates and the old rows are kept as voided history
    live = admin.execute(
        "select count(*) from public.supplier_entries where voided_at is null"
    ).fetchone()[0]
    voided = admin.execute(
        "select count(*) from public.supplier_entries where voided_at is not null"
    ).fetchone()[0]
    assert (live, voided) == (2, 2)
    assert (
        admin.execute(
            "select count(*) from public.daily_report_expenses where report_id=%s and voided_at is null",
            (r["id"],),
        ).fetchone()[0]
        == 1
    )
    reasons = admin.execute(
        "select distinct void_reason from public.supplier_entries where voided_at is not null"
    ).fetchall()
    assert all(x[0].startswith("Daily report corrected") for x in reasons)
    audit = admin.execute(
        "select count(*) from public.audit_log where table_name='daily_reports' and record_id=%s and reason is not null",
        (r["id"],),
    ).fetchone()[0]
    assert audit >= 1


def test_repeating_the_same_edit_does_not_duplicate_ledger_rows(
    api, owner, main_user, sup, bid, admin
) -> None:
    r = submit(
        api,
        main_user,
        expenses=[L("Raw Material", "x", "100.00", "Unpaid", None, supplier_id=str(sup))],
    )
    for _ in range(3):
        assert (
            _edit(
                api,
                owner,
                r["id"],
                bid["ALSAFA MAIN"],
                expenses=[L("Raw Material", "x", "100.00", "Unpaid", None, supplier_id=str(sup))],
            ).status_code
            == 200
        )
    assert supplier(api, owner, sup)["outstanding"] == "100.00"
    assert (
        admin.execute(
            "select count(*) from public.supplier_entries where voided_at is null"
        ).fetchone()[0]
        == 1
    )


def test_void_reverses_expense_lines_supplier_rows_and_balances(
    api, owner, main_user, sup, admin
) -> None:
    r = submit(api, main_user, expenses=[L("Petrol", "p", "300.00", "Partial", "Cash", amount_paid="120.00", supplier_id=str(sup)),
                                          L("Food", "f", "10.00", account="ADCB")])  # fmt: skip
    assert balances(api, owner)[0] == D("1100") - D("120")
    v = api.post(owner, f"/daily-reports/{r['id']}/void", {"reason": "entered twice"})
    assert v.status_code == 204
    assert balances(api, owner) == (D("500"), D("1000"), D("1000"), D("0"), D("0"))
    s = supplier(api, owner, sup)
    assert (s["new_dues"], s["total_paid"], s["outstanding"]) == ("0.00", "0.00", "0.00")
    assert (
        api.get(owner, "/expenses/shop", date_from=D1.isoformat(), date_to=D1.isoformat()).json()[
            "total"
        ]
        == 0
    )
    # the date is free for a fresh report
    assert api.post(owner, "/daily-reports", report_body(branch=r["branch_id"])).status_code == 201


def test_edit_that_would_leave_a_supplier_negative_is_refused_atomically(
    api, owner, main_user, sup, bid
) -> None:
    r = submit(
        api,
        main_user,
        expenses=[L("Raw Material", "x", "200.00", "Unpaid", None, supplier_id=str(sup))],
    )
    assert (
        api.post(
            owner, f"/suppliers/{sup}/payments", {"amount": "150.00", "paid_from": "ENBD"}
        ).status_code
        == 201
    )
    bad = _edit(api, owner, r["id"], bid["ALSAFA MAIN"], expenses=[L("Food", "cash", "10.00")])
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "negative_balance"
    assert supplier(api, owner, sup)["outstanding"] == "50.00"  # untouched: rolled back as a whole
    assert len(api.get(owner, f"/daily-reports/{r['id']}").json()["expenses"]) == 1
    assert (
        api.post(owner, f"/daily-reports/{r['id']}/void", {"reason": "void it"}).status_code == 422
    )


# ------------------------------------------------------------------------------------------------ 6. permissions
def test_branch_user_can_submit_but_not_edit_or_void(api, owner, main_user, bid) -> None:
    r = submit(api, main_user, expenses=[L("Food", "x", "5.00")])
    assert _edit(api, main_user, r["id"], bid["ALSAFA MAIN"]).status_code == 403
    assert (
        api.post(main_user, f"/daily-reports/{r['id']}/void", {"reason": "nope nope"}).status_code
        == 403
    )
    assert api.c.delete(f"{API}/daily-reports/{r['id']}", headers=api.h(main_user)).status_code in (
        404,
        405,
    )


def test_branch_user_cannot_submit_for_another_branch(api, main_user, bid) -> None:
    r = api.post(
        main_user,
        "/daily-reports",
        report_body(branch=bid["JERNAS"], expenses=[L("Food", "x", "5.00")]),
    )
    assert r.status_code == 403, r.text


def test_cross_branch_reads_are_blocked(api, owner, main_user, jernas_user, bid) -> None:
    mine = submit(api, main_user, expenses=[L("Food", "main only", "5.00")])
    other = submit(api, jernas_user, expenses=[L("Food", "jernas only", "7.00")])
    assert api.get(main_user, f"/daily-reports/{other['id']}").status_code in (403, 404)
    listing = api.get(main_user, "/daily-reports").json()["items"]
    assert [i["id"] for i in listing] == [mine["id"]]
    assert (
        api.get(
            main_user,
            "/daily-reports/export/pdf",
            date_from=D1.isoformat(),
            branch_id=str(bid["JERNAS"]),
        ).status_code
        == 403
    )
    # owner-only module: the itemised history, summaries and dashboards are not available to Branch Users
    assert api.get(main_user, "/expenses/shop").status_code == 403
    assert api.get(main_user, "/expenses/shop/summary").status_code == 403
    assert api.get(main_user, "/dashboard/owner").status_code == 403


def test_branch_user_selects_active_suppliers_but_cannot_manage_them(
    api, owner, main_user, sup, admin
) -> None:
    admin.execute("insert into public.suppliers (name, is_active) values ('Dormant Co', false)")
    opts = api.get(main_user, "/daily-reports/supplier-options")
    assert opts.status_code == 200 and opts.json() == [{"id": str(sup), "name": "Fresh Chem LLC"}]
    assert api.get(main_user, "/suppliers").status_code == 403
    assert api.post(main_user, "/suppliers", {"name": "Sneaky"}).status_code == 403
    assert (
        api.put(main_user, f"/suppliers/{sup}", {"name": "Renamed", "is_active": True}).status_code
        == 403
    )
    assert (
        api.post(
            main_user, f"/suppliers/{sup}/payments", {"amount": "1", "paid_from": "Cash"}
        ).status_code
        == 403
    )
    assert api.post(main_user, f"/suppliers/{sup}/dues", {"amount": "1"}).status_code == 403
    assert api.get(owner, "/daily-reports/supplier-options").status_code == 200


def test_branch_user_can_use_a_supplier_in_their_own_report(api, owner, main_user, sup) -> None:
    r = submit(
        api,
        main_user,
        expenses=[L("Raw Material", "credit", "40.00", "Unpaid", None, supplier_id=str(sup))],
    )
    assert (
        r["expenses"][0]["supplier_name"] == "Fresh Chem LLC"
    )  # snapshot readable without reading suppliers
    assert supplier(api, owner, sup)["outstanding"] == "40.00"


def test_database_submit_function_checks_identity_and_branch(
    admin, bid, main_user, app_api_url
) -> None:
    from sqlalchemy import text  # noqa: PLC0415

    from app.core.security import TokenClaims  # noqa: PLC0415
    from app.database.session import Database  # noqa: PLC0415

    db = Database(make_settings(database_url=app_api_url))
    gen = db.session_for(TokenClaims(main_user, None, None, None, 9999999999, None))
    s = next(gen)
    with pytest.raises(Exception, match="own branch"):
        s.execute(
            text("select public.submit_daily_report(:b,:d,100,1,0,0,0,0,0,cast('[]' as jsonb))"),
            {"b": bid["JERNAS"], "d": D1},
        )
    s.rollback()
    gen.close()
    db.dispose()
    assert admin.execute("select count(*) from public.daily_reports").fetchone()[0] == 0


# ------------------------------------------------------------------------------------------------ 7. PDF + calculations
def _pdf_text(data: bytes) -> str:
    import io  # noqa: PLC0415

    from pypdf import PdfReader  # noqa: PLC0415

    return " ".join(" ".join(pg.extract_text().split()) for pg in PdfReader(io.BytesIO(data)).pages)


def test_pdf_lists_the_itemised_rows_with_supplier_status_and_net_cash(
    api, owner, main_user, sup
) -> None:
    submit(api, main_user, expenses=[L("Raw Material", "Soap flakes", "200.00", "Partial", "ENBD", amount_paid="50.00",
                                       supplier_id=str(sup), remarks="invoice 77")])  # fmt: skip
    r = api.get(main_user, "/daily-reports/export/pdf", date_from=D1.isoformat())
    assert r.status_code == 200
    text = _pdf_text(r.content)
    for needle in (
        "Raw Material",
        "Soap flakes",
        "Fresh Chem LLC",
        "invoice 77",
        "Partial",
        "ENBD",
        "Outstanding",
        "Net cash after actual payments",
    ):
        assert needle in text, needle


def test_central_calculations() -> None:
    assert calc.shop_expenses_total(D("65.50"), D("20")) == D("85.50")
    assert calc.expense_outstanding(D("300"), D("120")) == D("180.00")
    assert calc.expense_outstanding(D("300"), D("300")) == D("0.00")
    assert calc.net_cash(D("600"), D("65.50"), D("20")) == D("514.50")
    assert calc.sales_minus_expenses(D("1000"), D("600"), D("0")) == D(
        "400.00"
    )  # savings never subtracted as expenses


# ------------------------------------------------------------------------------------------------ 8. full lifecycle
def _ledger_invariants(api, owner, admin):
    """Cross-checks that must hold after ANY sequence of operations."""
    # (a) one due and at most one payment per expense row; no orphan or duplicate generated rows
    rows = admin.execute(
        "select source_expense_id, kind, count(*) from public.supplier_entries "
        "where source_expense_id is not null and voided_at is null group by 1,2"
    ).fetchall()
    assert all(n == 1 for _, _, n in rows), rows
    # (b) every live generated entry points at a live expense row of a live report
    orphans = admin.execute(
        """select count(*) from public.supplier_entries e
            left join public.daily_report_expenses l on l.id = e.source_expense_id and l.voided_at is null
            left join public.daily_reports r on r.id = l.report_id and r.voided_at is null
           where e.source_expense_id is not null and e.voided_at is null and r.id is null"""
    ).fetchone()[0]
    assert orphans == 0
    # (c) a supplier-linked row is NEVER also a 'Shop expenses' ledger row (no double deduction)
    d_from, d_to = "2026-02-01", "2026-02-28"
    tx = api.get(
        owner, "/cash-banks/transactions", date_from=d_from, date_to=d_to, page_size=100
    ).json()["items"]
    shop_ids = {t["source_id"] for t in tx if t["txn_type"] == "Shop expenses"}
    linked = {
        r[0]
        for r in admin.execute(
            "select id::text from public.daily_report_expenses where supplier_id is not null"
        ).fetchall()
    }
    assert not (shop_ids & linked)
    # (d) balances = opening + sum of the ledger, per account
    opening = {"Cash": D("500"), "ENBD": D("1000"), "ADCB": D("1000"), "POS Pending": D("0")}
    s = {k: opening[k] for k in opening}
    for t in tx:
        s[t["account"]] += D(t["amount"])
    cash, enbd, adcb, pos, _ = balances(api, owner)
    assert (s["Cash"], s["ENBD"], s["ADCB"], s["POS Pending"]) == (cash, enbd, adcb, pos)


def test_full_lifecycle_report_dues_partial_and_final_payments(
    api, owner, main_user, bid, admin
) -> None:
    s1 = admin.execute("select id from public.suppliers where name='Fresh Chem LLC'").fetchone()[0]
    s2 = admin.execute(
        "insert into public.suppliers (name) values ('Gulf Paper') returning id"
    ).fetchone()[0]
    rows = [
        L("Food", "lunch", "100.00"),
        L("Raw Material", "drums", "1000.00", "Unpaid", None, supplier_id=str(s1)),
        L("Petrol", "fuel", "400.00", "Partial", "ENBD", amount_paid="150.00", supplier_id=str(s2)),
        L("Stationery", "paper", "60.00", "Paid", "ADCB", supplier_id=str(s2)),
        L("Repairs & Maintenance", "pump", "250.00", "Paid", "Cash", supplier_id=str(s1)),
        L("Other", "misc", "40.00", "Partial", "Cash", amount_paid="10.00", supplier_id=str(s1)),
    ]
    r = submit(api, main_user, total_sales="3000.00", cash_received="1000.00", pos_amount="500.00",
               bank_transfer="600.00", link_payment="200.00", shop_savings="50.00", expenses=rows)  # fmt: skip
    # --- the report itself
    assert r["shop_expenses_excl_savings"] == "1850.00" and r["shop_expenses"] == "1900.00"
    assert r["difference"] == "700.00" and r["check"] == "Diff"  # sales reconciliation untouched
    assert r["cash_received"] == "1000.00"
    assert (r["expenses_paid"], r["expenses_outstanding"]) == ("570.00", "1280.00")
    assert (r["cash_paid_expenses"], r["bank_paid_expenses"], r["net_cash"]) == (
        "360.00",
        "210.00",
        "590.00",
    )
    # --- day-1 closing balances (hand computed)
    assert balances(api, owner) == (D("1090"), D("1650"), D("940"), D("500"), D("50"))
    assert supplier(api, owner, s1)["outstanding"] == "1030.00"
    assert supplier(api, owner, s2)["outstanding"] == "250.00"
    _ledger_invariants(api, owner, admin)

    # --- day 2: partial payment, then settle both suppliers in full from different accounts
    p = api.post(
        owner,
        f"/suppliers/{s1}/payments",
        {"amount": "500.00", "paid_from": "ENBD", "payment_date": D2.isoformat()},
    )
    assert p.status_code == 201, p.text
    assert supplier(api, owner, s1)["outstanding"] == "530.00"
    m = api.post(
        owner,
        f"/suppliers/{s2}/mark-paid",
        {"paid_from": "Cash", "branch_id": str(bid["ALSAFA MAIN"]), "payment_date": D2.isoformat()},
    )
    assert m.status_code == 201 and m.json()["amount"] == "250.00", m.text
    f = api.post(
        owner,
        f"/suppliers/{s1}/payments",
        {"amount": "530.00", "paid_from": "ADCB", "payment_date": D2.isoformat()},
    )
    assert f.status_code == 201, f.text
    for sid in (s1, s2):
        assert supplier(api, owner, sid)["outstanding"] == "0.00"
    # nothing left to pay, and an extra payment is refused
    assert (
        api.post(
            owner, f"/suppliers/{s1}/payments", {"amount": "1.00", "paid_from": "Cash"}
        ).status_code
        == 422
    )
    assert api.post(owner, f"/suppliers/{s1}/mark-paid", {"paid_from": "Cash"}).status_code == 409
    # --- closing balances after settlement: cash 1090-250, ENBD 1650-500, ADCB 940-530
    assert balances(api, owner) == (D("840"), D("1150"), D("410"), D("500"), D("50"))
    # POS settlement moves pending to the bank
    assert (
        api.post(
            owner,
            "/cash-banks/pos-settlements",
            {"bank": "ADCB", "amount": "500.00", "settlement_date": D2.isoformat()},
        ).status_code
        == 201
    )
    assert balances(api, owner) == (D("840"), D("1150"), D("910"), D("0"), D("50"))
    # --- entries: 5 generated dues + 4 generated payments + 3 manual payments, no duplicates
    live = admin.execute(
        "select kind, count(*) from public.supplier_entries where voided_at is null group by 1 order by 1"
    ).fetchall()
    assert live == [("due", 5), ("payment", 7)]
    _ledger_invariants(api, owner, admin)

    # --- dashboard agrees with Cash & Banks and the supplier module
    k = api.get(
        owner, "/dashboard/owner", mode="range", date_from=D1.isoformat(), date_to=D2.isoformat()
    ).json()
    assert k["balances"]["cash_in_hand"] == "840.00" and k["kpis"]["supplier_outstanding"] == "0.00"
    assert (
        k["kpis"]["total_shop_expenses"] == "1900.00"
        and k["kpis"]["shop_expenses_outstanding"] == "1280.00"
    )  # incurred-time view

    # --- a correction after settlement would make suppliers negative -> refused as a whole, nothing changes
    before = (
        balances(api, owner),
        admin.execute("select count(*) from public.supplier_entries").fetchone()[0],
    )
    bad = _edit(
        api,
        owner,
        r["id"],
        bid["ALSAFA MAIN"],
        total_sales="3000.00",
        expenses=[L("Food", "only lunch", "100.00")],
        shop_savings="50.00",
    )
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "negative_balance"
    assert (
        balances(api, owner),
        admin.execute("select count(*) from public.supplier_entries").fetchone()[0],
    ) == before
    # --- retrying the original submission is a duplicate, not a second set of dues
    again = api.post(owner, "/daily-reports", report_body(branch=bid["ALSAFA MAIN"], expenses=rows))
    assert again.status_code == 409 and again.json()["error"]["code"] == "duplicate_report"
    assert admin.execute("select count(*) from public.supplier_entries").fetchone()[0] == before[1]
    _ledger_invariants(api, owner, admin)


def test_concurrent_double_submit_creates_one_report_and_one_set_of_entries(
    client, key, owner, main_user, sup, admin
) -> None:
    from concurrent.futures import ThreadPoolExecutor  # noqa: PLC0415

    body = report_body(
        expenses=[
            L(
                "Raw Material",
                "x",
                "100.00",
                "Partial",
                "Cash",
                amount_paid="30.00",
                supplier_id=str(sup),
            )
        ]
    )

    def post(_):
        return client.post(
            f"{API}/daily-reports",
            json=body,
            headers={"Authorization": f"Bearer {key.sign(claims(main_user))}"},
        ).status_code

    with ThreadPoolExecutor(max_workers=6) as ex:
        codes = sorted(ex.map(post, range(6)))
    assert codes.count(201) == 1 and codes.count(409) == 5, codes
    assert (
        admin.execute(
            "select count(*) from public.daily_reports where voided_at is null"
        ).fetchone()[0]
        == 1
    )
    assert admin.execute(
        "select kind, count(*) from public.supplier_entries where voided_at is null group by 1 order by 1"
    ).fetchall() == [("due", 1), ("payment", 1)]
    assert balances(Api(client, key), owner)[0] == D(
        "1070"
    )  # 500 + 600 cash received - 30 paid, deducted once

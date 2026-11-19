"""Migration 0021: itemised shop expenses, supplier integration, payment-based cash/bank deductions.

Runs against a REAL PostgreSQL (see conftest). Two groups:
  * TestHistoricalMigration builds a throw-away database at migration 0020, loads representative history, snapshots
    every balance, applies 0021 and proves the balances are identical (and that an unreconcilable report STOPS it).
  * the remaining classes exercise the secure functions on the fully migrated database.
"""
import json
import uuid
from decimal import Decimal as D

import psycopg
import pytest

from conftest import ADMIN_DSN, MIGRATIONS, STUB, _dsn_for, as_user, one, q, World, add_report

M0021 = next(m for m in MIGRATIONS if m.name.startswith("0021"))


# ------------------------------------------------------------------------------------------------ helpers
def today(conn):
    return one(conn, "select app.dubai_today()")


def setup_finance(conn, w):
    """Opening balances (tracking starts 2020-01-02), cash 500 at ALSAFA MAIN, ENBD/ADCB 1000 each, one supplier."""
    conn.execute("insert into public.opening_balances (as_of_date, receipts_bank, enbd_balance, adcb_balance) "
                 "values ('2020-01-01','ENBD',1000,1000)")
    conn.execute("insert into public.opening_branch_cash (branch_id, amount) values (%s, 500)", [w.branch["ALSAFA MAIN"]])
    return one(conn, "insert into public.suppliers (name) values ('Fresh Chem LLC') returning id")


def line(category="Food", amount="10.00", status="Paid", account="Cash", paid=None, supplier=None, desc="item", remarks=None):
    d = {"category": category, "description": desc, "amount": amount, "payment_status": status}
    if account is not None:
        d["payment_account"] = account
    if paid is not None:
        d["amount_paid"] = paid
    if supplier is not None:
        d["supplier_id"] = str(supplier)
    if remarks:
        d["remarks"] = remarks
    return d


def submit(conn, uid, branch, d, lines, sales="1000", cash="0", pos="0", bank="0", link="0", savings="0"):
    with as_user(conn, uid):
        return one(conn, "select public.submit_daily_report(%s,%s,%s,1,%s,%s,%s,%s,%s,%s::jsonb)",
                   [branch, d, sales, cash, pos, bank, link, savings, json.dumps(lines)])


def balances(conn, w):
    with as_user(conn, w.owner):
        r = q(conn, "select cash_now, enbd_now, adcb_now, pos_pending, savings_total from public.app_balances()")[0]
    return tuple(D(x) for x in r)


def supplier_row(conn, w, sid):
    with as_user(conn, w.owner):
        return q(conn, "select new_dues, payments, outstanding from public.v_supplier_outstanding where supplier_id=%s", [sid])[0]


@pytest.fixture()
def fin(conn, w):
    sid = setup_finance(conn, w)
    return {"sup": sid, "main": w.branch["ALSAFA MAIN"], "jernas": w.branch["JERNAS"], "d": today(conn)}


# ------------------------------------------------------------------------------------------------ itemised lines
class TestItemisedLines:
    def test_all_six_categories_and_multiple_rows(self, conn, w, fin):
        cats = ["Food", "Raw Material", "Stationery", "Repairs & Maintenance", "Petrol", "Other"]
        lines = [line(c, "10.00", desc=c) for c in cats] + [line("Food", "5.50", desc="second food row")]
        rid = submit(conn, w.main, fin["main"], fin["d"], lines, cash="400", savings="20")
        assert [r[0] for r in q(conn, "select category from public.daily_report_expenses where report_id=%s order by line_no", [rid])] == cats + ["Food"]
        se, ss = q(conn, "select shop_expenses, shop_savings from public.daily_reports where id=%s", [rid])[0]
        assert (se, ss) == (D("85.50"), D("20.00"))  # 65.50 itemised + 20 savings
        # cash: 500 opening + 400 gross - 65.50 expenses paid - 20 savings
        assert balances(conn, w)[0] == D("814.50")

    def test_savings_is_not_a_category(self, conn, w, fin):
        with pytest.raises(psycopg.errors.CheckViolation):
            submit(conn, w.main, fin["main"], fin["d"], [line("Savings")])

    def test_legacy_category_cannot_be_submitted(self, conn, w, fin):
        with pytest.raises(psycopg.errors.CheckViolation):
            submit(conn, w.main, fin["main"], fin["d"], [line("Other (Legacy)")])

    def test_savings_counted_in_savings_total_not_expenses(self, conn, w, fin):
        submit(conn, w.main, fin["main"], fin["d"], [line("Food", "10")], cash="100", savings="30")
        assert balances(conn, w)[4] == D("30")  # savings_total
        assert one(conn, "select coalesce(sum(amount),0) from public.daily_report_expenses where voided_at is null") == D("10")

    @pytest.mark.parametrize("bad", [
        line(amount="0"), line(amount="-5"), line(amount="abc"),
        line(status="Paid", paid="4.00"),                                     # paid must be in full
        line(status="Unpaid", account=None, paid="1.00", supplier="S"),       # unpaid has zero paid
        line(status="Partial", paid="10.00", supplier="S"),                   # partial must be < total
        line(status="Partial", paid="0", supplier="S"),                       # partial must be > 0
        line(status="Unpaid", account=None),                                  # credit needs a supplier
        line(status="Partial", paid="3.00"),                                  # partial needs a supplier
        line(status="Paid", account=None),                                    # paid needs an account
        line(category="Gym"), line(status="Late"),
        {**line(), "surprise": 1},
        line(account="Bitcoin"),
    ])
    def test_validation(self, conn, w, fin, bad):
        if bad.get("supplier_id") == "S":
            bad = {**bad, "supplier_id": str(fin["sup"])}
        with pytest.raises(psycopg.errors.CheckViolation):
            submit(conn, w.main, fin["main"], fin["d"], [bad])

    def test_paid_amount_cannot_exceed_amount(self, conn, w, fin):
        with pytest.raises(psycopg.errors.CheckViolation):
            submit(conn, w.main, fin["main"], fin["d"], [line(status="Partial", paid="11.00", supplier=fin["sup"])])

    def test_header_must_equal_lines_plus_savings(self, conn, w, fin):
        # A direct insert with expenses but no lines is refused when the deferred constraint is evaluated.
        conn.execute("""insert into public.daily_reports (report_date, branch_id, total_sales, shop_expenses, shop_savings)
                        values (%s,%s,100,50,0)""", [fin["d"], fin["main"]])
        with pytest.raises(psycopg.errors.CheckViolation, match="must equal itemised expenses"):
            conn.execute("set constraints all immediate")


# ------------------------------------------------------------------------------------------------ cash / bank / suppliers
class TestPaymentsAndSuppliers:
    def test_paid_unpaid_partial_with_supplier(self, conn, w, fin):
        s = fin["sup"]
        lines = [line("Raw Material", "100.00", "Paid", "ENBD", supplier=s, desc="paid"),
                 line("Raw Material", "200.00", "Unpaid", None, supplier=s, desc="unpaid"),
                 line("Petrol", "300.00", "Partial", "Cash", paid="120.00", supplier=s, desc="partial")]
        rid = submit(conn, w.main, fin["main"], fin["d"], lines, cash="0")
        dues, pays, out = supplier_row(conn, w, s)
        assert (dues, pays, out) == (D("600"), D("220"), D("380"))
        cash, enbd, adcb, pos, _ = balances(conn, w)
        assert cash == D("500") - D("120")       # only the part actually paid
        assert enbd == D("1000") - D("100")
        assert adcb == D("1000")                 # unpaid moves nothing
        # every supplier row is traceable to its line, exactly one due per line
        rows = q(conn, """select e.kind, e.amount, l.line_no from public.supplier_entries e
                          join public.daily_report_expenses l on l.id = e.source_expense_id
                          where l.report_id=%s order by l.line_no, e.kind""", [rid])
        assert rows == [("due", D("100"), 1), ("payment", D("100"), 1), ("due", D("200"), 2),
                        ("due", D("300"), 3), ("payment", D("120"), 3)]

    def test_no_double_counting_for_supplier_linked_paid(self, conn, w, fin):
        submit(conn, w.main, fin["main"], fin["d"], [line("Stationery", "80", "Paid", "Cash", supplier=fin["sup"])])
        with as_user(conn, w.owner):
            ledger = q(conn, "select source_table, amount, account from public.app_cash_bank_ledger(%s,%s) where amount < 0",
                       [fin["d"], fin["d"]])
        assert ledger == [("supplier_entries", D("-80.00"), "Cash")]
        assert balances(conn, w)[0] == D("420")

    def test_non_supplier_paid_from_banks(self, conn, w, fin):
        submit(conn, w.main, fin["main"], fin["d"], [line("Food", "40", "Paid", "ENBD"), line("Food", "25", "Paid", "ADCB")])
        cash, enbd, adcb, *_ = balances(conn, w)
        assert (cash, enbd, adcb) == (D("500"), D("960"), D("975"))

    def test_gross_cash_received_untouched_by_expenses(self, conn, w, fin):
        rid = submit(conn, w.main, fin["main"], fin["d"], [line("Food", "40", "Paid", "Cash")], sales="500", cash="300", pos="200")
        assert one(conn, "select cash_received from public.daily_reports where id=%s", [rid]) == D("300")
        with as_user(conn, w.owner):
            r = q(conn, "select difference, total_received from public.v_daily_reports_reconciled where id=%s", [rid])[0]
        assert r == (D("0"), D("500"))  # sales reconciliation is untouched by expenses

    def test_subsequent_supplier_payment_updates_outstanding(self, conn, w, fin):
        s = fin["sup"]
        submit(conn, w.main, fin["main"], fin["d"], [line("Raw Material", "200", "Unpaid", None, supplier=s)])
        assert supplier_row(conn, w, s)[2] == D("200")
        with as_user(conn, w.owner):
            conn.execute("""insert into public.supplier_entries (supplier_id, entry_date, kind, amount, paid_from, branch_id)
                            values (%s,%s,'payment',50,'Cash',%s)""", [s, fin["d"], fin["main"]])
        assert supplier_row(conn, w, s)[2] == D("150")
        assert balances(conn, w)[0] == D("450")

    def test_duplicate_due_or_payment_for_a_line_is_impossible(self, conn, w, fin):
        s = fin["sup"]
        rid = submit(conn, w.main, fin["main"], fin["d"], [line("Food", "30", "Partial", "Cash", paid="10", supplier=s)])
        lid = one(conn, "select id from public.daily_report_expenses where report_id=%s", [rid])
        for kind, pf in (("due", None), ("payment", "Cash")):
            with pytest.raises(psycopg.errors.UniqueViolation):
                with conn.transaction():
                    conn.execute("""insert into public.supplier_entries (supplier_id, entry_date, kind, amount, paid_from, source_expense_id)
                                    values (%s,%s,%s,5,%s,%s)""", [s, fin["d"], kind, pf, lid])

    def test_generated_entry_cannot_be_voided_directly(self, conn, w, fin):
        submit(conn, w.main, fin["main"], fin["d"], [line("Food", "30", "Unpaid", None, supplier=fin["sup"])])
        eid = one(conn, "select id from public.supplier_entries where source_expense_id is not null")
        with pytest.raises(psycopg.errors.CheckViolation):
            with as_user(conn, w.owner):
                conn.execute("select public.void_supplier_entry(%s, 'oops')", [eid])

    def test_supplier_expense_before_opening_date_rejected(self, conn, w, fin):
        with pytest.raises(psycopg.errors.CheckViolation):
            submit(conn, w.main, fin["main"], "2019-12-31", [line("Food", "5", "Unpaid", None, supplier=fin["sup"])])

    def test_inactive_supplier_rejected(self, conn, w, fin):
        conn.execute("update public.suppliers set is_active=false where id=%s", [fin["sup"]])
        with pytest.raises(psycopg.errors.CheckViolation):
            submit(conn, w.main, fin["main"], fin["d"], [line("Food", "5", "Unpaid", None, supplier=fin["sup"])])


# ------------------------------------------------------------------------------------------------ edit / void
class TestEditAndVoid:
    def _make(self, conn, w, fin):
        s = fin["sup"]
        return submit(conn, w.main, fin["main"], fin["d"],
                      [line("Raw Material", "200", "Partial", "ENBD", paid="50", supplier=s, desc="orig"),
                       line("Food", "10", "Paid", "Cash", desc="cash item")], cash="100", savings="5")

    def _edit(self, conn, w, fin, rid, lines, savings="0", reason="fix", user=None):
        with as_user(conn, user or w.owner):
            conn.execute("select public.edit_daily_report(%s,%s,%s,1000,1,100,0,0,0,%s,%s::jsonb,%s)",
                         [rid, fin["d"], fin["main"], savings, json.dumps(lines), reason])

    def test_edit_replaces_lines_and_supplier_rows_atomically(self, conn, w, fin):
        rid = self._make(conn, w, fin)
        before = balances(conn, w)
        assert supplier_row(conn, w, fin["sup"]) == (D("200"), D("50"), D("150"))
        self._edit(conn, w, fin, rid, [line("Raw Material", "200", "Paid", "ENBD", supplier=fin["sup"], desc="now paid")], savings="5")
        assert supplier_row(conn, w, fin["sup"]) == (D("200"), D("200"), D("0"))   # old rows voided, new rows live
        assert q(conn, "select count(*) from public.supplier_entries where voided_at is null")[0][0] == 2
        assert q(conn, "select count(*) from public.supplier_entries where voided_at is not null")[0][0] == 2
        assert q(conn, "select count(*) from public.daily_report_expenses where report_id=%s and voided_at is null", [rid])[0][0] == 1
        cash, enbd, *_ = balances(conn, w)
        assert enbd == D("1000") - D("200")
        assert cash == D("500") + D("100") - D("5")                                 # cash item removed from the edit
        assert one(conn, "select shop_expenses from public.daily_reports where id=%s", [rid]) == D("205")
        # audit: the reason is stored on the report update and on the voided lines
        assert one(conn, "select count(*) from public.audit_log where table_name='daily_reports' and record_id=%s and reason='fix'", [str(rid)]) >= 1
        assert before != balances(conn, w)

    def test_edit_to_zero_expenses_restores_balances(self, conn, w, fin):
        rid = self._make(conn, w, fin)
        self._edit(conn, w, fin, rid, [], savings="0")
        assert balances(conn, w) == (D("600"), D("1000"), D("1000"), D("0"), D("0"))
        assert supplier_row(conn, w, fin["sup"]) == (D("0"), D("0"), D("0"))

    def test_void_reverses_everything(self, conn, w, fin):
        rid = self._make(conn, w, fin)
        with as_user(conn, w.owner):
            conn.execute("select public.void_daily_report(%s,'duplicate')", [rid])
        assert balances(conn, w) == (D("500"), D("1000"), D("1000"), D("0"), D("0"))
        assert supplier_row(conn, w, fin["sup"]) == (D("0"), D("0"), D("0"))
        assert q(conn, "select count(*) from public.daily_report_expenses where report_id=%s and voided_at is null", [rid])[0][0] == 0
        # the date is free again and the lines/entries are still there as history
        assert q(conn, "select count(*) from public.daily_report_expenses where report_id=%s", [rid])[0][0] == 2

    def test_edit_that_would_make_supplier_negative_is_refused(self, conn, w, fin):
        rid = self._make(conn, w, fin)   # outstanding 150
        with as_user(conn, w.owner):
            conn.execute("""insert into public.supplier_entries (supplier_id, entry_date, kind, amount, paid_from)
                            values (%s,%s,'payment',150,'ENBD')""", [fin["sup"], fin["d"]])
        with pytest.raises(psycopg.errors.CheckViolation):
            self._edit(conn, w, fin, rid, [line("Food", "10")])      # would void the 200 due, leaving -200
        assert supplier_row(conn, w, fin["sup"]) == (D("200"), D("200"), D("0"))  # unchanged (rolled back)

    def test_branch_user_cannot_edit_or_void(self, conn, w, fin):
        rid = self._make(conn, w, fin)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            self._edit(conn, w, fin, rid, [], user=w.main)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with as_user(conn, w.main):
                conn.execute("select public.void_daily_report(%s,'x')", [rid])

    def test_edit_without_reason_refused(self, conn, w, fin):
        rid = self._make(conn, w, fin)
        with pytest.raises(psycopg.errors.CheckViolation):
            self._edit(conn, w, fin, rid, [], reason=" ")

    def test_duplicate_report_same_branch_day_refused(self, conn, w, fin):
        self._make(conn, w, fin)
        with pytest.raises(psycopg.errors.UniqueViolation):
            submit(conn, w.main, fin["main"], fin["d"], [line()])


# ------------------------------------------------------------------------------------------------ security
class TestSecurity:
    def test_branch_user_cannot_submit_for_another_branch(self, conn, w, fin):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            submit(conn, w.main, fin["jernas"], fin["d"], [line()])

    def test_owner_can_submit_for_any_active_branch(self, conn, w, fin):
        rid = submit(conn, w.owner, fin["jernas"], fin["d"], [line()])
        assert one(conn, "select branch_id from public.daily_reports where id=%s", [rid]) == fin["jernas"]

    def test_anonymous_cannot_submit(self, conn, w, fin):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("set local role anon")
            conn.execute("select public.submit_daily_report(%s,%s,100,1,0,0,0,0,0,'[]'::jsonb)", [fin["main"], fin["d"]])

    def test_branch_user_sees_only_own_branch_lines(self, conn, w, fin):
        submit(conn, w.main, fin["main"], fin["d"], [line(desc="main line")])
        submit(conn, w.jernas, fin["jernas"], fin["d"], [line(desc="jernas line")])
        with as_user(conn, w.main):
            assert [r[0] for r in q(conn, "select description from public.daily_report_expenses")] == ["main line"]
        with as_user(conn, w.owner):
            assert len(q(conn, "select 1 from public.daily_report_expenses")) == 2

    def test_no_direct_writes_to_lines_or_ledger(self, conn, w, fin):
        rid = submit(conn, w.main, fin["main"], fin["d"], [line()])
        for uid in (w.main, w.owner):
            for stmt in ("insert into public.daily_report_expenses (report_id, branch_id, expense_date, line_no, category, description, amount, payment_status, payment_account, amount_paid) "
                         f"select id, branch_id, report_date, 9, 'Food','x',1,'Paid','Cash',1 from public.daily_reports where id='{rid}'",
                         "update public.daily_report_expenses set amount = 1",
                         "delete from public.daily_report_expenses"):
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    with as_user(conn, uid):
                        conn.execute(stmt)

    def test_branch_user_cannot_touch_suppliers_but_can_list_active_names(self, conn, w, fin):
        conn.execute("insert into public.suppliers (name, is_active) values ('Dormant Co', false)")
        with as_user(conn, w.main):
            assert q(conn, "select * from public.suppliers") == []
            names = [r[1] for r in q(conn, "select * from public.report_supplier_options()")]
        assert names == ["Fresh Chem LLC"]                      # active only, id + name only
        with pytest.raises((psycopg.errors.InsufficientPrivilege, psycopg.errors.NoDataFound)):  # refused either way
            with as_user(conn, w.main):
                conn.execute("insert into public.supplier_entries (supplier_id, entry_date, kind, amount) values (%s, current_date, 'due', 1)",
                             [fin["sup"]])

    def test_frozen_legacy_json(self, conn, w, fin):
        rid = submit(conn, w.main, fin["main"], fin["d"], [line()])
        with pytest.raises(psycopg.errors.CheckViolation):
            with conn.transaction():
                conn.execute("""update public.daily_reports set other_expenses='[{"description":"x","amount":"1.00"}]'::jsonb where id=%s""", [rid])
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute("""insert into public.daily_reports (report_date, branch_id, total_sales, other_expenses)
                            values (%s,%s,1,'[{"description":"x","amount":"1.00"}]'::jsonb)""", [fin["d"], fin["jernas"]])

    def test_lines_are_immutable(self, conn, w, fin):
        rid = submit(conn, w.main, fin["main"], fin["d"], [line()])
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute("update public.daily_report_expenses set amount = 99 where report_id=%s", [rid])

    def test_definer_functions_pin_search_path(self, conn):
        names = ("submit_daily_report", "edit_daily_report", "void_daily_report", "report_supplier_options")
        rows = q(conn, "select proname, prosecdef, proconfig from pg_proc where proname = any(%s)", [list(names)])
        assert {r[0] for r in rows} == set(names)
        for name, secdef, cfg in rows:
            assert secdef and cfg and any(c.startswith("search_path=") for c in cfg), name


# ------------------------------------------------------------------------------------------------ historical migration
def _build(upto_exclusive_name):
    """Fresh database with the stub + every migration BEFORE 0021."""
    name = f"alsafa_mig_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(ADMIN_DSN, autocommit=True) as admin:
        admin.execute(f'create database "{name}"')
    c = psycopg.connect(_dsn_for(name), autocommit=True)
    c.execute(STUB.read_text())
    for m in MIGRATIONS:
        if m.name.startswith(upto_exclusive_name):
            break
        c.execute(m.read_text())
    return name, c


def _drop(name, c):
    c.close()
    with psycopg.connect(ADMIN_DSN, autocommit=True) as admin:
        admin.execute(f'drop database if exists "{name}" with (force)')


def _seed_history(c):
    """Representative production-like history at the 0020 schema (direct SQL as the migration role)."""
    br = {r[1]: r[0] for r in c.execute("select id, name from public.branches").fetchall()}
    owner = c.execute("insert into auth.users (email) values ('o@x.test') returning id").fetchone()[0]
    c.execute("insert into public.profiles (user_id, full_name, role) values (%s,'Owner','owner')", [owner])
    c.execute("insert into public.opening_balances (as_of_date, receipts_bank, enbd_balance, adcb_balance, pos_pending, savings_brought_forward) "
              "values ('2026-01-01','ENBD',5000,3000,400,250)")
    for b in ("ALSAFA MAIN", "JERNAS"):
        c.execute("insert into public.opening_branch_cash (branch_id, amount) values (%s, 700)", [br[b]])
    sid = c.execute("insert into public.suppliers (name, amount_owed) values ('Old Supplier', 900) returning id").fetchone()[0]
    rows = [  # date, branch, sales, cash, pos, bank, link, shop_exp, savings, other_expenses
        ("2026-01-05", "ALSAFA MAIN", 1000, 600, 300, 100, 0, 300, 100, '[{"description":"Gas","amount":"50.00","notes":"cylinder"},{"description":"Soap","amount":"20.50"}]'),
        ("2026-01-05", "JERNAS", 800, 500, 300, 0, 0, 120, 0, '[]'),
        ("2026-01-06", "ALSAFA MAIN", 900, 900, 0, 0, 0, 0, 0, '[]'),
        ("2026-01-07", "JERNAS", 700, 400, 0, 200, 100, 80, 80, '[]'),   # all savings
    ]
    for d, b, sales, cash, pos, bank, link, se, ss, oe in rows:
        c.execute("""insert into public.daily_reports (report_date, branch_id, total_sales, order_count, cash_received, pos_amount,
                     bank_transfer, link_payment, shop_expenses, shop_savings, other_expenses)
                     values (%s,%s,%s,1,%s,%s,%s,%s,%s,%s,%s::jsonb)""", [d, br[b], sales, cash, pos, bank, link, se, ss, oe])
    # a voided report with an other-item, to prove voided history is preserved too
    vid = c.execute("""insert into public.daily_reports (report_date, branch_id, total_sales, shop_expenses, other_expenses)
                       values ('2026-01-08', %s, 100, 40, '[{"description":"void me","amount":"40.00"}]'::jsonb) returning id""", [br["NDL"]]).fetchone()[0]
    c.execute("update public.daily_reports set voided_at=now(), void_reason='dup' where id=%s", [vid])
    cat = c.execute("select id from public.expense_categories limit 1").fetchone()[0]
    c.execute("insert into public.head_office_expenses (expense_date, category_id, amount, paid_from) values ('2026-01-06',%s,60,'Cash received')", [cat])
    c.execute("insert into public.head_office_expenses (expense_date, category_id, amount, paid_from, is_saving) values ('2026-01-06',%s,40,'ENBD',true)", [cat])
    c.execute("insert into public.supplier_entries (supplier_id, entry_date, kind, amount) values (%s,'2026-01-06','due',300)", [sid])
    c.execute("insert into public.supplier_entries (supplier_id, entry_date, kind, amount, paid_from, branch_id) values (%s,'2026-01-07','payment',150,'Cash',%s)", [sid, br["ALSAFA MAIN"]])
    c.execute("insert into public.pos_settlements (settlement_date, bank, amount) values ('2026-01-07','ADCB',250)")
    c.execute("insert into public.cash_transfers (transfer_date, from_account, to_account, branch_id, amount) values ('2026-01-07','Cash','ENBD',%s,100)", [br["JERNAS"]])
    return owner


def _snapshot(c, owner):
    c.execute("select set_config('request.jwt.claims', %s, false)", [json.dumps({"sub": str(owner)})])
    bal = c.execute("select cash_now, enbd_now, adcb_now, pos_pending, total_cash_bank, savings_total from public.app_balances('2026-01-31')").fetchone()
    branch = c.execute("select branch_name, opening, incoming, outgoing, closing from public.app_branch_cash('2026-01-31') order by sort_order, branch_name").fetchall()
    sup = c.execute("select supplier_id, opening_outstanding, new_dues, payments, outstanding from public.v_supplier_outstanding order by supplier_id").fetchall()
    ledger = c.execute("select account, sum(amount), count(*) filter (where amount <> 0) from public.app_cash_bank_ledger('2026-01-02','2026-01-31') group by account order by account").fetchall()
    others = c.execute("""select txn_date, txn_type, source_table, source_id, branch_id, account, amount, description, created_by, created_at
                          from public.app_cash_bank_ledger('2026-01-02','2026-01-31')
                         where txn_type not in ('Shop expenses', 'Shop savings') order by 1,2,3,4,6,7""").fetchall()
    reports = c.execute("select id, report_date, shop_expenses, shop_savings, other_expenses::text from public.daily_reports order by id").fetchall()
    return {"bal": bal, "branch": branch, "sup": sup, "acct_totals": [(a, s) for a, s, _ in ledger], "reports": reports, "other_rows": others}


class TestHistoricalMigration:
    def test_balances_and_history_identical_after_migration(self):
        name, c = _build("0021")
        try:
            owner = _seed_history(c)
            before = _snapshot(c, owner)
            n_reports = c.execute("select count(*) from public.daily_reports").fetchone()[0]
            c.execute(M0021.read_text())
            after = _snapshot(c, owner)
            assert after["bal"] == before["bal"]
            assert after["branch"] == before["branch"]
            assert after["sup"] == before["sup"]
            assert after["other_rows"] == before["other_rows"]      # every non-shop ledger row identical (incl. descriptions)
            assert after["reports"] == before["reports"]          # every report, incl. frozen other_expenses, untouched
            # per-account totals identical (row counts differ: lines replace the single shop-expense row)
            assert after["acct_totals"] == before["acct_totals"]
            assert c.execute("select count(*) from public.daily_reports").fetchone()[0] == n_reports

            # nothing silently dropped or duplicated: Gas 50.00 + Soap 20.50 + balance (300-100-70.50 = 129.50)
            rows = c.execute("""select description, amount, remarks, category, payment_status, payment_account, supplier_id
                                from public.daily_report_expenses l join public.daily_reports r on r.id = l.report_id
                               where r.report_date = '2026-01-05' and r.total_sales = 1000 order by line_no""").fetchall()
            assert rows == [("Gas", D("50.00"), "cylinder", "Other (Legacy)", "Paid", "Cash", None),
                            ("Soap", D("20.50"), None, "Other (Legacy)", "Paid", "Cash", None),
                            ("Unitemised shop expenses (legacy balance)", D("129.50"),
                             "Created by migration 0021 from the report's shop expenses total", "Other (Legacy)", "Paid", "Cash", None)]
            # savings-only report: no expense line at all, savings preserved
            assert c.execute("select count(*) from public.daily_report_expenses l join public.daily_reports r on r.id=l.report_id "
                             "where r.shop_savings = 80 and r.shop_expenses = 80").fetchone()[0] == 0
            # voided report: its item is preserved as a voided line
            assert c.execute("select description, amount, voided_at is not null from public.daily_report_expenses "
                             "where description = 'void me'").fetchall() == [("void me", D("40.00"), True)]
            # reconcile invariant holds for every active report
            assert c.execute("""select count(*) from public.daily_reports r where voided_at is null and shop_expenses <>
                                shop_savings + (select coalesce(sum(amount),0) from public.daily_report_expenses where report_id=r.id and voided_at is null)""").fetchone()[0] == 0
            # the migration wrote one audit marker
            assert c.execute("select count(*) from public.audit_log where record_id='migration-0021'").fetchone()[0] == 1
        finally:
            _drop(name, c)

    def test_unreconcilable_history_stops_the_migration_and_changes_nothing(self):
        name, c = _build("0021")
        try:
            owner = _seed_history(c)
            bad = c.execute("""insert into public.daily_reports (report_date, branch_id, total_sales, shop_expenses, shop_savings, other_expenses)
                               values ('2026-01-09', (select id from public.branches where name='NDL'), 100, 60, 40,
                                       '[{"description":"too much","amount":"50.00"}]'::jsonb) returning id""").fetchone()[0]
            before = _snapshot(c, owner)
            with pytest.raises(psycopg.errors.CheckViolation) as e:
                c.execute(M0021.read_text())
            assert str(bad) in str(e.value)                       # the offending report is named
            assert c.execute("select to_regclass('public.daily_report_expenses')").fetchone()[0] is None
            assert _snapshot(c, owner) == before                  # nothing changed
        finally:
            _drop(name, c)

    def test_a_failure_at_any_point_rolls_back_the_whole_migration(self):
        name, c = _build("0021")
        try:
            _seed_history(c)
            with pytest.raises(psycopg.errors.DivisionByZero):
                c.execute(M0021.read_text() + "\nselect 1/0;")  # fails AFTER every 0021 statement ran
            assert c.execute("select to_regclass('public.daily_report_expenses')").fetchone()[0] is None
            assert c.execute("select count(*) from information_schema.columns where table_name='supplier_entries' "
                             "and column_name='source_expense_id'").fetchone()[0] == 0
            assert c.execute("select count(*) from pg_proc where proname = 'submit_daily_report'").fetchone()[0] == 0
            assert c.execute("select count(*) from public.audit_log where record_id = 'migration-0021'").fetchone()[0] == 0
        finally:
            _drop(name, c)

    def test_no_history_migrates_cleanly(self):
        name, c = _build("0021")
        try:
            c.execute(M0021.read_text())
            assert c.execute("select count(*) from public.daily_report_expenses").fetchone()[0] == 0
        finally:
            _drop(name, c)

"""Pre-production rehearsal of migration 0021 on a LARGE, production-shaped disposable database (synthetic data).
Run it on a laptop/CI against a DISPOSABLE PostgreSQL superuser DSN (edit ADMIN below); it creates and drops its own
database and never touches any other. It does NOT replace rehearsing on a restored copy of the real production database.

Builds a throw-away database at the pre-0021 schema (migrations 0001-0020), fills it with thousands of rows of every
money source (seeded RNG, so the run is reproducible), snapshots every balance at many dates, applies 0021 once,
snapshots again and compares EXACTLY. Never touches any other database.

usage: big_migration_check.py <repo_root> [n_days]
"""

import datetime as dt
import json
import pathlib
import random
import sys
import time
import uuid
from decimal import Decimal as D

import psycopg

ROOT = pathlib.Path(sys.argv[1]).resolve()
N_DAYS = int(sys.argv[2]) if len(sys.argv) > 2 else 320
ADMIN = "host=/tmp port=54329 user=postgres dbname=postgres"
MIG = sorted((ROOT / "database/migrations").glob("*.sql"))
STUB = ROOT / "database/tests/stub_supabase.sql"
M0021 = next(m for m in MIG if m.name.startswith("0021"))
rng = random.Random(20261119)

name = f"rehearsal_{uuid.uuid4().hex[:8]}"
with psycopg.connect(ADMIN, autocommit=True) as a:
    a.execute(f'create database "{name}"')
c = psycopg.connect(ADMIN.replace("dbname=postgres", f"dbname={name}"), autocommit=True)


def money(lo, hi):
    return D(rng.randint(int(lo * 100), int(hi * 100))) / 100


try:
    c.execute(STUB.read_text())
    for m in MIG:
        if m.name.startswith("0021"):
            break
        c.execute(m.read_text())

    # ------------------------------------------------------------------ seed
    br = c.execute(
        "select id, name from public.branches order by sort_order"
    ).fetchall()
    owner = c.execute(
        "insert into auth.users (email) values ('o@x.test') returning id"
    ).fetchone()[0]
    c.execute(
        "insert into public.profiles (user_id, full_name, role) values (%s,'Owner','owner')",
        [owner],
    )
    start = dt.date(2026, 1, 1)
    c.execute(
        "insert into public.opening_balances (as_of_date, receipts_bank, enbd_balance, adcb_balance, pos_pending, savings_brought_forward) values (%s,'ENBD',25000.55,18000.10,3000.25,5000.75)",
        [start - dt.timedelta(days=1)],
    )
    for bid, _ in br:
        c.execute(
            "insert into public.opening_branch_cash (branch_id, amount) values (%s,%s)",
            [bid, money(100, 3000)],
        )
    sups = [
        c.execute(
            "insert into public.suppliers (name, amount_owed, is_active) values (%s,%s,%s) returning id",
            [f"Supplier {i}", money(0, 5000), i % 7 != 0],
        ).fetchone()[0]
        for i in range(15)
    ]
    cats = [
        r[0] for r in c.execute("select id from public.expense_categories").fetchall()
    ]

    n_rep = n_items = n_void = 0
    days = [start + dt.timedelta(days=i) for i in range(N_DAYS)]
    for d in days:
        for bid, _bname in br:
            if rng.random() < 0.06:
                continue  # a missing day
            sales = money(200, 9000)
            cash = money(0, float(sales))
            pos = money(0, float(sales - cash)) if sales > cash else D(0)
            bank = money(0, float(sales - cash - pos)) if sales > cash + pos else D(0)
            link = (
                money(0, float(sales - cash - pos - bank))
                if sales > cash + pos + bank
                else D(0)
            )
            exp = money(0, 900) if rng.random() < 0.85 else D(0)
            sav = (
                (money(0, float(exp)) if rng.random() < 0.3 else D(0)) if exp else D(0)
            )
            if rng.random() < 0.03 and exp:
                sav = exp  # all savings
            room = exp - sav
            items = []
            for _ in range(rng.choice([0, 0, 1, 2, 3, 4])):
                if room <= D("0.01"):
                    break
                amt = (
                    money(0.01, float(room)) if rng.random() < 0.8 else room
                )  # sometimes consume the whole room
                items.append(
                    {
                        "description": f"item {rng.randint(1, 999)}",
                        "amount": f"{amt:.2f}",
                        **({"notes": "n"} if rng.random() < 0.4 else {}),
                    }
                )
                room -= amt
            rid = c.execute(
                """insert into public.daily_reports (report_date, branch_id, total_sales, order_count, cash_received, pos_amount,
                   bank_transfer, link_payment, shop_expenses, shop_savings, other_expenses) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb) returning id""",
                [
                    d,
                    bid,
                    sales,
                    rng.randint(1, 80),
                    cash,
                    pos,
                    bank,
                    link,
                    exp,
                    sav,
                    json.dumps(items),
                ],
            ).fetchone()[0]
            n_rep += 1
            n_items += len(items)
            if rng.random() < 0.03:
                c.execute(
                    "update public.daily_reports set voided_at=now(), void_reason='dup' where id=%s",
                    [rid],
                )
                n_void += 1
    # a report with a unicode description and an item equal to the entire room
    for d in days:
        if rng.random() < 0.12:
            c.execute(
                "insert into public.head_office_expenses (expense_date, branch_id, category_id, amount, paid_from, is_saving, note) values (%s,%s,%s,%s,%s,%s,%s)",
                [
                    d,
                    rng.choice([None] + [b[0] for b in br]),
                    rng.choice(cats),
                    money(10, 2500),
                    rng.choice(["Cash received", "ENBD", "ADCB"]),
                    rng.random() < 0.15,
                    "ملاحظة",
                ],
            )
    for _ in range(40):
        c.execute(
            "update public.head_office_expenses set voided_at=now(), void_reason='x' where id=(select id from public.head_office_expenses where voided_at is null order by random() limit 1)"
        )
    for sid in sups:
        for _ in range(rng.randint(2, 14)):
            d = rng.choice(days)
            if rng.random() < 0.55:
                c.execute(
                    "insert into public.supplier_entries (supplier_id, entry_date, kind, amount) values (%s,%s,'due',%s)",
                    [sid, d, money(50, 2500)],
                )
            else:
                acct = rng.choice(["Cash", "ENBD", "ADCB"])
                c.execute(
                    "insert into public.supplier_entries (supplier_id, entry_date, kind, amount, paid_from, branch_id) values (%s,%s,'payment',%s,%s,%s)",
                    [
                        sid,
                        d,
                        money(20, 900),
                        acct,
                        rng.choice(br)[0] if acct == "Cash" else None,
                    ],
                )
    c.execute(
        "update public.supplier_entries set voided_at=now(), void_reason='x' where id in (select id from public.supplier_entries order by random() limit 15)"
    )
    for _ in range(60):
        c.execute(
            "insert into public.pos_settlements (settlement_date, bank, amount, reference) values (%s,%s,%s,'S')",
            [rng.choice(days), rng.choice(["ENBD", "ADCB"]), money(100, 3000)],
        )
    for _ in range(80):
        fr, to = rng.sample(["Cash", "ENBD", "ADCB"], 2)
        bidt = rng.choice(br)[0] if "Cash" in (fr, to) else None
        c.execute(
            "insert into public.cash_transfers (transfer_date, from_account, to_account, branch_id, amount) values (%s,%s,%s,%s,%s)",
            [rng.choice(days), fr, to, bidt, money(50, 2000)],
        )
    for k in range(6):
        ra = c.execute(
            "insert into public.rental_agreements (branch_id, landlord, unit, start_date, end_date, annual_rent) values (%s,%s,'U1','2026-01-01','2026-12-31',12000) returning id",
            [br[k][0], f"Landlord {k}"],
        ).fetchone()[0]
        for qn in range(4):
            if qn < 3:
                c.execute(
                    "insert into public.pdc_cheques (agreement_id, cheque_no, bank, cheque_date, amount, status, cleared_at, cleared_on) values (%s,%s,%s,%s,3000,'Cleared',now(),%s)",
                    [
                        ra,
                        f"CH{k}-{qn}",
                        rng.choice(["ENBD", "ADCB"]),
                        dt.date(2026, 1 + qn * 2, 5),
                        dt.date(2026, 1 + qn * 2, 5),
                    ],
                )
            else:
                c.execute(
                    "insert into public.pdc_cheques (agreement_id, cheque_no, bank, cheque_date, amount, status) values (%s,%s,'ENBD',%s,3000,'Pending')",
                    [ra, f"CH{k}-{qn}", dt.date(2026, 11, 5)],
                )

    counts = {
        t: c.execute(f"select count(*) from public.{t}").fetchone()[0]
        for t in (
            "daily_reports",
            "head_office_expenses",
            "supplier_entries",
            "pos_settlements",
            "cash_transfers",
            "pdc_cheques",
            "audit_log",
        )
    }
    print(
        "SEEDED",
        json.dumps(counts),
        "| items in other_expenses:",
        n_items,
        "| voided reports:",
        n_void,
    )

    # ------------------------------------------------------------------ snapshot
    def snapshot():
        c.execute(
            "select set_config('request.jwt.claims', %s, false)",
            [json.dumps({"sub": str(owner)})],
        )
        out = {
            "bal": {},
            "branch": {},
            "ledger_day_acct": None,
            "other_rows": None,
            "sup": None,
            "reports": None,
            "sav": None,
        }
        asofs = [dt.date(2026, m, 28) for m in range(1, 12)] + [
            dt.date(2026, 11, 19),
            dt.date(2026, 3, 1),
            dt.date(2026, 6, 15),
        ]
        for a in asofs:
            out["bal"][str(a)] = c.execute(
                "select tracking_start, balance_date, cash_now, enbd_now, adcb_now, total_cash_bank, savings_total, pos_pending from public.app_balances(%s)",
                [a],
            ).fetchone()
            out["branch"][str(a)] = c.execute(
                "select branch_id, opening, incoming, outgoing, closing from public.app_branch_cash(%s) order by sort_order, branch_name",
                [a],
            ).fetchall()
        out["ledger_day_acct"] = c.execute(
            "select txn_date, account, sum(amount) from public.app_cash_bank_ledger('2026-01-01','2026-12-31') group by 1,2 order by 1,2"
        ).fetchall()
        out["other_rows"] = (
            c.execute("""select txn_date, txn_type, source_table, source_id, branch_id, account, amount, description, created_by, created_at
              from public.app_cash_bank_ledger('2026-01-01','2026-12-31') where txn_type not in ('Shop expenses','Shop savings') order by 1,2,3,4,6,7""").fetchall()
        )
        out["branch_day"] = c.execute(
            "select txn_date, branch_id, sum(amount) from public.app_cash_bank_ledger('2026-01-01','2026-12-31') where account='Cash' group by 1,2 order by 1,2"
        ).fetchall()
        out["sup"] = c.execute(
            "select supplier_id, opening_outstanding, new_dues, payments, outstanding from public.v_supplier_outstanding order by supplier_id"
        ).fetchall()
        out["reports"] = c.execute(
            "select id, report_date, branch_id, total_sales, cash_received, pos_amount, bank_transfer, link_payment, shop_expenses, shop_savings, other_expenses::text, voided_at, void_reason, created_at, updated_at from public.daily_reports order by id"
        ).fetchall()
        out["ho"] = c.execute(
            "select * from public.head_office_expenses order by id"
        ).fetchall()
        out["recon"] = c.execute(
            "select id, total_received, difference, check_label from public.v_daily_reports_reconciled order by id"
        ).fetchall()
        out["opening"] = (
            c.execute("select * from public.opening_balances").fetchall(),
            c.execute(
                "select * from public.opening_branch_cash order by branch_id"
            ).fetchall(),
            c.execute(
                "select id, name, amount_owed, is_active from public.suppliers order by id"
            ).fetchall(),
        )
        return out

    SNAP = (
        (ROOT / "database/verification/0021_balance_snapshot.sql")
        .read_text()
        .replace("<OWNER_USER_ID>", str(owner))
    )
    head, query = SNAP.split(";\n\nwith ob", 1)

    def run_snap():
        c.execute(head + ";")
        return [tuple(r) for r in c.execute("with ob" + query).fetchall()]

    def timed():
        c.execute(
            "select set_config('request.jwt.claims', %s, false)",
            [json.dumps({"sub": str(owner)})],
        )
        qs = {
            "app_balances(today)": "select * from public.app_balances('2026-11-19')",
            "app_branch_cash(today)": "select * from public.app_branch_cash('2026-11-19')",
            "ledger full year": "select count(*) from public.app_cash_bank_ledger('2026-01-01','2026-12-31')",
            "dashboard totals by branch (year)": "select r.branch_id, count(*), sum(r.total_sales), sum(r.shop_expenses), sum(r.shop_savings) from public.daily_reports r where r.voided_at is null and r.report_date between '2026-01-01' and '2026-12-31' group by r.branch_id",
            "dashboard totals by branch (year) WITH expense rows": "select r.branch_id, count(*), sum(r.total_sales), sum(r.shop_expenses), sum(x.paid), sum(x.outstanding), sum(x.cash_paid) from public.daily_reports r left join lateral (select sum(l.amount_paid) as paid, sum(l.outstanding) as outstanding, sum(l.amount_paid) filter (where l.payment_account = 'Cash') as cash_paid from public.daily_report_expenses l where l.report_id = r.id and l.voided_at is null) x on true where r.voided_at is null and r.report_date between '2026-01-01' and '2026-12-31' group by r.branch_id",
        }
        out = {}
        has_rows = (
            c.execute("select to_regclass('public.daily_report_expenses')").fetchone()[
                0
            ]
            is not None
        )
        for k, q in qs.items():
            if "WITH expense rows" in k and not has_rows:
                continue
            best = 1e9
            for _ in range(5):
                t = time.perf_counter()
                c.execute(q).fetchall()
                best = min(best, time.perf_counter() - t)
            out[k] = best * 1000
        return out

    t_before = timed()
    sql_before = run_snap()
    before = snapshot()
    audit_before = c.execute("select count(*) from public.audit_log").fetchone()[0]
    t0 = time.perf_counter()
    c.execute(M0021.read_text())
    took = time.perf_counter() - t0
    after = snapshot()
    # later migrations (0022, 0023 ...) must not change a single figure either
    later = [m for m in MIG if m.name > M0021.name]
    for m in later:
        c.execute(m.read_text())
    after_later = snapshot()
    print(f"LATER MIGRATIONS {[m.name[:4] for m in later]}: all figures identical to the 0021 result = {after_later == after}")
    after = after_later
    t_after = timed()
    print("TIMING ms (best of 5)  before -> after:")
    for k in t_after:
        print(f"   {k:55s} {t_before.get(k, float('nan')):8.1f} -> {t_after[k]:8.1f}")
    sql_after = run_snap()
    print(
        f"VERIFICATION SCRIPT: {len(sql_before)} rows; identical before/after = {sql_before == sql_after}"
    )
    if sql_before != sql_after:
        for a, b in zip(sql_before, sql_after, strict=True):
            if a != b:
                print("   DIFF", a, "|", b)
    print(f"MIGRATION 0021 applied in {took:.2f}s")

    bad = [k for k in before if before[k] != after[k]]
    for k in before:
        print(f"  {k:15s} identical: {before[k] == after[k]}")
    # strongest balance statement
    print(
        "  sample 2026-11-19 balances before:",
        [str(x) for x in before["bal"]["2026-11-19"][2:]],
    )
    print(
        "  sample 2026-11-19 balances after :",
        [str(x) for x in after["bal"]["2026-11-19"][2:]],
    )

    # ------------------------------------------------------------------ post-migration invariants
    def q(sql: str):
        return c.execute(sql).fetchone()[0]

    inv = {
        "reports unchanged count": q("select count(*) from public.daily_reports")
        == counts["daily_reports"],
        "active reports not reconciling": q("""select count(*) from public.daily_reports r where voided_at is null and shop_expenses <>
            shop_savings + (select coalesce(sum(amount),0) from public.daily_report_expenses where report_id=r.id and voided_at is null)"""),
        "legacy lines": q("select count(*) from public.daily_report_expenses"),
        "legacy items expected (items + balances)": None,
        "lines with supplier": q(
            "select count(*) from public.daily_report_expenses where supplier_id is not null"
        ),
        "supplier_entries with source": q(
            "select count(*) from public.supplier_entries where source_expense_id is not null"
        ),
        "supplier_entries unchanged": q("select count(*) from public.supplier_entries")
        == counts["supplier_entries"],
        "sum active lines == sum(exp-sav) active": q(
            "select coalesce(sum(amount),0) from public.daily_report_expenses where voided_at is null"
        )
        == q(
            "select coalesce(sum(shop_expenses-shop_savings),0) from public.daily_reports where voided_at is null"
        ),
        "sum ALL lines == sum(exp-sav) ALL": q(
            "select coalesce(sum(amount),0) from public.daily_report_expenses"
        )
        == q(
            "select coalesce(sum(shop_expenses-shop_savings),0) from public.daily_reports"
        ),
        "item descriptions+amounts preserved": q("""select count(*) from (
              select r.id, i->>'description' d, (i->>'amount')::numeric a from public.daily_reports r, jsonb_array_elements(r.other_expenses) i
              except select report_id, description, amount from public.daily_report_expenses where description <> 'Unitemised shop expenses (legacy balance)') x"""),
        "audit rows added": q("select count(*) from public.audit_log") - audit_before,
    }
    # expected number of legacy rows = items + reports with a positive unitemised balance
    inv["legacy items expected (items + balances)"] = (
        n_items
        + q("""select count(*) from public.daily_reports r where shop_expenses - shop_savings -
        (select coalesce(sum((i->>'amount')::numeric),0) from jsonb_array_elements(r.other_expenses) i) > 0""")
    )
    for k, v in inv.items():
        print(f"  {k}: {v}")
    ok = (
        sql_before == sql_after
        and len(sql_before) > 20
        and not bad
        and inv["reports unchanged count"]
        and inv["active reports not reconciling"] == 0
        and inv["supplier_entries unchanged"]
        and inv["sum active lines == sum(exp-sav) active"]
        and inv["sum ALL lines == sum(exp-sav) ALL"]
        and inv["item descriptions+amounts preserved"] == 0
        and inv["legacy lines"] == inv["legacy items expected (items + balances)"]
        and inv["supplier_entries with source"] == 0
        and inv["audit rows added"] == 1
    )
    try:
        c.execute(M0021.read_text())
        rerun = "NO ERROR (unexpected)"
    except psycopg.errors.DuplicateTable:
        rerun = "refused (relation already exists)"
    still = (
        run_snap() == sql_after
        and q("select count(*) from public.daily_report_expenses")
        == inv["legacy lines"]
    )
    print("SECOND RUN of 0021:", rerun, "| state unchanged:", still)
    ok = ok and rerun.startswith("refused") and still
    c.execute(
        "update public.head_office_expenses set amount = amount + 0.01 where id = (select id from public.head_office_expenses where voided_at is null limit 1)"
    )
    tampered = run_snap()
    detected = tampered != sql_after
    print(
        "NEGATIVE CONTROL (one head-office amount changed by 0.01) detected by the script:",
        detected,
    )
    ok = ok and detected
    print(
        "RESULT:",
        "PASS - every balance, ledger row, report and supplier figure identical"
        if ok
        else f"FAIL: {bad}",
    )
    sys.exit(0 if ok else 1)
finally:
    c.close()
    with psycopg.connect(ADMIN, autocommit=True) as a:
        a.execute(f'drop database if exists "{name}" with (force)')

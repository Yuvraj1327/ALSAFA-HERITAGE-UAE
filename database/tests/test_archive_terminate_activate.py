"""Migration 0023 at database level: constraints, triggers, privileges and the branch functions."""
import uuid

import psycopg
import pytest
from conftest import World, as_user, one, q

E = psycopg.errors


@pytest.fixture()
def w(conn):
    return World(conn)


def mk_task(conn, **kw):
    return one(conn, "insert into public.tasks (title, assignee_user_id, branch_id) values (%s,%s,%s) returning id",
               [kw.get("title", "t"), kw.get("assignee"), kw.get("branch")])


# ---------------------------------------------------------------- tasks
class TestTasks:
    def test_no_hard_delete_for_the_application_role(self, conn, w):
        assert one(conn, "select has_table_privilege('authenticated','public.tasks','DELETE')") is False
        assert one(conn, "select count(*) from pg_policies where tablename='tasks' and cmd='DELETE'") == 0
        mk_task(conn)
        with pytest.raises(E.InsufficientPrivilege), as_user(conn, w.owner):
            conn.execute("delete from public.tasks")

    def test_archive_columns_must_be_set_together(self, conn):
        t = mk_task(conn)
        with pytest.raises(E.CheckViolation):
            conn.execute("update public.tasks set archived_at = now() where id=%s", [t])

    def test_archived_task_is_read_only_until_restored(self, conn):
        t = mk_task(conn)
        conn.execute("update public.tasks set archived_at=now(), archive_reason='parked' where id=%s", [t])
        with pytest.raises(E.CheckViolation, match="archived task cannot be changed"):
            with conn.transaction():
                conn.execute("update public.tasks set title='x' where id=%s", [t])
        conn.execute("update public.tasks set archived_at=null, archive_reason=null where id=%s", [t])
        conn.execute("update public.tasks set title='x' where id=%s", [t])

    def test_archive_and_restore_work_when_the_assignee_was_deactivated_but_other_updates_still_refuse(self, conn, w):
        t = mk_task(conn, assignee=w.main)
        conn.execute("update public.profiles set is_active=false where user_id=%s", [w.main])
        conn.execute("update public.tasks set archived_at=now(), archive_reason='assignee left' where id=%s", [t])
        conn.execute("update public.tasks set archived_at=null, archive_reason=null where id=%s", [t])
        with pytest.raises(E.CheckViolation, match="active user"):  # the pre-existing rule is unchanged
            with conn.transaction():
                conn.execute("update public.tasks set title='x' where id=%s", [t])

    def test_audit_keeps_the_archive_reason(self, conn, w):
        t = mk_task(conn)
        with as_user(conn, w.owner):
            conn.execute("select set_config('app.audit_reason','Not needed',true)")
            conn.execute("update public.tasks set archived_at=now(), archived_by=auth.uid(), archive_reason='Not needed' where id=%s", [t])
        assert q(conn, "select reason from public.audit_log where table_name='tasks' and record_id=%s and action='UPDATE'", [str(t)]) == [("Not needed",)]


# ---------------------------------------------------------------- rental agreements
def mk_agr(conn, w, status="Active"):
    return one(conn, """insert into public.rental_agreements (branch_id, landlord, start_date, end_date, annual_rent, status)
                        values (%s,'L','2026-01-01','2026-12-31',1000,%s) returning id""", [w.branch["ALSAFA MAIN"], status])


class TestRentals:
    def test_termination_record_shape(self, conn, w):
        a = mk_agr(conn, w)
        with pytest.raises(E.CheckViolation):  # date without a reason
            conn.execute("update public.rental_agreements set status='Terminated', terminated_on='2026-06-01' where id=%s", [a])

    def test_terminated_on_requires_terminated_status(self, conn, w):
        a = mk_agr(conn, w)
        with pytest.raises(E.CheckViolation):
            conn.execute("update public.rental_agreements set terminated_on='2026-06-01', termination_reason='abc' where id=%s", [a])

    def test_termination_is_final_and_cheques_survive(self, conn, w):
        a = mk_agr(conn, w)
        c = one(conn, "insert into public.pdc_cheques (agreement_id, cheque_no, bank, cheque_date, amount) values (%s,'C1','ENBD','2026-03-01',100) returning id", [a])
        conn.execute("""update public.rental_agreements set status='Terminated', terminated_on='2026-06-01', terminated_at=now(),
                        termination_reason='Agreed to end' where id=%s""", [a])
        assert q(conn, "select status from public.pdc_cheques where id=%s", [c]) == [("Pending",)]
        for sql in ("update public.rental_agreements set status='Active' where id=%s",
                    "update public.rental_agreements set termination_reason='rewritten' where id=%s",
                    "update public.rental_agreements set terminated_on='2026-07-01' where id=%s"):
            with pytest.raises(E.CheckViolation), conn.transaction():
                conn.execute(sql, [a])
        with pytest.raises(E.CheckViolation), conn.transaction():
            conn.execute("delete from public.pdc_cheques where id=%s", [c])  # cheques are never deleted either


# ---------------------------------------------------------------- customers
def mk_customer(conn, w, branch="ALSAFA MAIN"):
    n = uuid.uuid4().int % 10**8
    return one(conn, "insert into public.customers (branch_id, name, mobile) values (%s,'Cust',%s) returning id",
               [w.branch[branch], f"+9715{n:08d}"])


def mk_order(conn, w, cust, branch="ALSAFA MAIN"):
    cat = one(conn, "select id from public.service_categories limit 1")
    return conn.execute("""insert into public.orders (customer_id, branch_id, category_id, service_type, delivery_at, order_total)
                           values (%s,%s,%s,'Standard', now() + interval '2 day', 100) returning id""", [cust, w.branch[branch], cat])


class TestCustomers:
    def test_archive_columns_must_be_set_together(self, conn, w):
        c = mk_customer(conn, w)
        with pytest.raises(E.CheckViolation):
            conn.execute("update public.customers set archived_at=now() where id=%s", [c])

    def test_archived_customer_is_read_only_and_gets_no_new_order_but_keeps_old_ones(self, conn, w):
        c = mk_customer(conn, w)
        mk_order(conn, w, c)
        conn.execute("update public.customers set archived_at=now(), archive_reason='left' where id=%s", [c])
        assert q(conn, "select count(*) from public.orders where customer_id=%s", [c]) == [(1,)]  # history stays
        with pytest.raises(E.CheckViolation, match="archived customer cannot be changed"), conn.transaction():
            conn.execute("update public.customers set name='x' where id=%s", [c])
        with pytest.raises(E.CheckViolation, match="customer is archived"), conn.transaction():
            mk_order(conn, w, c)
        conn.execute("update public.customers set notes='ok' where id=%s", [mk_customer(conn, w)])  # other customers unaffected
        conn.execute("update public.customers set archived_at=null, archive_reason=null where id=%s", [c])
        mk_order(conn, w, c)  # restored: new orders are fine again

    def test_branch_user_can_archive_only_inside_their_own_branch(self, conn, w):
        mine, theirs = mk_customer(conn, w, "ALSAFA MAIN"), mk_customer(conn, w, "JERNAS")
        with as_user(conn, w.main):
            assert conn.execute("update public.customers set archived_at=now(), archive_reason='mine' where id=%s", [mine]).rowcount == 1
            assert conn.execute("update public.customers set archived_at=now(), archive_reason='theirs' where id=%s", [theirs]).rowcount == 0
        assert q(conn, "select archived_at is null from public.customers where id=%s", [theirs]) == [(True,)]

    def test_customers_cannot_be_deleted(self, conn, w):
        mk_customer(conn, w)
        with pytest.raises(E.InsufficientPrivilege), as_user(conn, w.owner):
            conn.execute("delete from public.customers")


# ---------------------------------------------------------------- branches
def deps(conn, w, branch):
    with as_user(conn, w.owner):
        return {r[0]: r[2] for r in q(conn, "select * from public.branch_dependencies(%s)", [w.branch[branch]])}


class TestBranches:
    def test_owner_only(self, conn, w):
        with pytest.raises(E.InsufficientPrivilege), as_user(conn, w.main):
            conn.execute("select * from public.branch_dependencies(%s)", [w.branch["JERNAS"]])
        with pytest.raises(E.InsufficientPrivilege), as_user(conn, w.main):
            conn.execute("select public.set_branch_active(%s, false, 'not allowed')", [w.branch["JERNAS"]])
        with as_user(conn, w.main):  # a column grant exists, but the Owner-only policy hides every row from a Branch User
            assert conn.execute("update public.branches set is_active=false where id=%s", [w.branch["JERNAS"]]).rowcount == 0
        assert one(conn, "select is_active from public.branches where id=%s", [w.branch["JERNAS"]]) is True

    def test_each_dependency_blocks_deactivation(self, conn, w):
        # JERNAS has a Branch User (World)
        d = deps(conn, w, "JERNAS")
        assert d["active_users"] == 1 and d["last_active_branch"] == 0
        with pytest.raises(E.CheckViolation, match="branch_has_dependencies"), as_user(conn, w.owner):
            conn.execute("select public.set_branch_active(%s, false, 'closing down')", [w.branch["JERNAS"]])
        assert one(conn, "select is_active from public.branches where id=%s", [w.branch["JERNAS"]]) is True

    def test_clean_branch_deactivates_and_reactivates_with_audit(self, conn, w):
        b = w.branch["KARAMA"]
        assert all(v == 0 for v in deps(conn, w, "KARAMA").values())
        with as_user(conn, w.owner):
            conn.execute("select public.set_branch_active(%s, false, 'closing down')", [b])
        row = q(conn, "select is_active, deactivation_reason, deactivated_at is not null from public.branches where id=%s", [b])[0]
        assert row == (False, "closing down", True)
        with pytest.raises(E.CheckViolation, match="already inactive"), as_user(conn, w.owner):
            conn.execute("select public.set_branch_active(%s, false, 'again')", [b])
        with as_user(conn, w.owner):
            conn.execute("select public.set_branch_active(%s, true, 'reopened')", [b])
        assert q(conn, "select is_active, deactivation_reason from public.branches where id=%s", [b]) == [(True, None)]
        reasons = [r[0] for r in q(conn, "select reason from public.audit_log where table_name='branches' and record_id=%s and action='UPDATE' order by id", [str(b)])]
        assert reasons[-2:] == ["closing down", "reopened"]

    def test_reason_is_mandatory(self, conn, w):
        with pytest.raises(E.CheckViolation), as_user(conn, w.owner):
            conn.execute("select public.set_branch_active(%s, false, ' ')", [w.branch["KARAMA"]])

    def test_open_orders_tasks_agreements_and_cash_block(self, conn, w):
        b = "KARAMA"
        c = mk_customer(conn, w, b)
        mk_order(conn, w, c, b)
        mk_task(conn, branch=w.branch[b])
        mk_agr_b = one(conn, "insert into public.rental_agreements (branch_id, landlord, start_date, end_date, annual_rent) values (%s,'L','2026-01-01','2026-12-31',1) returning id", [w.branch[b]])
        conn.execute("insert into public.opening_balances (as_of_date, receipts_bank) values ('2020-01-01','ENBD') on conflict do nothing")
        conn.execute("insert into public.opening_branch_cash (branch_id, amount) values (%s, 75) on conflict (branch_id) do update set amount=75", [w.branch[b]])
        d = deps(conn, w, b)
        assert (d["open_orders"], d["open_tasks"], d["active_agreements"], d["branch_cash"]) == (1, 1, 1, 1)
        conn.execute("update public.tasks set archived_at=now(), archive_reason='parked' where branch_id=%s", [w.branch[b]])
        conn.execute("update public.rental_agreements set status='Terminated', terminated_on='2026-06-01', termination_reason='ended' where id=%s", [mk_agr_b])
        conn.execute("update public.orders set status='Cancelled', cancel_reason='Cancelled in test', cancelled_at=now() where branch_id=%s", [w.branch[b]])
        conn.execute("update public.opening_branch_cash set amount=0 where branch_id=%s", [w.branch[b]])
        assert all(v == 0 for v in deps(conn, w, b).values())

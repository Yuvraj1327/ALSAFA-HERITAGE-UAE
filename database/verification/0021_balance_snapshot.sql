-- 0021_balance_snapshot.sql : READ-ONLY before/after check for migration 0021.
--
-- Run this script TWICE in the SQL editor / psql as the database owner: once BEFORE applying 0021 and once AFTER.
-- Save both outputs and compare them. Every row must be IDENTICAL (same `section`, `item` and `value`).
-- It only SELECTs. The one set_config() below sets a session setting (it does not write any table) so that the
-- owner-only balance functions run as the Owner.
--
-- Replace <OWNER_USER_ID> with the Owner's auth user id (profiles.user_id of the active owner) before running.

select set_config('request.jwt.claims', json_build_object('sub', '<OWNER_USER_ID>', 'role', 'authenticated')::text, false);

with ob as (select as_of_date + 1 as start_d from public.opening_balances where id = 1),
-- 1. headline balances today and at every month end since tracking started
months as (
  select (date_trunc('month', ob.start_d) + (n || ' month')::interval + interval '1 month - 1 day')::date as d
    from ob, generate_series(0, 36) n
   where (date_trunc('month', ob.start_d) + (n || ' month')::interval + interval '1 month - 1 day')::date
         <= (now() at time zone 'Asia/Dubai')::date
  union select (now() at time zone 'Asia/Dubai')::date
),
bal as (
  select m.d, b.* from months m cross join lateral public.app_balances(m.d) b
)
select '1 balances' as section, d::text as item,
       'cash=' || cash_now || ' enbd=' || enbd_now || ' adcb=' || adcb_now || ' pos_pending=' || pos_pending
       || ' total_cash_bank=' || total_cash_bank || ' savings=' || savings_total as value
  from bal
union all
-- 2. cash held by each branch today
select '2 branch cash', coalesce(branch_name, 'Head office'),
       'opening=' || opening || ' in=' || incoming || ' out=' || outgoing || ' closing=' || closing
  from public.app_branch_cash((now() at time zone 'Asia/Dubai')::date)
union all
-- 3. supplier balances
select '3 suppliers', 'total',
       'opening=' || coalesce(sum(opening_outstanding), 0) || ' dues=' || coalesce(sum(new_dues), 0)
       || ' payments=' || coalesce(sum(payments), 0) || ' outstanding=' || coalesce(sum(outstanding), 0)
  from public.v_supplier_outstanding
union all
select '3 suppliers', 'per-supplier checksum',
       md5(string_agg(supplier_id::text || ':' || opening_outstanding || ':' || new_dues || ':' || payments || ':' || outstanding,
                      '|' order by supplier_id))
  from public.v_supplier_outstanding
union all
-- 4. the cash/bank ledger: totals per account and day, and every row that is NOT a shop-expense row
select '4 ledger', 'per account and day (checksum)',
       md5(string_agg(txn_date::text || ':' || account || ':' || s, '|' order by txn_date, account))
  from (select txn_date, account, sum(amount) as s
          from public.app_cash_bank_ledger((select start_d from ob), (now() at time zone 'Asia/Dubai')::date)
         group by 1, 2) x
union all
select '4 ledger', 'per account (totals)',
       string_agg(account || '=' || s, ' ' order by account)
  from (select account, sum(amount) as s
          from public.app_cash_bank_ledger((select start_d from ob), (now() at time zone 'Asia/Dubai')::date)
         group by 1) x
union all
select '4 ledger', 'cash per branch and day (checksum)',
       md5(string_agg(txn_date::text || ':' || coalesce(branch_id::text, '-') || ':' || s, '|' order by txn_date, branch_id))
  from (select txn_date, branch_id, sum(amount) as s
          from public.app_cash_bank_ledger((select start_d from ob), (now() at time zone 'Asia/Dubai')::date)
         where account = 'Cash' group by 1, 2) x
union all
select '4 ledger', 'non-shop rows (checksum)',
       md5(string_agg(txn_date::text || ':' || txn_type || ':' || source_id::text || ':' || coalesce(branch_id::text, '-') || ':'
                      || account || ':' || amount || ':' || coalesce(description, ''), '|'
                      order by txn_date, txn_type, source_id, account, amount))
  from public.app_cash_bank_ledger((select start_d from ob), (now() at time zone 'Asia/Dubai')::date)
 where txn_type not in ('Shop expenses', 'Shop savings')
union all
-- 5. the reports themselves must be untouched
select '5 reports', 'active / voided counts',
       count(*) filter (where voided_at is null) || ' / ' || count(*) filter (where voided_at is not null)
  from public.daily_reports
union all
select '5 reports', 'sums over active reports',
       'sales=' || coalesce(sum(total_sales), 0) || ' cash=' || coalesce(sum(cash_received), 0) || ' pos=' || coalesce(sum(pos_amount), 0)
       || ' bank=' || coalesce(sum(bank_transfer), 0) || ' link=' || coalesce(sum(link_payment), 0)
       || ' shop_expenses=' || coalesce(sum(shop_expenses), 0) || ' savings=' || coalesce(sum(shop_savings), 0)
  from public.daily_reports where voided_at is null
union all
select '5 reports', 'row checksum (all columns the migration must not change)',
       md5(string_agg(id::text || ':' || report_date || ':' || branch_id || ':' || total_sales || ':' || cash_received || ':'
                      || pos_amount || ':' || bank_transfer || ':' || link_payment || ':' || shop_expenses || ':' || shop_savings
                      || ':' || other_expenses::text || ':' || coalesce(voided_at::text, '-'), '|' order by id))
  from public.daily_reports
union all
select '6 other money', 'head office expenses (checksum)',
       md5(string_agg(id::text || ':' || amount || ':' || paid_from || ':' || is_saving || ':' || coalesce(voided_at::text, '-'), '|' order by id))
  from public.head_office_expenses
union all
select '6 other money', 'opening balances',
       'as_of=' || as_of_date || ' enbd=' || enbd_balance || ' adcb=' || adcb_balance || ' pos=' || pos_pending || ' savings=' || savings_brought_forward
  from public.opening_balances
order by 1, 2;

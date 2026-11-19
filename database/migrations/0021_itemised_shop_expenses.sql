-- 0021_itemised_shop_expenses.sql
-- Itemised shop expenses, supplier integration and payment-based cash/bank deductions.
-- ADDITIVE: no table, column or row is dropped or rewritten. Existing reports keep every value they have today.
--
--   1. public.daily_report_expenses : one row per expense line (6 categories, supplier link, Paid/Unpaid/Partial,
--      payment account, amount paid, outstanding). Immutable except for void columns.
--   2. supplier_entries.source_expense_id : traceable link from a ledger row to the expense line that created it,
--      with a partial unique index so a line can never create two dues or two payments.
--   3. Historical backfill: every existing report is itemised into "Other (Legacy)" lines. A report whose numbers do
--      not reconcile STOPS the migration (nothing is changed) and the offending report ids are listed.
--   4. Reconciliation rule enforced by the database (deferred constraint trigger):
--         active report: shop_expenses = SUM(active line amounts) + shop_savings
--   5. app_cash_bank_ledger() now deducts what was ACTUALLY PAID: non-supplier lines from their payment account,
--      supplier-linked lines through the supplier payment row (never both). Unpaid amounts move nothing.
--   6. Secure functions: submit_daily_report(), edit_daily_report() (replaced), void_daily_report() (replaced),
--      report_supplier_options(). All SECURITY DEFINER with a pinned search_path and identity/branch checks inside.
--   7. The old JSON validation trigger on daily_reports.other_expenses is replaced; the column stays as a frozen
--      read-only legacy record.
--
-- Manual deployment: see docs/database/MIGRATION_GUIDE.md ("0021"). Run on a backup/copy first.

-- ------------------------------------------------------------------------------------------------ 0. pre-flight
-- Stop BEFORE changing anything if any report cannot be itemised without inventing or losing money:
-- the legacy "Other" items must fit inside (shop_expenses - shop_savings).
do $$
declare bad text;
begin
  select string_agg(r.id::text || ' [' || r.report_date::text || ', expenses ' || r.shop_expenses::text
                    || ', savings ' || r.shop_savings::text || ', other items '
                    || (select coalesce(sum((i ->> 'amount')::numeric), 0) from jsonb_array_elements(r.other_expenses) i)::text || ']',
                    '; ' order by r.report_date)
    into bad
    from public.daily_reports r
   where (select coalesce(sum((i ->> 'amount')::numeric), 0) from jsonb_array_elements(r.other_expenses) i)
         > r.shop_expenses - r.shop_savings;
  if bad is not null then
    raise exception 'Migration 0021 stopped. These daily reports do not reconcile (other items exceed shop expenses less savings): %', bad
      using errcode = 'check_violation',
            hint = 'Review and correct these reports (owner edit/void) and re-run. No data was changed.';
  end if;
end $$;

-- ------------------------------------------------------------------------------------------------ 1. table
create table public.daily_report_expenses (
  id              uuid primary key default gen_random_uuid(),
  report_id       uuid not null references public.daily_reports (id) on delete restrict,
  branch_id       uuid not null references public.branches (id) on delete restrict,   -- = report branch (RLS)
  expense_date    date not null,                                                       -- = report date
  line_no         smallint not null,
  category        text not null,
  description     text not null,
  amount          numeric(14,2) not null,
  remarks         text,
  supplier_id     uuid references public.suppliers (id) on delete restrict,
  supplier_name   text,                      -- snapshot, so Branch Users never need to read the owner-only suppliers table
  payment_status  text not null,
  payment_account text,
  amount_paid     numeric(14,2) not null default 0,
  outstanding     numeric(14,2) generated always as (amount - amount_paid) stored,
  is_legacy       boolean not null default false,
  voided_at       timestamptz,
  voided_by       uuid,
  void_reason     text,
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now(),
  created_by      uuid default auth.uid(),
  updated_by      uuid,
  constraint dre_category_chk check (category in
    ('Food', 'Raw Material', 'Stationery', 'Repairs & Maintenance', 'Petrol', 'Other', 'Other (Legacy)')),
  constraint dre_legacy_chk check ((category = 'Other (Legacy)') = is_legacy),
  constraint dre_legacy_shape_chk check (not is_legacy or (supplier_id is null and payment_status = 'Paid')),
  constraint dre_desc_chk check (char_length(btrim(description)) between 1 and 200),
  constraint dre_remarks_chk check (remarks is null or char_length(remarks) <= 500),
  constraint dre_amount_pos check (amount > 0),
  constraint dre_status_chk check (payment_status in ('Paid', 'Unpaid', 'Partial')),
  constraint dre_account_chk check (payment_account is null or payment_account in ('Cash', 'ENBD', 'ADCB')),
  constraint dre_payment_shape_chk check (
       (payment_status = 'Paid'    and amount_paid = amount and payment_account is not null)
    or (payment_status = 'Unpaid'  and amount_paid = 0      and payment_account is null)
    or (payment_status = 'Partial' and amount_paid > 0 and amount_paid < amount and payment_account is not null)),
  constraint dre_credit_needs_supplier_chk check (payment_status = 'Paid' or supplier_id is not null),
  constraint dre_supplier_name_chk check ((supplier_id is null) = (supplier_name is null)),
  constraint dre_void_pair_chk check ((voided_at is null) = (void_reason is null))
);
create unique index dre_report_line_uq on public.daily_report_expenses (report_id, line_no) where voided_at is null;
create index dre_report_idx   on public.daily_report_expenses (report_id);
create index dre_date_idx     on public.daily_report_expenses (expense_date) where voided_at is null;
create index dre_branch_idx   on public.daily_report_expenses (branch_id, expense_date) where voided_at is null;
create index dre_category_idx on public.daily_report_expenses (category, expense_date) where voided_at is null;
create index dre_supplier_idx on public.daily_report_expenses (supplier_id) where supplier_id is not null;

comment on table public.daily_report_expenses is
  'Itemised shop expenses of a daily report. SUM(active lines) + daily_reports.shop_savings = daily_reports.shop_expenses. '
  'Created/voided only by submit_/edit_/void_daily_report(); rows are immutable apart from the void columns.';
comment on column public.daily_report_expenses.outstanding is
  'amount - amount_paid at the time of entry. Later supplier payments reduce the supplier balance, not this figure.';

-- Immutable apart from the void columns. `outstanding` is generated: PostgreSQL computes it AFTER BEFORE-triggers run,
-- so it is excluded from the comparison (it can only change if amount / amount_paid change, which are compared).
create or replace function app.dre_row_immutable() returns trigger
language plpgsql
as $$
begin
  if to_jsonb(new) - 'voided_at' - 'voided_by' - 'void_reason' - 'updated_at' - 'updated_by' - 'outstanding'
     is distinct from to_jsonb(old) - 'voided_at' - 'voided_by' - 'void_reason' - 'updated_at' - 'updated_by' - 'outstanding' then
    raise exception 'expense lines cannot be edited; void and re-enter' using errcode = 'check_violation';
  end if;
  return new;
end $$;
revoke all on function app.dre_row_immutable() from public, anon, authenticated;
create trigger dre_touch     before update on public.daily_report_expenses for each row execute function app.touch_row();
create trigger dre_immutable before update on public.daily_report_expenses for each row execute function app.dre_row_immutable();

-- RLS: Owner reads everything; a Branch User reads only their own branch. Nobody writes through the API (no insert,
-- update or delete grant): rows come from the SECURITY DEFINER functions below.
revoke all on public.daily_report_expenses from public, anon, authenticated;
grant select on public.daily_report_expenses to authenticated;
alter table public.daily_report_expenses enable row level security;
create policy dre_select on public.daily_report_expenses for select to authenticated
  using ((select app.is_owner()) or branch_id = (select app.branch_id()));

-- ------------------------------------------------------------------------------------------------ 2. supplier link
alter table public.supplier_entries
  add column source_expense_id uuid references public.daily_report_expenses (id) on delete restrict;
create unique index se_source_kind_uq on public.supplier_entries (source_expense_id, kind)
  where source_expense_id is not null and voided_at is null;
create index se_source_idx on public.supplier_entries (source_expense_id) where source_expense_id is not null;
comment on column public.supplier_entries.source_expense_id is
  'Set when the row was generated by a daily-report expense line. Such rows can only be voided by correcting the report.';

-- A generated supplier row must not be voided on its own (it would leave the expense line and the ledger disagreeing).
create or replace function app.void_ledger_row(p_table text, p_id uuid, p_reason text)
returns void
language plpgsql security definer set search_path = public, pg_temp
as $$
declare n integer;
begin
  if not app.is_owner() then
    raise exception 'owner only' using errcode = 'insufficient_privilege';
  end if;
  if p_reason is null or btrim(p_reason) = '' then
    raise exception 'a void reason is required' using errcode = 'check_violation';
  end if;
  if p_table not in ('supplier_entries', 'pos_settlements', 'cash_transfers') then
    raise exception 'unknown ledger table' using errcode = 'invalid_parameter_value';
  end if;
  if p_table = 'supplier_entries'
     and exists (select 1 from public.supplier_entries where id = p_id and source_expense_id is not null) then
    raise exception 'this entry was created by a daily report expense; correct or void the daily report instead'
      using errcode = 'check_violation';
  end if;
  perform set_config('app.audit_reason', p_reason, true);
  execute format('update public.%I set voided_at = now(), voided_by = auth.uid(), void_reason = $1
                   where id = $2 and voided_at is null', p_table) using p_reason, p_id;
  get diagnostics n = row_count;
  if n = 0 then
    raise exception 'row % not found or already voided', p_id using errcode = 'no_data_found';
  end if;
end $$;

-- ------------------------------------------------------------------------------------------------ 3. reconcile rule
create or replace function app.check_report_expense_totals(p_report uuid) returns void
language plpgsql security definer set search_path = public, pg_temp
as $$
declare r public.daily_reports%rowtype; lines numeric;
begin
  select * into r from public.daily_reports where id = p_report;
  if not found or r.voided_at is not null then return; end if;
  select coalesce(sum(amount), 0) into lines from public.daily_report_expenses
   where report_id = p_report and voided_at is null;
  if lines + r.shop_savings <> r.shop_expenses then
    raise exception 'daily report %: shop expenses % must equal itemised expenses % + savings %',
      p_report, r.shop_expenses, lines, r.shop_savings using errcode = 'check_violation';
  end if;
end $$;

create or replace function app.dr_reconcile_trg() returns trigger
language plpgsql security definer set search_path = public, pg_temp
as $$
begin
  if tg_table_name = 'daily_reports' then
    perform app.check_report_expense_totals(new.id);
  else
    perform app.check_report_expense_totals(new.report_id);
  end if;
  return null;
end $$;

-- ------------------------------------------------------------------------------------------------ 4. old JSON trigger
drop trigger if exists dr_validate_other on public.daily_reports;
drop function if exists app.dr_validate_other();

-- other_expenses is now a frozen legacy record: new reports carry [] and existing values can never change.
create or replace function app.dr_freeze_other() returns trigger
language plpgsql
as $$
begin
  if tg_op = 'INSERT' and new.other_expenses <> '[]'::jsonb then
    raise exception 'other_expenses is read-only legacy data; use itemised expenses' using errcode = 'check_violation';
  end if;
  if tg_op = 'UPDATE' and new.other_expenses is distinct from old.other_expenses then
    raise exception 'other_expenses is read-only legacy data; use itemised expenses' using errcode = 'check_violation';
  end if;
  return new;
end $$;
revoke all on function app.dr_freeze_other() from public, anon, authenticated;
create trigger dr_freeze_other before insert or update of other_expenses on public.daily_reports
  for each row execute function app.dr_freeze_other();

-- ------------------------------------------------------------------------------------------------ 5. backfill
-- One legacy line per existing "Other" item (description, amount, notes preserved) plus ONE legacy line for the
-- remaining un-itemised balance (shop_expenses - shop_savings - items). All are Paid from Cash, exactly how the old
-- ledger deducted them, so every balance is identical afterwards. Voided reports get voided lines.
do $$
declare
  r record; it record; n smallint; items numeric; bal numeric;
  created_n integer := 0; rep_n integer := 0; sum_before numeric; sum_after numeric;
begin
  select coalesce(sum(shop_expenses - shop_savings), 0) into sum_before from public.daily_reports where voided_at is null;

  for r in select * from public.daily_reports order by created_at, id loop
    n := 0;
    for it in select value as v from jsonb_array_elements(r.other_expenses) loop
      n := n + 1;
      insert into public.daily_report_expenses
        (report_id, branch_id, expense_date, line_no, category, description, amount, remarks, payment_status,
         payment_account, amount_paid, is_legacy, voided_at, voided_by, void_reason, created_at, created_by)
      values (r.id, r.branch_id, r.report_date, n, 'Other (Legacy)', btrim(it.v ->> 'description'),
              (it.v ->> 'amount')::numeric, nullif(it.v ->> 'notes', ''), 'Paid', 'Cash', (it.v ->> 'amount')::numeric, true,
              r.voided_at, r.voided_by, case when r.voided_at is not null then 'Report voided (migrated)' end,
              r.created_at, r.created_by);
      created_n := created_n + 1;
    end loop;
    select coalesce(sum((i ->> 'amount')::numeric), 0) into items from jsonb_array_elements(r.other_expenses) i;
    bal := r.shop_expenses - r.shop_savings - items;
    if bal > 0 then
      n := n + 1;
      insert into public.daily_report_expenses
        (report_id, branch_id, expense_date, line_no, category, description, amount, remarks, payment_status,
         payment_account, amount_paid, is_legacy, voided_at, voided_by, void_reason, created_at, created_by)
      values (r.id, r.branch_id, r.report_date, n, 'Other (Legacy)', 'Unitemised shop expenses (legacy balance)', bal,
              'Created by migration 0021 from the report''s shop expenses total', 'Paid', 'Cash', bal, true,
              r.voided_at, r.voided_by, case when r.voided_at is not null then 'Report voided (migrated)' end,
              r.created_at, r.created_by);
      created_n := created_n + 1;
    end if;
    rep_n := rep_n + 1;
  end loop;

  -- Verification: itemised total of active reports must equal the old (shop_expenses - savings) total, to the fils.
  select coalesce(sum(l.amount), 0) into sum_after from public.daily_report_expenses l
   where l.voided_at is null and l.is_legacy;
  if sum_after <> sum_before then
    raise exception 'Migration 0021 stopped: itemised total % differs from the original total %', sum_after, sum_before
      using errcode = 'check_violation';
  end if;

  insert into public.audit_log (actor_user_id, action, table_name, record_id, new_values, reason)
  values (null, 'INSERT', 'daily_report_expenses', 'migration-0021',
          jsonb_build_object('reports_processed', rep_n, 'legacy_lines_created', created_n,
                             'active_total_before', sum_before, 'active_total_after', sum_after),
          'Migration 0021: historical shop expenses itemised as Other (Legacy)');
end $$;

-- Row audit + the reconcile constraints are attached AFTER the backfill (no per-line audit noise for the migration).
create trigger daily_report_expenses_audit after insert or update or delete on public.daily_report_expenses
  for each row execute function app.audit_row_change();
create constraint trigger dre_reconcile_lines after insert or update on public.daily_report_expenses
  deferrable initially deferred for each row execute function app.dr_reconcile_trg();
create constraint trigger dre_reconcile_report after insert or update of shop_expenses, shop_savings, voided_at on public.daily_reports
  deferrable initially deferred for each row execute function app.dr_reconcile_trg();

-- Final guard: every active report reconciles right now (aborts the whole migration otherwise).
do $$
declare bad text;
begin
  select string_agg(r.id::text, ', ') into bad
    from public.daily_reports r
   where r.voided_at is null
     and r.shop_expenses <> r.shop_savings + (select coalesce(sum(l.amount), 0)
                                                from public.daily_report_expenses l
                                               where l.report_id = r.id and l.voided_at is null);
  if bad is not null then
    raise exception 'Migration 0021 stopped. Reports that do not reconcile after itemising: %', bad
      using errcode = 'check_violation';
  end if;
end $$;

-- ------------------------------------------------------------------------------------------------ 6. ledger
-- Same signature and columns as 0012/0014. Differences (all else is identical to the 0014 version, incl. the cheque row):
--   * header shop_expenses is NO LONGER deducted. Instead:
--       - shop_savings            -> Cash (as before, savings were always inside the old total)
--       - non-supplier lines      -> payment_account, amount_paid (Unpaid moves nothing, Partial moves what was paid)
--       - supplier-linked lines   -> the generated 'Supplier payment' row from supplier_entries (counted once, there)
create or replace function public.app_cash_bank_ledger(p_from date, p_to date)
returns table (
  txn_date date, txn_type text, source_table text, source_id uuid, branch_id uuid, branch_name text,
  account text, amount numeric, description text, created_by uuid, created_by_name text, created_at timestamptz)
language plpgsql stable set search_path = public, pg_temp
as $$
declare ob public.opening_balances%rowtype; start_d date;
begin
  if not app.is_owner() then
    raise exception 'owner only' using errcode = 'insufficient_privilege';
  end if;
  select * into ob from public.opening_balances where id = 1;
  if not found then
    raise exception 'opening balances are not set' using errcode = 'no_data_found';
  end if;
  start_d := ob.as_of_date + 1;
  return query
  with raw as (
    select r.report_date as d, 'Daily report cash'::text as t, 'daily_reports'::text as st, r.id as sid, r.branch_id as b,
           'Cash'::text as acc, r.cash_received as amt, 'Cash received'::text as descr, r.created_by as cb, r.created_at as ca
      from public.daily_reports r where r.voided_at is null and r.cash_received > 0
    union all
    select r.report_date, 'Shop savings', 'daily_reports', r.id, r.branch_id, 'Cash', -r.shop_savings,
           'Shop savings', r.created_by, r.created_at
      from public.daily_reports r where r.voided_at is null and r.shop_savings > 0
    union all
    select l.expense_date, 'Shop expenses', 'daily_report_expenses', l.id, l.branch_id, l.payment_account, -l.amount_paid,
           l.category || ' - ' || l.description || case when l.payment_status = 'Partial' then ' (part paid)' else '' end,
           l.created_by, l.created_at
      from public.daily_report_expenses l join public.daily_reports r on r.id = l.report_id and r.voided_at is null
     where l.voided_at is null and l.supplier_id is null and l.amount_paid > 0
    union all
    select r.report_date, 'POS collection', 'daily_reports', r.id, r.branch_id, 'POS Pending', r.pos_amount,
           'POS card sales awaiting settlement', r.created_by, r.created_at
      from public.daily_reports r where r.voided_at is null and r.pos_amount > 0
    union all
    select r.report_date, 'Bank receipts', 'daily_reports', r.id, r.branch_id, ob.receipts_bank,
           r.bank_transfer + r.link_payment, 'Bank transfers and payment links', r.created_by, r.created_at
      from public.daily_reports r where r.voided_at is null and r.bank_transfer + r.link_payment > 0
    union all
    select h.expense_date, case when h.is_saving then 'Head office saving' else 'Head office expense' end,
           'head_office_expenses', h.id, h.branch_id,
           case h.paid_from when 'Cash received' then 'Cash' else h.paid_from end, -h.amount,
           coalesce(h.note, c.name), h.created_by, h.created_at
      from public.head_office_expenses h join public.expense_categories c on c.id = h.category_id
     where h.voided_at is null
    union all
    select e.entry_date, 'Supplier payment', 'supplier_entries', e.id, e.branch_id, e.paid_from, -e.amount,
           s.name || coalesce(' - ' || e.reference, ''), e.created_by, e.created_at
      from public.supplier_entries e join public.suppliers s on s.id = e.supplier_id
     where e.voided_at is null and e.kind = 'payment'
    union all
    select t.transfer_date, 'Transfer out', 'cash_transfers', t.id,
           case when t.from_account = 'Cash' then t.branch_id end, t.from_account, -t.amount,
           'To ' || t.to_account || coalesce(' - ' || t.reference, ''), t.created_by, t.created_at
      from public.cash_transfers t where t.voided_at is null
    union all
    select t.transfer_date, 'Transfer in', 'cash_transfers', t.id,
           case when t.to_account = 'Cash' then t.branch_id end, t.to_account, t.amount,
           'From ' || t.from_account || coalesce(' - ' || t.reference, ''), t.created_by, t.created_at
      from public.cash_transfers t where t.voided_at is null
    union all
    select p.settlement_date, 'POS settlement', 'pos_settlements', p.id, null::uuid, 'POS Pending', -p.amount,
           'Settled to ' || p.bank || coalesce(' - ' || p.reference, ''), p.created_by, p.created_at
      from public.pos_settlements p where p.voided_at is null
    union all
    select p.settlement_date, 'POS settlement', 'pos_settlements', p.id, null::uuid, p.bank, p.amount,
           'POS settlement received' || coalesce(' - ' || p.reference, ''), p.created_by, p.created_at
      from public.pos_settlements p where p.voided_at is null
    union all
    select k.cheque_date, 'Rental cheque cleared', 'pdc_cheques', k.id, ra.branch_id, k.bank, -k.amount,
           'Cheque ' || k.cheque_no || ' - ' || ra.landlord, coalesce(k.cleared_by, k.created_by), coalesce(k.cleared_at, k.created_at)
      from public.pdc_cheques k join public.rental_agreements ra on ra.id = k.agreement_id
     where k.status = 'Cleared'
  )
  select raw.d, raw.t, raw.st, raw.sid, raw.b, br.name, raw.acc, raw.amt, raw.descr, raw.cb, pr.full_name, raw.ca
    from raw
    left join public.branches br on br.id = raw.b
    left join public.profiles pr on pr.user_id = raw.cb
   where raw.d between greatest(p_from, start_d) and p_to
   order by raw.d, raw.ca, raw.t;
end $$;
-- app_balances() and app_branch_cash() are unchanged: they already derive every account from this ledger.

-- ------------------------------------------------------------------------------------------------ 7. functions
-- Validated view of a p_expenses payload. One output row per line. All business validation lives here.
create or replace function app.parse_report_expenses(p_expenses jsonb, p_date date)
returns table (line_no smallint, category text, description text, amount numeric, remarks text, supplier_id uuid,
               supplier_name text, payment_status text, payment_account text, amount_paid numeric)
language plpgsql set search_path = public, pg_temp
as $$
declare
  it jsonb; n integer := 0; v_amt text; v_paid_txt text; v_amount numeric; v_paid numeric;
  v_status text; v_acct text; v_cat text; v_desc text; v_sup uuid; v_sup_name text; v_sup_active boolean;
  ob_date date; k text;
begin
  if p_expenses is null or jsonb_typeof(p_expenses) <> 'array' then
    raise exception 'expenses must be a JSON array' using errcode = 'invalid_parameter_value';
  end if;
  if jsonb_array_length(p_expenses) > 100 then
    raise exception 'at most 100 expense lines per report' using errcode = 'check_violation';
  end if;
  for it in select value from jsonb_array_elements(p_expenses) loop
    n := n + 1;
    if jsonb_typeof(it) <> 'object' then
      raise exception 'expense line %: must be an object', n using errcode = 'check_violation';
    end if;
    for k in select jsonb_object_keys(it) loop
      if k not in ('category', 'description', 'amount', 'remarks', 'supplier_id', 'payment_status', 'payment_account', 'amount_paid') then
        raise exception 'expense line %: unknown field %', n, k using errcode = 'check_violation';
      end if;
    end loop;

    v_cat := it ->> 'category';
    if v_cat is null or v_cat not in ('Food', 'Raw Material', 'Stationery', 'Repairs & Maintenance', 'Petrol', 'Other') then
      raise exception 'expense line %: choose a category', n using errcode = 'check_violation';
    end if;
    v_desc := btrim(coalesce(it ->> 'description', ''));
    if char_length(v_desc) not between 1 and 200 then
      raise exception 'expense line %: description is required (max 200 characters)', n using errcode = 'check_violation';
    end if;
    if char_length(coalesce(it ->> 'remarks', '')) > 500 then
      raise exception 'expense line %: remarks are limited to 500 characters', n using errcode = 'check_violation';
    end if;

    v_amt := it ->> 'amount';
    if v_amt is null or v_amt !~ '^[0-9]{1,12}(\.[0-9]{1,2})?$' or v_amt::numeric <= 0 then
      raise exception 'expense line %: amount must be greater than zero', n using errcode = 'check_violation';
    end if;
    v_amount := v_amt::numeric;

    v_status := it ->> 'payment_status';
    if v_status is null or v_status not in ('Paid', 'Unpaid', 'Partial') then
      raise exception 'expense line %: payment status must be Paid, Unpaid or Partial', n using errcode = 'check_violation';
    end if;
    v_acct := nullif(it ->> 'payment_account', '');
    v_paid_txt := it ->> 'amount_paid';
    if v_paid_txt is not null and v_paid_txt !~ '^[0-9]{1,12}(\.[0-9]{1,2})?$' then
      raise exception 'expense line %: amount paid is not a valid amount', n using errcode = 'check_violation';
    end if;
    v_paid := case v_status when 'Paid' then coalesce(v_paid_txt::numeric, v_amount)
                            when 'Unpaid' then coalesce(v_paid_txt::numeric, 0)
                            else v_paid_txt::numeric end;
    if v_status = 'Paid' and v_paid <> v_amount then
      raise exception 'expense line %: a Paid expense must be paid in full', n using errcode = 'check_violation';
    elsif v_status = 'Unpaid' and v_paid <> 0 then
      raise exception 'expense line %: an Unpaid expense has no amount paid', n using errcode = 'check_violation';
    elsif v_status = 'Partial' and (v_paid is null or v_paid <= 0 or v_paid >= v_amount) then
      raise exception 'expense line %: a Partial payment must be more than zero and less than the amount', n using errcode = 'check_violation';
    end if;
    if v_status <> 'Unpaid' and v_acct is null then
      raise exception 'expense line %: choose the payment account (Cash, ENBD or ADCB)', n using errcode = 'check_violation';
    end if;
    if v_status = 'Unpaid' then v_acct := null; end if;
    if v_acct is not null and v_acct not in ('Cash', 'ENBD', 'ADCB') then
      raise exception 'expense line %: payment account must be Cash, ENBD or ADCB', n using errcode = 'check_violation';
    end if;

    v_sup := null; v_sup_name := null;
    if nullif(it ->> 'supplier_id', '') is not null then
      begin
        v_sup := (it ->> 'supplier_id')::uuid;
      exception when invalid_text_representation then
        raise exception 'expense line %: unknown supplier', n using errcode = 'check_violation';
      end;
      select s.name, s.is_active into v_sup_name, v_sup_active from public.suppliers s where s.id = v_sup;
      if not found then
        raise exception 'expense line %: unknown supplier', n using errcode = 'check_violation';
      end if;
      if not v_sup_active then
        raise exception 'expense line %: supplier "%" is inactive', n, v_sup_name using errcode = 'check_violation';
      end if;
      select as_of_date into ob_date from public.opening_balances where id = 1;
      if ob_date is null or p_date <= ob_date then
        raise exception 'expense line %: supplier-linked expenses can only be recorded for dates after the opening balance date', n
          using errcode = 'check_violation';
      end if;
    elsif v_status <> 'Paid' then
      raise exception 'expense line %: an Unpaid or Partial expense needs a supplier', n using errcode = 'check_violation';
    end if;

    line_no := n; category := v_cat; description := v_desc; amount := v_amount;
    remarks := nullif(btrim(coalesce(it ->> 'remarks', '')), '');
    supplier_id := v_sup; supplier_name := v_sup_name; payment_status := v_status;
    payment_account := v_acct; amount_paid := v_paid;
    return next;
  end loop;
end $$;

-- Inserts the lines and (for supplier-linked lines) the due / payment ledger rows. Caller holds the finance lock.
create or replace function app.write_report_expenses(p_report uuid, p_branch uuid, p_date date, p_expenses jsonb)
returns void
language plpgsql set search_path = public, pg_temp
as $$
declare l record; v_line uuid; v_branch_name text; v_ref text;
begin
  select name into v_branch_name from public.branches where id = p_branch;
  for l in select * from app.parse_report_expenses(p_expenses, p_date) order by line_no loop
    insert into public.daily_report_expenses
      (report_id, branch_id, expense_date, line_no, category, description, amount, remarks, supplier_id, supplier_name,
       payment_status, payment_account, amount_paid)
    values (p_report, p_branch, p_date, l.line_no, l.category, l.description, l.amount, l.remarks, l.supplier_id,
            l.supplier_name, l.payment_status, l.payment_account, l.amount_paid)
    returning id into v_line;

    if l.supplier_id is not null then
      v_ref := 'Daily report ' || v_branch_name || ' ' || p_date::text || ' #' || l.line_no::text;
      insert into public.supplier_entries (supplier_id, entry_date, kind, amount, paid_from, branch_id, reference, note, source_expense_id)
      values (l.supplier_id, p_date, 'due', l.amount, null, null, v_ref, l.category || ' - ' || l.description, v_line);
      if l.amount_paid > 0 then
        insert into public.supplier_entries (supplier_id, entry_date, kind, amount, paid_from, branch_id, reference, note, source_expense_id)
        values (l.supplier_id, p_date, 'payment', l.amount_paid, l.payment_account,
                case when l.payment_account = 'Cash' then p_branch end, v_ref,
                'Paid at the time of the expense (' || l.payment_status || ')', v_line);
      end if;
    end if;
  end loop;
end $$;

create or replace function app.assert_no_negative_supplier() returns void
language plpgsql set search_path = public, pg_temp
as $$
begin
  if exists (select 1 from public.v_supplier_outstanding where outstanding < 0) then
    raise exception 'this change would leave a supplier with a negative balance (later supplier payments exist)'
      using errcode = 'check_violation';
  end if;
end $$;

-- Branch Users (own branch) and the Owner (any active branch) submit a report with its expense lines atomically.
create or replace function public.submit_daily_report(
  p_branch_id uuid, p_report_date date, p_total_sales numeric, p_order_count integer,
  p_cash numeric, p_pos numeric, p_bank numeric, p_link numeric, p_shop_savings numeric, p_expenses jsonb)
returns uuid
language plpgsql security definer set search_path = public, pg_temp
as $$
declare
  v_uid uuid := auth.uid(); v_role text := app.user_role(); v_total numeric; v_id uuid;
begin
  if v_uid is null or v_role is null then
    raise exception 'not signed in or inactive account' using errcode = 'insufficient_privilege';
  end if;
  if v_role = 'branch' then
    if p_branch_id is distinct from app.branch_id() then
      raise exception 'a Branch User may only submit reports for their own branch' using errcode = 'insufficient_privilege';
    end if;
  elsif v_role <> 'owner' then
    raise exception 'role not allowed' using errcode = 'insufficient_privilege';
  end if;
  if p_branch_id is null or not exists (select 1 from public.branches b where b.id = p_branch_id and b.is_active) then
    raise exception 'unknown or inactive branch' using errcode = 'foreign_key_violation';
  end if;
  if p_report_date is null then
    raise exception 'report date is required' using errcode = 'check_violation';
  end if;

  perform pg_advisory_xact_lock(80801);  -- same lock as every balance-affecting write (FINANCE_LOCK)
  select coalesce(sum(e.amount), 0) into v_total from app.parse_report_expenses(p_expenses, p_report_date) e;

  insert into public.daily_reports
    (report_date, branch_id, total_sales, order_count, cash_received, pos_amount, bank_transfer, link_payment,
     shop_expenses, shop_savings, created_by)
  values (p_report_date, p_branch_id, p_total_sales, p_order_count, p_cash, p_pos, p_bank, p_link,
          v_total + p_shop_savings, p_shop_savings, v_uid)
  returning id into v_id;

  perform app.write_report_expenses(v_id, p_branch_id, p_report_date, p_expenses);
  perform app.check_report_expense_totals(v_id);
  return v_id;
end $$;

-- Owner correction: full replacement of the report AND its expense lines. Old lines and the supplier rows they created
-- are voided (never edited); replacements are inserted. One transaction, one audit reason.
drop function if exists public.edit_daily_report(uuid, date, uuid, numeric, integer, numeric, numeric, numeric,
  numeric, numeric, numeric, jsonb, text);
create or replace function public.edit_daily_report(
  p_id uuid, p_report_date date, p_branch_id uuid, p_total_sales numeric, p_order_count integer,
  p_cash numeric, p_pos numeric, p_bank numeric, p_link numeric, p_shop_savings numeric, p_expenses jsonb, p_reason text)
returns void
language plpgsql security definer set search_path = public, pg_temp
as $$
declare
  old_row public.daily_reports; v_total numeric; v_reason text := btrim(coalesce(p_reason, ''));
begin
  if not app.is_owner() then
    raise exception 'owner only' using errcode = 'insufficient_privilege';
  end if;
  if char_length(v_reason) < 3 then
    raise exception 'a reason of at least 3 characters is required' using errcode = 'check_violation';
  end if;
  perform pg_advisory_xact_lock(80801);
  select * into old_row from public.daily_reports where id = p_id and voided_at is null for update;
  if not found then
    raise exception 'daily report % not found or voided', p_id using errcode = 'no_data_found';
  end if;
  if p_branch_id is distinct from old_row.branch_id
     and not exists (select 1 from public.branches b where b.id = p_branch_id and b.is_active) then
    raise exception 'unknown or inactive branch' using errcode = 'foreign_key_violation';
  end if;

  select coalesce(sum(e.amount), 0) into v_total from app.parse_report_expenses(p_expenses, p_report_date) e;

  perform set_config('app.audit_reason', v_reason, true);
  update public.supplier_entries
     set voided_at = now(), voided_by = auth.uid(), void_reason = 'Daily report corrected: ' || v_reason
   where voided_at is null
     and source_expense_id in (select id from public.daily_report_expenses where report_id = p_id and voided_at is null);
  update public.daily_report_expenses
     set voided_at = now(), voided_by = auth.uid(), void_reason = 'Daily report corrected: ' || v_reason
   where report_id = p_id and voided_at is null;

  update public.daily_reports
     set report_date = p_report_date, branch_id = p_branch_id, total_sales = p_total_sales,
         order_count = p_order_count, cash_received = p_cash, pos_amount = p_pos, bank_transfer = p_bank,
         link_payment = p_link, shop_expenses = v_total + p_shop_savings, shop_savings = p_shop_savings
   where id = p_id;

  perform app.write_report_expenses(p_id, p_branch_id, p_report_date, p_expenses);
  perform app.check_report_expense_totals(p_id);
  perform app.assert_no_negative_supplier();
end $$;

create or replace function public.void_daily_report(p_id uuid, p_reason text)
returns void
language plpgsql security definer set search_path = public, pg_temp
as $$
begin
  if not app.is_owner() then
    raise exception 'owner only' using errcode = 'insufficient_privilege';
  end if;
  if p_reason is null or btrim(p_reason) = '' then
    raise exception 'a void reason is required' using errcode = 'check_violation';
  end if;
  perform pg_advisory_xact_lock(80801);
  perform set_config('app.audit_reason', p_reason, true);
  update public.daily_reports
     set voided_at = now(), voided_by = auth.uid(), void_reason = p_reason
   where id = p_id and voided_at is null;
  if not found then
    raise exception 'daily report % not found or already voided', p_id using errcode = 'no_data_found';
  end if;
  update public.supplier_entries
     set voided_at = now(), voided_by = auth.uid(), void_reason = 'Daily report voided: ' || p_reason
   where voided_at is null
     and source_expense_id in (select id from public.daily_report_expenses where report_id = p_id and voided_at is null);
  update public.daily_report_expenses
     set voided_at = now(), voided_by = auth.uid(), void_reason = 'Daily report voided: ' || p_reason
   where report_id = p_id and voided_at is null;
  perform app.assert_no_negative_supplier();
end $$;

-- Branch Users cannot read the owner-only suppliers table. This exposes ONLY id and name of ACTIVE suppliers, to any
-- active signed-in user, so the daily report form can offer them. No balances, terms or notes.
create or replace function public.report_supplier_options()
returns table (id uuid, name text)
language sql stable security definer set search_path = public, pg_temp
as $$
  select s.id, s.name from public.suppliers s
   where s.is_active
     and exists (select 1 from public.profiles p where p.user_id = auth.uid() and p.is_active)
   order by lower(s.name)
$$;

-- ------------------------------------------------------------------------------------------------ 8. privileges
revoke all on function app.parse_report_expenses(jsonb, date), app.write_report_expenses(uuid, uuid, date, jsonb),
  app.assert_no_negative_supplier(), app.check_report_expense_totals(uuid), app.dr_reconcile_trg()
  from public, anon, authenticated;
revoke all on function public.submit_daily_report(uuid, date, numeric, integer, numeric, numeric, numeric, numeric, numeric, jsonb),
  public.edit_daily_report(uuid, date, uuid, numeric, integer, numeric, numeric, numeric, numeric, numeric, jsonb, text),
  public.void_daily_report(uuid, text), public.report_supplier_options()
  from public, anon, authenticated;
grant execute on function
  public.submit_daily_report(uuid, date, numeric, integer, numeric, numeric, numeric, numeric, numeric, jsonb),
  public.edit_daily_report(uuid, date, uuid, numeric, integer, numeric, numeric, numeric, numeric, numeric, jsonb, text),
  public.void_daily_report(uuid, text), public.report_supplier_options()
  to authenticated;

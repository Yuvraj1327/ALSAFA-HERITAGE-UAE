-- 0022_report_edit_inactive_supplier.sql
-- Fix found in the pre-production code review of 0021: the Owner could not correct (or even re-save) a daily report whose
-- supplier had been deactivated AFTER the report was filed, because every row was re-validated as "supplier must be active".
-- Rule now: a deactivated supplier is accepted on rows of the SAME report that already used it; it is still refused on new
-- reports and on rows that never used it. Nothing else changes.
--
-- Additive-safe: three functions are replaced (same behaviour otherwise); no table, column or row is touched.
-- Requires 0021. Apply as one script (single transaction).

drop function if exists app.parse_report_expenses(jsonb, date);

create or replace function app.parse_report_expenses(p_expenses jsonb, p_date date, p_report uuid default null)
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
      -- A supplier deactivated AFTER this report used it may stay on the report's existing rows (so the Owner can still
      -- correct the report); an inactive supplier is still refused for new reports and for rows that never used it.
      if not v_sup_active and not (p_report is not null and exists (
           select 1 from public.daily_report_expenses x where x.report_id = p_report and x.supplier_id = v_sup)) then
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


create or replace function app.write_report_expenses(p_report uuid, p_branch uuid, p_date date, p_expenses jsonb)
returns void
language plpgsql set search_path = public, pg_temp
as $$
declare l record; v_line uuid; v_branch_name text; v_ref text;
begin
  select name into v_branch_name from public.branches where id = p_branch;
  for l in select * from app.parse_report_expenses(p_expenses, p_date, p_report) order by line_no loop
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

  select coalesce(sum(e.amount), 0) into v_total from app.parse_report_expenses(p_expenses, p_report_date, p_id) e;

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


revoke all on function app.parse_report_expenses(jsonb, date, uuid), app.write_report_expenses(uuid, uuid, date, jsonb)
  from public, anon, authenticated;

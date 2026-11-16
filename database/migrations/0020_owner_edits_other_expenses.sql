-- 0020_owner_edits_other_expenses.sql
-- Additive only. Touches no existing row's data, drops nothing, relaxes no policy.
--
--   1. daily_reports.other_expenses: itemised "Other" shop expenses ({description, amount, notes}). It is a BREAKDOWN of
--      the existing daily_reports.shop_expenses total (which therefore already includes it): there is no second
--      ledger and nothing is summed twice. A trigger enforces shape and "items <= shop_expenses".
--   2. public.edit_daily_report(): the ONLY way to change a submitted report. Owner only (checked again here, in
--      the database), reason required, row locked, duplicate branch/date rejected by dr_branch_date_active_uq.
--      The existing audit trigger records old values, new values, actor, time and the reason (app.audit_reason).
--      daily_reports still has NO update grant for anyone, so Branch Users can never edit or delete.
--   3. public.record_user_admin_event(): audit entry for the two user changes that do not live in `profiles`
--      (email and password reset, both held by Supabase Auth). It stores the event name and, for email, the old/new
--      address. It has no parameter that could carry a password.
--   4. app.profile_display_name(): the author's display name only, so a Branch User's own-branch report/PDF can show
--      "Prepared by" even for a colleague (profiles RLS hides other people's rows).

-- 1 ---------------------------------------------------------------------------------------------------------------
alter table public.daily_reports
  add column other_expenses jsonb not null default '[]'::jsonb;
alter table public.daily_reports
  add constraint dr_other_is_array check (jsonb_typeof(other_expenses) = 'array');

create or replace function app.dr_validate_other() returns trigger
language plpgsql
as $$
declare
  item  jsonb;
  total numeric := 0;
begin
  if jsonb_array_length(new.other_expenses) > 50 then
    raise exception 'other_expenses: at most 50 items' using errcode = 'check_violation';
  end if;
  for item in select value from jsonb_array_elements(new.other_expenses) loop
    if jsonb_typeof(item) <> 'object'
       or exists (select 1 from jsonb_object_keys(item) k where k not in ('description', 'amount', 'notes')) then
      raise exception 'other_expenses: each item must be {description, amount, notes}' using errcode = 'check_violation';
    end if;
    if char_length(btrim(coalesce(item ->> 'description', ''))) not between 1 and 200 then
      raise exception 'other_expenses: description is required (max 200 characters)' using errcode = 'check_violation';
    end if;
    if char_length(coalesce(item ->> 'notes', '')) > 500 then
      raise exception 'other_expenses: notes are limited to 500 characters' using errcode = 'check_violation';
    end if;
    if coalesce(item ->> 'amount', '') !~ '^[0-9]{1,12}(\.[0-9]{1,2})?$' or (item ->> 'amount')::numeric <= 0 then
      raise exception 'other_expenses: amount must be a positive AED amount' using errcode = 'check_violation';
    end if;
    total := total + (item ->> 'amount')::numeric;
  end loop;
  if total > new.shop_expenses then
    raise exception 'other_expenses total % exceeds shop_expenses %', total, new.shop_expenses
      using errcode = 'check_violation';
  end if;
  return new;
end $$;

revoke all on function app.dr_validate_other() from public, anon, authenticated;  -- trigger function: no direct callers
create trigger dr_validate_other before insert or update of other_expenses, shop_expenses on public.daily_reports
  for each row execute function app.dr_validate_other();

-- 2 ---------------------------------------------------------------------------------------------------------------
create or replace function public.edit_daily_report(
  p_id uuid, p_report_date date, p_branch_id uuid, p_total_sales numeric, p_order_count integer,
  p_cash numeric, p_pos numeric, p_bank numeric, p_link numeric,
  p_shop_expenses numeric, p_shop_savings numeric, p_other jsonb, p_reason text)
returns void
language plpgsql security definer set search_path = public, pg_temp
as $$
declare
  old_row public.daily_reports;
begin
  if not app.is_owner() then
    raise exception 'owner only' using errcode = 'insufficient_privilege';
  end if;
  if p_reason is null or char_length(btrim(p_reason)) < 3 then
    raise exception 'a reason of at least 3 characters is required' using errcode = 'check_violation';
  end if;
  select * into old_row from public.daily_reports where id = p_id and voided_at is null for update;
  if not found then
    raise exception 'daily report % not found or voided', p_id using errcode = 'no_data_found';
  end if;
  if p_branch_id is distinct from old_row.branch_id
     and not exists (select 1 from public.branches b where b.id = p_branch_id and b.is_active) then
    raise exception 'unknown or inactive branch' using errcode = 'foreign_key_violation';
  end if;

  perform set_config('app.audit_reason', btrim(p_reason), true);
  update public.daily_reports
     set report_date = p_report_date, branch_id = p_branch_id, total_sales = p_total_sales,
         order_count = p_order_count, cash_received = p_cash, pos_amount = p_pos, bank_transfer = p_bank,
         link_payment = p_link, shop_expenses = p_shop_expenses, shop_savings = p_shop_savings,
         other_expenses = coalesce(p_other, '[]'::jsonb)
   where id = p_id;
end $$;

revoke all on function public.edit_daily_report(uuid, date, uuid, numeric, integer, numeric, numeric, numeric,
  numeric, numeric, numeric, jsonb, text) from public, anon, authenticated;
grant execute on function public.edit_daily_report(uuid, date, uuid, numeric, integer, numeric, numeric, numeric,
  numeric, numeric, numeric, jsonb, text) to authenticated;

-- 3 ---------------------------------------------------------------------------------------------------------------
create or replace function public.record_user_admin_event(
  p_user uuid, p_event text, p_old_email text, p_new_email text, p_reason text)
returns void
language plpgsql security definer set search_path = public, pg_temp
as $$
declare
  v_name text;
begin
  if not app.is_owner() then
    raise exception 'owner only' using errcode = 'insufficient_privilege';
  end if;
  if p_event not in ('password_reset', 'email_changed') then
    raise exception 'unknown user admin event' using errcode = 'check_violation';
  end if;
  select full_name into v_name from public.profiles where user_id = p_user and role = 'branch';
  if not found then
    raise exception 'branch user % not found', p_user using errcode = 'no_data_found';
  end if;
  insert into public.audit_log (actor_user_id, action, table_name, record_id, old_values, new_values, reason)
  values (auth.uid(), 'UPDATE', 'profiles', p_user::text,
          case when p_event = 'email_changed'
               then jsonb_build_object('full_name', v_name, 'email', left(p_old_email, 254))
               else jsonb_build_object('full_name', v_name) end,
          case when p_event = 'email_changed'
               then jsonb_build_object('full_name', v_name, 'email', left(p_new_email, 254))
               else jsonb_build_object('full_name', v_name, 'event', 'Password reset by Owner') end,
          nullif(left(btrim(coalesce(p_reason, '')), 500), ''));
end $$;

revoke all on function public.record_user_admin_event(uuid, text, text, text, text) from public, anon, authenticated;
grant execute on function public.record_user_admin_event(uuid, text, text, text, text) to authenticated;

-- 4 ---------------------------------------------------------------------------------------------------------------
create or replace function app.profile_display_name(p_user uuid) returns text
language sql stable security definer set search_path = public, pg_temp
as $$
  select p.full_name from public.profiles p
   where p.user_id = p_user
     and exists (select 1 from public.profiles me where me.user_id = auth.uid() and me.is_active)
$$;
revoke all on function app.profile_display_name(uuid) from public, anon, authenticated;
grant execute on function app.profile_display_name(uuid) to authenticated;

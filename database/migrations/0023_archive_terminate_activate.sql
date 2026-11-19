-- 0023_archive_terminate_activate.sql
-- Archive / terminate / activate actions from the "delete options" audit. ADDITIVE: no row is deleted or rewritten.
--
--   1. tasks              : archive columns (Owner archives / restores); hard DELETE removed for everyone (privilege + policy);
--                           archived tasks are read-only until restored; archiving / restoring works even if the assignee was deactivated.
--   2. rental_agreements  : termination columns (date + reason + who/when); termination is final; cheques are never touched.
--   3. customers          : archive columns; archived customers are read-only, cannot get NEW orders; orders/payments are untouched.
--   4. branches           : public.branch_dependencies() and public.set_branch_active(): activate / deactivate with the
--                           operational-dependency checks done atomically inside the database (Owner only).
--
-- Apply after 0022 as ONE script (single transaction). Safe to re-run? No: the first statement that already exists fails
-- and the whole script rolls back, leaving the database unchanged.

-- ------------------------------------------------------------------------------------------------ 1. tasks
alter table public.tasks
  add column archived_at     timestamptz,
  add column archived_by     uuid,
  add column archive_reason  text;
alter table public.tasks
  add constraint tasks_archive_chk check ((archived_at is null) = (archive_reason is null)),
  add constraint tasks_archive_reason_len check (archive_reason is null or char_length(archive_reason) between 3 and 500);
create index tasks_live_idx on public.tasks (due_date) where archived_at is null;

-- No hard delete for anyone: tasks are archived, history is kept.
revoke delete on public.tasks from authenticated;
drop policy if exists tasks_delete_owner on public.tasks;

-- Same function as 0015 plus: (a) an archived task cannot be changed (only restored); (b) the existing "assignee must be an
-- active user" rule is NOT applied to the archive / restore step itself, so a task whose assignee was deactivated can still be
-- archived and restored. Every other insert / update behaves exactly as before.
create or replace function app.tasks_before() returns trigger
language plpgsql
set search_path = pg_catalog, public
as $$
begin
  if tg_op = 'UPDATE' and old.archived_at is not null and new.archived_at is not null then
    raise exception 'an archived task cannot be changed; restore it first' using errcode = 'check_violation';
  end if;
  if tg_op = 'INSERT' then
    if new.status = 'Done' then
      new.completed_at := coalesce(new.completed_at, now());
      new.completed_by := coalesce(new.completed_by, auth.uid());
    else
      new.completed_at := null; new.completed_by := null;
    end if;
  else
    if new.status = 'Done' and old.status <> 'Done' then
      new.completed_at := now(); new.completed_by := auth.uid();
    elsif new.status = 'Done' then
      new.completed_at := old.completed_at; new.completed_by := old.completed_by;  -- completion is never rewritten
    else
      new.completed_at := null; new.completed_by := null;                          -- re-opened
    end if;
  end if;
  if new.assignee_user_id is not null
     and (tg_op = 'INSERT' or new.archived_at is not distinct from old.archived_at)
     and not exists (select 1 from public.profiles p where p.user_id = new.assignee_user_id and p.is_active) then
    raise exception 'tasks can only be assigned to an active user' using errcode = 'check_violation';
  end if;
  return new;
end $$;

-- ------------------------------------------------------------------------------------------------ 2. rental agreements
alter table public.rental_agreements
  add column terminated_on       date,
  add column terminated_at       timestamptz,
  add column terminated_by       uuid,
  add column termination_reason  text;
alter table public.rental_agreements
  add constraint ra_termination_chk check (
    (terminated_on is null) = (termination_reason is null)
    and (terminated_on is null or status = 'Terminated')),
  add constraint ra_termination_reason_len check (termination_reason is null or char_length(termination_reason) between 3 and 500);

-- Termination is final: a terminated agreement cannot be reopened and its termination record cannot be rewritten.
create or replace function app.ra_terminated_final() returns trigger
language plpgsql
as $$
begin
  if old.status = 'Terminated' and new.status <> 'Terminated' then
    raise exception 'a terminated rental agreement cannot be reopened' using errcode = 'check_violation';
  end if;
  if old.terminated_on is not null
     and (new.terminated_on is distinct from old.terminated_on
          or new.termination_reason is distinct from old.termination_reason) then
    raise exception 'the termination record cannot be changed' using errcode = 'check_violation';
  end if;
  return new;
end $$;
revoke all on function app.ra_terminated_final() from public, anon, authenticated;
create trigger ra_terminated_final before update on public.rental_agreements
  for each row execute function app.ra_terminated_final();

-- ------------------------------------------------------------------------------------------------ 3. customers
alter table public.customers
  add column archived_at     timestamptz,
  add column archived_by     uuid,
  add column archive_reason  text;
alter table public.customers
  add constraint customers_archive_chk check ((archived_at is null) = (archive_reason is null)),
  add constraint customers_archive_reason_len check (archive_reason is null or char_length(archive_reason) between 3 and 500);
create index customers_live_idx on public.customers (branch_id, lower(name)) where archived_at is null;

-- An archived customer is read-only (details cannot change) until restored; archiving / restoring itself is allowed.
create or replace function app.customers_archived_guard() returns trigger
language plpgsql
as $$
begin
  if old.archived_at is not null and new.archived_at is not null
     and (to_jsonb(new) - 'updated_at' - 'updated_by') is distinct from (to_jsonb(old) - 'updated_at' - 'updated_by') then
    raise exception 'an archived customer cannot be changed; restore it first' using errcode = 'check_violation';
  end if;
  return new;
end $$;
revoke all on function app.customers_archived_guard() from public, anon, authenticated;
create trigger customers_archived_guard before update on public.customers
  for each row execute function app.customers_archived_guard();

-- No NEW order for an archived customer (existing orders and their payments stay fully usable).
create or replace function app.orders_customer_active() returns trigger
language plpgsql set search_path = public, pg_temp
as $$
begin
  if exists (select 1 from public.customers c where c.id = new.customer_id and c.archived_at is not null) then
    raise exception 'this customer is archived; restore the customer before creating a new order' using errcode = 'check_violation';
  end if;
  return new;
end $$;
revoke all on function app.orders_customer_active() from public, anon, authenticated;
create trigger orders_customer_active before insert on public.orders
  for each row execute function app.orders_customer_active();

-- ------------------------------------------------------------------------------------------------ 4. branches
alter table public.branches
  add column deactivated_at      timestamptz,
  add column deactivated_by      uuid,
  add column deactivation_reason text;

-- What still depends on a branch. `blocking` rows must be zero before the branch can be deactivated.
create or replace function public.branch_dependencies(p_branch uuid)
returns table (key text, label text, item_count bigint, blocking boolean)
language plpgsql stable security definer set search_path = public, pg_temp
as $$
declare
  v_cash numeric := 0;
begin
  if not app.is_owner() then
    raise exception 'owner only' using errcode = 'insufficient_privilege';
  end if;
  if not exists (select 1 from public.branches where id = p_branch) then
    raise exception 'branch not found' using errcode = 'no_data_found';
  end if;
  begin
    select closing into v_cash from public.app_branch_cash(app.dubai_today()) where branch_id = p_branch;
  exception when others then
    v_cash := 0;   -- opening balances not configured yet: there is no cash to account for
  end;
  return query
    select 'active_users'::text, 'Active Branch Users assigned to this branch'::text,
           (select count(*) from public.profiles p where p.branch_id = p_branch and p.is_active), true
    union all
    select 'open_orders', 'Orders not yet delivered or cancelled',
           (select count(*) from public.orders o where o.branch_id = p_branch and o.status not in ('Delivered', 'Cancelled')), true
    union all
    select 'active_agreements', 'Rental agreements still active (renew or terminate them first)',
           (select count(*) from public.rental_agreements a where a.branch_id = p_branch and a.status = 'Active'), true
    union all
    select 'open_tasks', 'Open tasks for this branch (complete or archive them first)',
           (select count(*) from public.tasks t where t.branch_id = p_branch and t.status <> 'Done' and t.archived_at is null), true
    union all
    select 'branch_cash', 'Cash still held by this branch (transfer it first): AED ' || to_char(coalesce(v_cash, 0), 'FM999,999,999,990.00'),
           (case when coalesce(v_cash, 0) <> 0 then 1 else 0 end)::bigint, true
    union all
    select 'last_active_branch', 'This is the only active branch',
           (case when (select count(*) from public.branches b where b.is_active and b.id <> p_branch) = 0 then 1 else 0 end)::bigint, true;
end $$;

-- Owner-only, atomic: re-checks the dependencies under a row lock, then flips the flag with an audited reason.
create or replace function public.set_branch_active(p_id uuid, p_active boolean, p_reason text)
returns void
language plpgsql security definer set search_path = public, pg_temp
as $$
declare
  v_reason text := btrim(coalesce(p_reason, ''));
  v_blockers text;
  v_current boolean;
begin
  if not app.is_owner() then
    raise exception 'owner only' using errcode = 'insufficient_privilege';
  end if;
  if char_length(v_reason) < 3 then
    raise exception 'a reason of at least 3 characters is required' using errcode = 'check_violation';
  end if;
  select is_active into v_current from public.branches where id = p_id for update;
  if not found then
    raise exception 'branch not found' using errcode = 'no_data_found';
  end if;
  if v_current = p_active then
    raise exception 'the branch is already %', case when p_active then 'active' else 'inactive' end
      using errcode = 'check_violation';
  end if;
  if not p_active then
    select string_agg(d.label || ' (' || d.item_count || ')', '; ') into v_blockers
      from public.branch_dependencies(p_id) d where d.blocking and d.item_count > 0;
    if v_blockers is not null then
      raise exception 'branch_has_dependencies: %', v_blockers using errcode = 'check_violation';
    end if;
  end if;
  perform set_config('app.audit_reason', v_reason, true);
  update public.branches
     set is_active = p_active,
         deactivated_at = case when p_active then null else now() end,
         deactivated_by = case when p_active then null else auth.uid() end,
         deactivation_reason = case when p_active then null else v_reason end
   where id = p_id;
end $$;

revoke all on function public.branch_dependencies(uuid), public.set_branch_active(uuid, boolean, text)
  from public, anon, authenticated;
grant execute on function public.branch_dependencies(uuid), public.set_branch_active(uuid, boolean, text)
  to authenticated;
-- the branch audit trigger (0006) already records old/new values and the reason; the new columns add no new privilege:
-- `authenticated` still has UPDATE on (name, sort_order, is_active) only.

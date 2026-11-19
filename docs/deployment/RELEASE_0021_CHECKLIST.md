# Release checklist — itemised shop expenses (migration 0021)

Scope: database migration `0021`, backend (Railway), frontend (Vercel — the repository has a `.vercel` link and `frontend/vercel.json`;
the steps are the same for any static host). **Nothing here deploys automatically; every step is manual.**
The repository contains no Railway configuration, so every Railway setting below is set in the Railway dashboard.

> This release is a **coordinated release**: database, backend and frontend change together and are **not** compatible with
> each other in mixed versions (see §2). Plan a short maintenance window, outside branch reporting hours.

## 1. What changes

| Layer | Change | Backward compatible? |
|---|---|---|
| Database | new table `daily_report_expenses`, `supplier_entries.source_expense_id`, new/replaced functions (`submit_daily_report`, `edit_daily_report` **new signature**, `void_daily_report`, `report_supplier_options`), ledger rewritten, `shop_expenses = Σ rows + savings` enforced at commit | No (see §2) |
| Backend | `POST/PUT /daily-reports` take `expenses[]` (the old `shop_expenses`/`other_expenses` fields are rejected with 422); `GET /expenses/other` removed → `/expenses/shop`; new fields on reports/dashboard/analytics | No |
| Frontend | new report form, Shop expenses tab, dashboard cards, report filters | No |

## 2. Compatibility matrix (why the order matters)

| Database | Backend | Frontend | Result (measured unless marked *inferred*) |
|---|---|---|---|
| old | old | old | today |
| **new** | **old** | old | **Reads all work** (reports list, dashboard, branch & date view, head-office expenses, `/expenses/other`, suppliers, analytics, branch-user list: 200). A report with **no expenses** can still be submitted (201) and voided (204). Submitting **with expenses** is refused (422, nothing written) because the old backend sends a total without rows. Old "Other" items are refused (422). **Owner "Edit report" fails with HTTP 500** (the old function signature no longer exists). Measured by running the previous commit's backend against the migrated database. |
| new | new | **old** | the old page sends the retired payload → 422 on submit/edit (`shop_expenses`/`other_expenses` are rejected; tested); its "Other Expenses" tab errors (`/expenses/other` no longer exists). Other pages work. A browser tab opened before the release must be refreshed. |
| old | new | any | the new backend calls functions that do not exist yet → submissions fail. *Inferred* (not run). **Never deploy the backend first.** |
| new | new | new | target (all verification below was run in this combination) |

Required order: **backup → migrate → backend → frontend → smoke test**. The unavoidable gap is the time between the
migration and the backend going live (minutes). Do it when no branch is submitting.

## 3. Before the window

1. **Identify the production schema level.** There is no migration table. Run read-only checks in the Supabase SQL editor:
   ```sql
   select to_regclass('public.user_preferences')            as m0019,   -- null => 0019 missing
          (select count(*) from information_schema.columns where table_name='daily_reports' and column_name='other_expenses') as m0020,  -- 0 => 0020 missing
          to_regclass('public.daily_report_expenses')        as m0021;   -- not null => already applied, STOP
   ```
   Apply any missing earlier files (0017…0020) **in order** before 0021. (`docs/deployment/DEPLOYMENT_GUIDE.md` still lists only 0001–0016.)
2. **Back up**: Supabase → Database → Backups (take a manual backup / note the PITR timestamp), and export a `pg_dump` of the public schema + data.
3. **Rehearse on a copy**: restore that backup into a *separate* throw-away Supabase project (or local Postgres 15+), apply 0021 there, run the snapshot script before/after (must be identical) and run §5 on it. Do not skip: it is the only test on real data shapes. (`database/verification/rehearse_0021.py` rehearses the migration on a large *synthetic* database; it does not replace this step.)
4. **Review the Python version**: `backend/pyproject.toml` requires Python ≥ 3.12. In Railway set the Python version explicitly to 3.12 (e.g. variable `RAILPACK_PYTHON_VERSION=3.12` / `NIXPACKS_PYTHON_VERSION=3.12`, depending on the builder) and build from `requirements.lock`.
5. **Stop automatic deploys of the backend** for the window (Railway → service → Settings → disable auto-deploy / "wait for CI"), so merging code cannot deploy it before the migration. Merge/deploy the backend deliberately in step 3 below.
6. **Do not change Supabase Auth, the `app_api` password or any environment variable** in this release. No new variables are needed.
7. Tell the branch users the date/time. Confirm nobody is submitting.
8. Prepare the previous Railway deployment and previous Vercel deployment for rollback (both keep history; note their ids).

## 4. The window (in this order)

1. **Baseline snapshot** — run `database/verification/0021_balance_snapshot.sql` (read-only; its sensitivity was proven: a 0.01 change is detected) (replace `<OWNER_USER_ID>`), save the output as `before.txt`. Also note `select count(*) from public.daily_reports;`.
2. **Apply the migration** — paste the *whole* file `database/migrations/0021_itemised_shop_expenses.sql` in one SQL-editor run (or `psql --single-transaction -f`). One script = one transaction: any failure, even at the last statement, rolls everything back (tested).
   * If it stops with *"These daily reports do not reconcile"*, nothing changed. Fix the listed reports (Owner edit/void on the OLD app) and re-run.
   * Running it twice is safe: the second run fails on "relation already exists" and changes nothing.
3. **Snapshot again** — run the same script, save `after.txt`. `diff before.txt after.txt` must be **empty**. Any difference ⇒ **STOP**, restore the backup, do not deploy.
4. **Deploy the backend (Railway)** — deploy the new commit. Settings: root directory `backend`; build `pip install -r requirements.lock`; start `uvicorn app.main:app --host 0.0.0.0 --port $PORT`; health check path `/api/v1/health`. Wait for `GET /api/v1/health` = ok **and** `GET /api/v1/health/ready` = ok. Check the deploy log for errors.
5. **Quick API check (read-only)** with an Owner token: `GET /api/v1/daily-reports/supplier-options`, `GET /api/v1/expenses/shop`, `GET /api/v1/dashboard/owner` → all 200.
6. **Deploy the frontend (Vercel)** — promote the new build. Build `npm run build`, output `dist`, env `VITE_API_BASE_URL`, `VITE_SUPABASE_URL`, `VITE_SUPABASE_ANON_KEY` unchanged. (`CORS_ORIGINS` unchanged.)
7. **Smoke test** (§5). Then re-enable normal auto-deploys.

## 5. Smoke test after deploy

Read-only first (safe on production):
* Owner: Dashboard loads; "Shop expenses & suppliers" cards show; Cash & Banks balances equal the `after.txt` values (same day); Suppliers list totals equal `after.txt`; Reports → Expenses & Savings → *Shop expenses by category* shows historical amounts under **Other (Legacy)** and their total equals the old shop-expenses-excluding-savings total; Daily Reports history opens a historical report's *Expense Items* (legacy rows).
* Branch user: sees only their branch; `Expenses`, `Suppliers`, `Reports` show *Access denied*.
* Exports: download a Daily Reports PDF for one past day and a CSV/Excel of *Shop expense items*; spot-check against the screen.

Write test (creates production data — only if the Owner explicitly agrees; otherwise rely on the rehearsal copy):
* Submit one clearly named test report on a quiet day for one branch (one Paid-Cash row, one Unpaid supplier row), confirm the supplier due and balances, then **Void** it with the reason "release smoke test". The void is itself audited.

## 6. Rollback

* **Before any new-format report exists** (nobody submitted since step 2): redeploy the previous backend and frontend, then restore the pre-migration backup (this discards nothing but the migration). Verify with the snapshot script.
* **After new-format reports exist**: do **not** restore the backup (it would delete those reports and the supplier entries they created). There is no in-place down-migration. Forward-fix instead; if a code defect, redeploy the previous *frontend and backend only if* the database still accepts them — it does not (see §2) — so fix forward.
* Keep the `before.txt`/`after.txt` and the backup for at least a month.

## 7. After release

* Watch Railway logs for `unmapped database error`, 5xx and `negative_balance` for the first days.
* Branch users: "refresh the page before submitting today's report" (a stale tab sends the old payload and gets a clear 422).
* Historical reports show their old expenses as **Other (Legacy)**; if the Owner edits one, its legacy rows become **Other** (audited).

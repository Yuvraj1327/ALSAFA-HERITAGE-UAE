# TEST_RESULTS — Step 2

**Actually executed** on 2026-11-08 against a throwaway PostgreSQL 16.15 server (local, disposable cluster). Command: `pytest -v` in `database/tests`.
**Result: 111 passed, 0 failed, 0 skipped (≈1.6 s).** A fresh database was created, the Supabase stub and migrations 0001-0009 applied from scratch, and the database dropped afterwards.

## Results by required area

| # | Area | Tests | Result |
|---|---|---|---|
| 1 | Fresh migration (14 tables, RLS on all, auth untouched, restricted API role, pinned search_path, no PUBLIC/anon execute, no passport/Emirates ID columns) | 6 | pass |
| 2 | Ten branch seed (names, order), 7 categories, idempotent re-seed, no fake data | 4 | pass |
| 3 | Unique branch names | 2 | pass |
| 4 | Duplicate daily report prevention incl. **real two-connection concurrent insert** (second waits, then unique violation) and resubmission after void | 4 | pass |
| 5 | Negative amounts, sales must be > 0 | 10 | pass |
| 6 | Savings greater than expenses rejected | 2 | pass |
| 7 | Future-dated report rejected (Dubai date), employee future leave rejected | 3 | pass |
| 8 | Owner access (all branches, owner-only tables, last-owner lock, profile consistency) | 5 | pass |
| 9 | Branch user own-branch access, created_by forgery blocked | 5 | pass |
| 10 | Cross-branch denial, inactive user, user without profile | 5 | pass |
| 11 | Branch (and owner) update/delete denial, privilege-escalation attempts, void rules | 6 | pass |
| 12 | Anonymous denial (read 15 relations, write, functions) | 20 | pass |
| 13 | Employee/owner-only privacy | 3 | pass |
| 14 | Financial integrity (sources, single-row settings, column-limited edits, no hard delete, FK restrict, void) | 7 | pass |
| 15 | Rental agreement + cheque consistency (atomic create, rollback, 1-12 cheques, duplicates, renewal rules, history, double renewal) | 12 | pass |
| 16 | Cheque status/bank/amount validation, cleared_at maintenance, task status | 4 | pass |
| 17 | Audit creation, actor and old/new values, void reason, append-only, no API writes | 6 | pass |
| 18 | Balance verification against hand-computed golden case | 7 | pass |

## Mutation check (do the RLS tests really bite?)
Temporarily changed the `daily_reports` SELECT policy to `using (true)`: 3 tests failed as expected (cross-branch read, inactive user, user without profile). The migration was restored and the full suite re-run: 111 passed.

## Golden case working (invented test data; as of 2026-10-02, opening date 2026-09-30, tracking starts 2026-10-01, receiving bank ENBD)
Opening: ENBD 10000, ADCB 5000, POS pending 700, savings b/f 300, branch cash MAIN 1000 + JERNAS 500 = 1500.
Reports counted (>= 2026-10-01): MAIN 10-01 (cash 400, POS 300, transfer 200, link 100, shop exp 150, savings 50); JERNAS 10-01 (cash 100, POS 100, exp 40); MAIN 10-02 (cash 500, POS 200, transfer 100, exp 100, savings 20). The 09-30 report is ignored.
- Cash = 1500 + (400+100+500) - (150+40+100) - 60 (HO paid from cash) = 1500 + 1000 - 290 - 60 = **2150**
- Bank receipts = 600 + 100 + 300 = 1000
- ENBD = 10000 + 1000 - 200 (HO) - 300 (cleared cheque A) = **10500**
- ADCB = 5000 - 80 (HO) - 120 (cleared cheque B) = **4800** (cleared cheque D, 77, is dated 20 Oct and counts from then: 4723)
- Total = 2150 + 10500 + 4800 = **17450**; Savings = 300 + (50+20) + 80 = **450**.
Ignored by design: report and HO expense before start date, a voided HO expense (999), a pending cheque.
Reconciliation: MAIN 10-01 matches; JERNAS 10-01 received 200 vs sales 500 -> Diff 300; MAIN 10-02 received 800 -> matches. Missing reports 10-01..10-02: 2 x 10 - 3 = 17.

## NOT tested / limitations (do not treat as verified)
- Not run on a real Supabase project; `auth.users`, `auth.uid()` and roles were **stubbed** to mimic Supabase. Supabase's actual claim handling, PostgREST, and pooler (transaction mode) behaviour are untested.
- `app_api` login and the `SET LOCAL ROLE authenticated` request flow were tested by switching role in a superuser session, not through a real backend connection (Step 4).
- Calculations were checked against hand-computed values from the PDF's text rules, **not** against the original `app_balances()` (SQL file unavailable) **[VERIFY]**.
- Performance/index usage with realistic data volumes not measured.
- Backup/restore, Alembic wrapping and downgrade scripts: Step 4/12.

## Migration 0021 (itemised shop expenses) — run against a real PostgreSQL 16.2

| Suite | Result |
|---|---|
| `database/tests` (incl. new `test_itemised_expenses.py`: 6 categories, paid/unpaid/partial, supplier due + payment, no double count, edit/void reconcile, negative-supplier refusal, RLS/identity/branch checks, frozen legacy JSON, historical migration, stop-on-bad-history, late-failure rollback) | 223 passed, 2 failed (pre-existing, unrelated: `test_audit_does_not_store_secrets`, `test_preferences_are_private_per_user`) |
| `backend/tests` (incl. new `test_itemised_expenses_api.py`, 26 tests) | 691 passed, 4 failed (pre-existing, unrelated), 1 skipped (`TEST_DATABASE_URL` not set; passes when set) |
| `frontend` (`vitest`, `tsc -b`, `eslint`, `prettier --check`, `vite build`) | 350 passed; typecheck, lint, format and build clean |

Baseline before the change (same machine, same database): backend 658 passed / 4 failed / 1 skipped; database 171 passed / 3 failed (one of those, the table-list check, was stale for `user_preferences` and is fixed together with the new table).
Not run: `e2e/run_e2e.py` (needs live servers; its payloads were updated but it was not executed).


## Pre-production verification of 0021 (second pass)

| Check | Result |
|---|---|
| Migration rehearsal on a large synthetic database (2,995 reports incl. 105 voided, 3,381 legacy items → 5,169 legacy rows, 119 supplier entries, 60 POS settlements, 80 transfers, 24 cheques; `database/verification/rehearse_0021.py`) | applied in 0.4 s; balances at 14 dates, branch cash, 33-row verification script, every non-shop ledger row, every report/HO row and supplier figure **identical**; second run of 0021 refused and state unchanged; negative control (0.01 change) detected |
| Query time before → after (3,000 reports) | `app_balances` 17→24 ms, `app_branch_cash` 16→24 ms, ledger (year) 14→22 ms, dashboard totals with expense rows 4 ms |
| Backend `pytest` (28 itemised API tests incl. full lifecycle and 6-way concurrent double submit) | Python 3.11 and Python 3.12 (locked deps): 694 passed, 4 failed (pre-existing) |
| Database `pytest` | 223 passed, 2 failed (pre-existing) |
| Frontend | `tsc`, ESLint, Prettier, build clean; 351 tests passed (+24 live-contract tests, run against real API responses: 24/24) |
| Browser end to end (real Chrome, real frontend/backend/PostgreSQL, **simulated** Auth) | 124 of 131 passed. The 7 failures are identical to the 7 on the previous commit (login/logout/layout drift, unrelated). All 9 new itemised scenarios pass |
| Old backend (previous commit) against the migrated database | measured; see `docs/deployment/RELEASE_0021_CHECKLIST.md` §2 |

The pre-existing failures (all reproduced on the commit before this feature): backend `test_owner_profile` (profile now returns avatar/phone/preferences/must_change_password), `test_no_endpoint_accepts_role_changes` and `test_04_05_no_api_route_changes_roles_or_branches`
(`PATCH /auth/profile` now exists and answers 422; verified that role/branch/active/user_id changes are all refused and the role is unchanged), `test_activity_export_is_complete_and_owner_only`
(the activity report gained a 8th "Source" column; the test still counts 7-column rows); database `test_audit_does_not_store_secrets` (matches the harmless flag `must_change_password: false`),
`test_preferences_are_private_per_user` (the test asserts an error inside an open transaction block). Browser: Sign out now opens a confirmation dialog, which 3 of the 7 old scenarios do not click through; the rest are layout/label drift.

## Migration 0023 (archive / terminate / activate) — real PostgreSQL 16.2

| Suite | Result |
|---|---|
| `database/tests` (new `test_archive_terminate_activate.py`: tasks, rentals, customers, branches) | 240 passed, 2 failed (the same 2 pre-existing, unrelated) |
| `backend/tests` (new `test_archive_terminate_activate_api.py`, 23 tests) | 718 passed, 4 failed (the same 4 pre-existing, unrelated) |
| `frontend` (new `archive-actions.test.tsx`, 19 tests) | 370 passed; `tsc`, ESLint, Prettier, build clean |
| Browser end to end (real Chrome, real frontend / backend / PostgreSQL, simulated Auth) | 130 of 137 passed: the 6 new `ARCHIVE` scenarios and all `ITEMISED` scenarios pass; the 7 failures are the same 7 that fail on the commit before the itemised-expenses work (login / layout drift) |
| Large synthetic database: 0021 + 0022 + 0023 | every balance, ledger row, report and supplier figure identical before and after; 0023 touches no money table |

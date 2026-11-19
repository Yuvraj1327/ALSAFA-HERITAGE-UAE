> **Superseded by migration 0021** (`docs/finance/ITEMISED_SHOP_EXPENSES.md`): the "Other" JSON list is now a frozen legacy record; itemised expense rows replace it. Owner edit/audit/PDF behaviour described below still applies, with the new edit signature.

# Owner edits, daily-report PDF and "Other" shop expenses

Migration: `database/migrations/0020_owner_edits_other_expenses.sql` (additive; **review it, then apply it yourself** - nothing
was applied to any real database). It adds one column, one validation trigger and three owner-only functions.

## Design decisions (please confirm)
| Topic | Decision |
| --- | --- |
| Editing a submitted report | Only through `public.edit_daily_report()` (owner check inside the DB, reason required, row locked). `daily_reports` still has **no UPDATE grant** for anyone, so Branch Users cannot edit or delete, even by bypassing the API. |
| Original values / who / when / why | The existing audit trigger writes old and new values, actor, time and the reason (`app.audit_reason`) to `audit_log`. `GET /daily-reports/{id}/history` (Owner) and Activity History show them. |
| Recalculation | Collections, difference and expenses-excl-savings are derived on every read by `core/calculations.py`; nothing is stored, so an edit is reflected everywhere (dashboard, reports, PDF). |
| Duplicate branch/date | The existing partial unique index `dr_branch_date_active_uq` rejects it (409 `duplicate_report`); the refused edit changes nothing. |
| "Other" shop expense | `daily_reports.other_expenses` (jsonb items `{description, amount, notes}`) is a **breakdown of `shop_expenses`**, which stays the total. Nothing is added on top, so dashboard/report totals cannot double-count. A trigger checks the item shape and `sum(items) <= shop_expenses`. The `head_office_expenses` ledger is untouched. |
| Other Expenses tab | `GET /expenses/other` reads those items (joined to branch and the submitting user). Owner only (the `expenses` module is Owner-only). No second ledger. A voided report's items disappear. |
| PDF | `GET /daily-reports/export/pdf?date_from&date_to&branch_id&layout=individual\|consolidated` (ReportLab). *individual* = one branded page per report; *consolidated* = one landscape table with branch subtotals and grand totals. AED, Dubai time, logo from `backend/app/assets/logo.png`. Branch Users get `EXPORT` on `daily_reports` with **own-branch scope**: another branch id is 403 + audited. Limits: 366 days, 1500 reports. |
| Prepared By | The profile name of the report's creator (`app.profile_display_name`, so a colleague's name still shows for own-branch reports). |
| User edit | `PATCH /users/{id}` (Owner only; Branch User accounts only). Name, branch, active status are written to `profiles` (audited by the existing trigger). Email and password go to Supabase Auth through the Admin API; the database change is committed only after Auth accepts it (email is restored if the final commit fails). A duplicate email is 409. A reset password sets `must_change_password` so the user picks their own at next login. |
| Passwords | `SecretStr`, `hide_input_in_errors`, never returned, logged, stored or audited. Activity History records only "Password reset by Owner" (`record_user_admin_event`, which has no password parameter). |

## Not done / open points
* Deactivating a user blocks them on their next API call (profile is checked on every request) but does not revoke an already-issued JWT before it expires.
* "Other" was **not** added to the head-office `expense_categories` list, to avoid a second place to record the same cost. Say if you also want it there.
* The three screenshots mentioned in the brief were not available in the workspace, so the UI follows the existing design system rather than a mock-up.
* Pre-existing, unrelated test failures (identical with these changes stashed): `test_auth_profile_db::{test_owner_profile,test_no_endpoint_accepts_role_changes}`, `test_rbac_api::test_04_05_no_api_route_changes_roles_or_branches` (the self-profile PATCH from migration 0019), `test_step12_reports_api::test_activity_export_is_complete_and_owner_only`, and 3 in `database/tests` (`user_preferences` table list, audit secrets check, preferences privacy).

## Tests
`backend/tests/test_owner_edit_pdf_other.py` (integration, real PostgreSQL + RLS, mocked Supabase Auth), `test_user_update_service.py`,
updated permission-matrix tests, and `frontend/src/test/owner-features.test.tsx`.

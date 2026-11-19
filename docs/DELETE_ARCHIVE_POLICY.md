# Delete / archive / void / terminate policy (migration 0023)

**Nothing in ALSAFA ERP is hard-deleted.** There is no `DELETE` route in the API, no DELETE privilege for the application database role, and no
screen with a permanent-delete button. Records leave the active view in one of five audited ways; every one needs a reason, is written to
`audit_log` (who, when, why, old and new values) and keeps its history available to reports.

| Kind | Meaning | Reversible | Modules |
|---|---|---|---|
| **Void** | a money record is cancelled; it no longer counts in balances but stays visible | no (re-enter a corrected record) | daily reports (+ their expense rows and supplier entries), head-office expenses (button/dialog now say **Void expense**), supplier ledger entries, cash transfers, POS settlements |
| **Archive** | hidden from active lists, alerts and dashboard counts; read-only; history and reports keep it | **yes (Restore)** | employees, trade licences (existing); **tasks, customers (new)** |
| **Terminate** | a contract is closed from a date; everything linked is kept | no (final) | **rental agreements (new)** |
| **Deactivate** | can no longer take new work | **yes (Activate)** | suppliers, users (existing); **branches (new)** |
| **Cancel** | a business process is stopped with a reason | no | orders (existing) |

## The five actions added in 0023

| Module | Action | Who | Rules |
|---|---|---|---|
| Tasks | Archive / Restore | Owner (`tasks:manage`) | reason required; archived task is read-only; hidden from the list (filter *Show: Archived*), alerts and overdue counts; **stays in reports** (completion history; "Archived (not completed)" column in *Tasks by branch*); the old hard-delete grant, privilege and policy were removed |
| Rental agreements | Terminate | Owner (`rentals_cheques:manage`) | needs a **date** (inside the agreement period, not in the future), a **reason** and an explicit **confirmation**; only `Active` agreements; final; **all linked cheques (pending, cleared, bounced, replaced) and every balance stay exactly as they are**; pending cheques of a terminated agreement stop raising due-date alerts (mark them cleared/bounced if the bank presents them) |
| Customers | Archive / Restore | Owner (all branches), Branch User (own branch only) | reason required; blocked while the customer has **open orders** (not delivered/cancelled); archived customer: hidden from lists and the order form, read-only, **no new orders** (API 409 + database trigger), **orders, payments and history untouched and reachable**; the mobile number stays reserved (a duplicate attempt says "restore the archived customer") |
| Branches | Activate / Deactivate | Owner (`branch_management:manage`) | reason required; deactivation is **blocked** while the branch has active Branch Users, undelivered orders, active rental agreements, open (non-archived) tasks, cash on hand, or when it is the only active branch (checked in the UI list and again atomically in the database); an inactive branch takes no new reports, orders, customers or tasks; its history stays in every report |
| Head-office expenses | wording | - | every label, confirmation and message says **Void** (it always was a void) |

Everything above is covered by tests: database (constraints, triggers, privileges), backend API (permissions, rules, audit, reports), frontend (dialogs, reasons, permissions) and real-browser end-to-end scenarios.

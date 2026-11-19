"""Itemised shop expense lines (daily_report_expenses): queries only. Rules live in the services/calculations.

Runs as `authenticated` under RLS: the Owner sees every branch, a Branch User only their own. Only ACTIVE lines of
ACTIVE reports are ever counted (voiding a report or editing it voids its lines).
"""

import uuid
from datetime import date
from typing import Any

from sqlalchemy import text

from app.repositories.base import BaseRepository

Row = dict[str, Any]

_FROM = """from public.daily_report_expenses l
  join public.daily_reports r on r.id = l.report_id and r.voided_at is null
  join public.branches b on b.id = l.branch_id"""

_ACTIVE = "l.voided_at is null"

_FILTER = """ where l.voided_at is null
   and (cast(:d_from as date) is null or l.expense_date >= cast(:d_from as date))
   and (cast(:d_to as date) is null or l.expense_date <= cast(:d_to as date))
   and (cast(:branch as uuid) is null or l.branch_id = cast(:branch as uuid))
   and (cast(:category as text) is null or l.category = cast(:category as text))
   and (cast(:supplier as uuid) is null or l.supplier_id = cast(:supplier as uuid))
   and (cast(:status as text) is null or l.payment_status = cast(:status as text))
   and (cast(:q as text) is null or l.description ilike '%' || cast(:q as text) || '%'
        or coalesce(l.remarks, '') ilike '%' || cast(:q as text) || '%'
        or coalesce(l.supplier_name, '') ilike '%' || cast(:q as text) || '%')"""

# Sums shared by every breakdown. `Partial` figures are the part actually paid on partially paid lines.
_SUMS = """count(*) as lines, coalesce(sum(l.amount), 0) as total, coalesce(sum(l.amount_paid), 0) as paid,
       coalesce(sum(l.outstanding), 0) as outstanding,
       coalesce(sum(l.amount_paid) filter (where l.payment_status = 'Partial'), 0) as partial_paid,
       count(*) filter (where l.payment_status = 'Partial') as partial_lines,
       coalesce(sum(l.amount) filter (where l.payment_status = 'Unpaid'), 0) as unpaid_amount,
       count(*) filter (where l.payment_status = 'Unpaid') as unpaid_lines,
       coalesce(sum(l.amount_paid) filter (where l.payment_account = 'Cash'), 0) as cash_paid,
       coalesce(sum(l.amount_paid) filter (where l.payment_account in ('ENBD', 'ADCB')), 0) as bank_paid"""

_LINE_COLS = """l.id, l.report_id, l.expense_date, l.branch_id, b.name as branch_name, l.line_no, l.category,
       l.description, l.amount, l.remarks, l.supplier_id, l.supplier_name, l.payment_status, l.payment_account,
       l.amount_paid, l.outstanding, l.is_legacy"""

_GROUPS = {
    "category": ("l.category", "l.category", "l.category"),
    "branch": ("b.name", "b.sort_order, b.name", "b.name"),
    "date": ("l.expense_date", "l.expense_date desc", "l.expense_date"),
    "supplier": (
        "coalesce(l.supplier_name, 'No supplier')",
        "coalesce(l.supplier_name, 'No supplier')",
        "coalesce(l.supplier_name, 'No supplier')",
    ),
    "status": ("l.payment_status", "l.payment_status", "l.payment_status"),
}


def filters(
    d_from: date | None = None,
    d_to: date | None = None,
    branch: uuid.UUID | None = None,
    category: str | None = None,
    supplier: uuid.UUID | None = None,
    status: str | None = None,
    q: str | None = None,
) -> dict[str, Any]:
    return {
        "d_from": d_from,
        "d_to": d_to,
        "branch": branch,
        "category": category,
        "supplier": supplier,
        "status": status,
        "q": (q or "").strip() or None,
    }


class ShopExpenseRepository(BaseRepository):
    def _all(self, sql: str, params: dict[str, Any]) -> list[Row]:
        return [dict(r) for r in self.session.execute(text(sql), params).mappings().all()]

    def lines_for_reports(self, report_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[Row]]:
        if not report_ids:
            return {}
        rows = self._all(
            f"select {_LINE_COLS} {_FROM} where {_ACTIVE} and l.report_id = any(:ids)"
            " order by l.report_id, l.line_no",
            {"ids": list(report_ids)},
        )
        out: dict[uuid.UUID, list[Row]] = {}
        for r in rows:
            out.setdefault(r["report_id"], []).append(r)
        return out

    # ---- aggregates (dashboard / reports) --------------------------------------------------------------
    def totals(self, f: dict[str, Any]) -> Row:
        rows = self._all(f"select {_SUMS} {_FROM}{_FILTER}", f)
        return rows[0]

    def totals_by_branch(self, d_from: date, d_to: date) -> dict[uuid.UUID, Row]:
        rows = self._all(
            f"select l.branch_id, {_SUMS} {_FROM}{_FILTER} group by l.branch_id",
            filters(d_from, d_to),
        )
        return {r["branch_id"]: r for r in rows}

    def totals_by_day(self, d_from: date, d_to: date) -> dict[date, Row]:
        rows = self._all(
            f"select l.expense_date as day, {_SUMS} {_FROM}{_FILTER} group by l.expense_date",
            filters(d_from, d_to),
        )
        return {r["day"]: r for r in rows}

    def breakdown(self, group: str, f: dict[str, Any]) -> list[Row]:
        expr, order, grp = _GROUPS[group]
        return self._all(
            f"select {expr} as label, {_SUMS} {_FROM}{_FILTER} group by {grp} order by {order}",  # noqa: S608
            f,
        )

    def breakdown_branch_ids(self, f: dict[str, Any]) -> list[Row]:
        return self._all(
            f"select l.branch_id, b.name as label, {_SUMS} {_FROM}{_FILTER}"
            " group by l.branch_id, b.name, b.sort_order order by b.sort_order, b.name",
            f,
        )

    # ---- itemised history --------------------------------------------------------------------------------
    def lines_page(self, f: dict[str, Any], limit: int, offset: int) -> tuple[list[Row], Row]:
        rows = self._all(
            f"select {_LINE_COLS}, r.created_at as recorded_at,"  # noqa: S608
            " app.profile_display_name(r.created_by) as recorded_by"
            f" {_FROM}{_FILTER}"
            " order by l.expense_date desc, b.sort_order, r.created_at desc, l.line_no limit :lim offset :off",
            {**f, "lim": limit, "off": offset},
        )
        agg = self.totals(f)
        return rows, agg

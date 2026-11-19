import builtins
import logging
import re
import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy.exc import DBAPIError, IntegrityError

from app.api.authz import BranchAccessDeniedError, BranchScope
from app.core import calculations as calc
from app.core.exceptions import ConflictError, NotFoundError, UnprocessableError
from app.repositories.report_repository import ReportRepository
from app.schemas.reports import (
    DailyReportCreate,
    DailyReportEdit,
    DailyReportOut,
    DailyReportPage,
    ExpenseLineOut,
    ReportHistoryEntry,
    SupplierOption,
    expenses_payload,
)
from app.services import report_pdf_service as pdf
from app.services.base import BaseService
from app.utils.time import BUSINESS_TZ, dubai_today

logger = logging.getLogger(__name__)

MAX_PDF_DAYS = 366
MAX_PDF_REPORTS = 1500

# PostgreSQL SQLSTATE codes we translate into API errors
_UNIQUE, _CHECK, _FK, _RLS = "23505", "23514", "23503", "42501"


_HIDDEN = {"id", "created_at", "created_by", "updated_at", "updated_by", "voided_by"}
_LABELS = {"pos_amount": "POS", "link_payment": "Payment links", "bank_transfer": "Bank transfers"}


def _text(v: Any) -> str | None:
    if v is None or v == "":
        return None
    if isinstance(
        v, float
    ):  # audit_log jsonb numerics: 600.0 -> "600.00" (money has exactly two places)
        return f"{Decimal(str(v)):.2f}"
    if isinstance(v, list):  # legacy other_expenses: "Detergent 12.00; Gas 5.00"
        return "; ".join(f"{i.get('description')} {i.get('amount')}" for i in v) or None
    return str(v)


def history_entry(r: dict[str, Any]) -> ReportHistoryEntry:
    """Audit row -> readable before/after list. Only changed, non-bookkeeping fields are listed."""
    old, new = r["old_values"] or {}, r["new_values"] or {}
    changes = [
        {
            "field": _LABELS.get(k, k.replace("_", " ").capitalize()),
            "old": _text(old.get(k)),
            "new": _text(new.get(k)),
        }
        for k in new
        if k not in _HIDDEN and (r["action"] == "INSERT" or old.get(k) != new.get(k))
    ]
    return ReportHistoryEntry(
        id=r["id"],
        occurred_at=r["occurred_at"],
        actor_name=r["actor_name"],
        action=r["action"],
        reason=r["reason"],
        changes=changes,
    )


def money_sum(values: Any) -> Decimal:
    return calc.money(sum(values, Decimal(0)))


def expense_line_out(x: dict[str, Any]) -> ExpenseLineOut:
    return ExpenseLineOut(
        id=x["id"],
        line_no=x["line_no"],
        category=x["category"],
        description=x["description"],
        amount=x["amount"],
        remarks=x["remarks"],
        supplier_id=x["supplier_id"],
        supplier_name=x["supplier_name"],
        payment_status=x["payment_status"],
        payment_account=x["payment_account"],
        amount_paid=x["amount_paid"],
        outstanding=calc.expense_outstanding(x["amount"], x["amount_paid"]),
        is_legacy=x["is_legacy"],
    )


def report_out(row: dict[str, Any]) -> DailyReportOut:
    """Row -> API model, adding every computed field through app.core.calculations (the only place they exist)."""
    received = calc.total_received(
        row["cash_received"], row["pos_amount"], row["bank_transfer"], row["link_payment"]
    )
    diff = calc.difference(row["total_sales"], received)
    lines = [expense_line_out(x) for x in row.get("expenses") or []]
    cash_paid = money_sum(x.amount_paid for x in lines if x.payment_account == "Cash")
    return DailyReportOut(
        **{
            k: row[k]
            for k in (
                "id",
                "report_date",
                "branch_id",
                "branch_name",
                "total_sales",
                "order_count",
                "cash_received",
                "pos_amount",
                "bank_transfer",
                "link_payment",
                "shop_expenses",
                "shop_savings",
                "prepared_by",
                "created_at",
                "updated_at",
            )
        },
        expenses=lines,
        expenses_paid=money_sum(x.amount_paid for x in lines),
        expenses_outstanding=money_sum(x.outstanding for x in lines),
        cash_paid_expenses=cash_paid,
        bank_paid_expenses=money_sum(x.amount_paid for x in lines if x.payment_account != "Cash"),
        net_cash=calc.net_cash(row["cash_received"], cash_paid, row["shop_savings"]),
        total_received=received,
        difference=diff,
        check=calc.check_label(diff),
        shop_expenses_excl_savings=calc.shop_expenses_excl_savings(
            row["shop_expenses"], row["shop_savings"]
        ),
    )


def _sqlstate(exc: DBAPIError) -> str | None:
    return getattr(exc.orig, "sqlstate", None)


class ReportService(BaseService):
    def __init__(self, session: Any) -> None:
        super().__init__(session)
        self.repo = ReportRepository(session)

    # ---- submit ----------------------------------------------------------------------------------------
    def create(
        self, scope: BranchScope, body: DailyReportCreate, today: date | None = None
    ) -> DailyReportOut:
        today = today or dubai_today()
        branch_id = self._branch_for_submit(scope, body.branch_id)
        report_date = body.report_date or today
        if report_date > today:
            raise UnprocessableError(
                "Report date cannot be in the future (Dubai time)",
                fields={
                    "report_date": [
                        f"Report date cannot be after today in Dubai ({today.isoformat()})"
                    ]
                },
            )
        try:
            values = body.model_dump(mode="python", exclude={"expenses"})
            values["expenses"] = expenses_payload(body.expenses)
            new_id = self.repo.insert(branch_id, report_date, values)
            self.session.commit()
        except IntegrityError as exc:
            self.session.rollback()
            raise self._translate(exc) from exc
        except DBAPIError as exc:
            self.session.rollback()
            raise self._translate(exc) from exc
        row = self.repo.get(new_id)
        assert row is not None  # we just inserted it under the same session
        return report_out(row)

    def _branch_for_submit(self, scope: BranchScope, requested: uuid.UUID | None) -> uuid.UUID:
        if scope.is_all:
            if requested is None:
                raise UnprocessableError(
                    "Select a branch", fields={"branch_id": ["Select a branch"]}
                )
            found = self.repo.get_branch(requested)
            if found is None or not found["is_active"]:
                raise UnprocessableError(
                    "Unknown or inactive branch", fields={"branch_id": ["Select an active branch"]}
                )
            return requested
        # Branch user: the branch is the one on their profile. A different id in the payload is an attack or a
        # stale form, and is refused (and audited) rather than silently replaced.
        own = scope.resolve(requested)
        assert own is not None
        return own

    @staticmethod
    def _translate(exc: DBAPIError) -> Exception:
        state = _sqlstate(exc)
        text = str(getattr(exc.orig, "diag", None) and exc.orig.diag.constraint_name or "")  # type: ignore[union-attr]
        if state == _UNIQUE:
            return ConflictError(
                "A report for this branch and date already exists",
                code="duplicate_report",
                fields={"report_date": ["A report for this branch and date already exists"]},
            )
        if state == _RLS:
            return BranchAccessDeniedError()
        if state == _FK:
            return UnprocessableError(
                "Unknown branch", fields={"branch_id": ["Select an active branch"]}
            )
        if state == _CHECK:
            message = str(exc.orig).splitlines()[0] if exc.orig else "Report breaks a business rule"
            diag = getattr(exc.orig, "diag", None)
            primary = str(getattr(diag, "message_primary", "") or "")
            if primary.startswith("expense line"):
                # raised by app.parse_report_expenses, e.g. "expense line 2: amount must be greater than zero"
                return UnprocessableError(
                    primary[0].upper() + primary[1:], fields={"expenses": [primary]}
                )
            if "supplier" in primary and "negative" in primary:
                return UnprocessableError(
                    "This change would leave a supplier with a negative balance",
                    code="negative_balance",
                )
            if "future" in message:
                return UnprocessableError(
                    "Report date cannot be in the future (Dubai time)",
                    fields={"report_date": ["Report date cannot be in the future"]},
                )
            logger.warning("report rejected by database check %s", text)
            return UnprocessableError("The report breaks a business rule")
        raise exc

    # ---- read ------------------------------------------------------------------------------------------
    def list(
        self,
        scope: BranchScope,
        branch_id: uuid.UUID | None,
        d_from: date | None,
        d_to: date | None,
        page: int,
        page_size: int,
    ) -> DailyReportPage:
        if d_from and d_to and d_from > d_to:
            raise UnprocessableError(
                "Start date is after end date",
                fields={"date_from": ["Start date must not be after end date"]},
            )
        branch = scope.resolve(branch_id)
        rows, total = self.repo.list_page(branch, d_from, d_to, page_size, (page - 1) * page_size)
        return DailyReportPage(
            items=[report_out(r) for r in rows], total=total, page=page, page_size=page_size
        )

    def get(self, scope: BranchScope, report_id: uuid.UUID) -> DailyReportOut:
        row = self.repo.get(report_id)
        if row is None:
            # RLS hides other branches' rows, so "not yours" and "does not exist" look the same.
            raise NotFoundError("Report not found")
        scope.ensure(row["branch_id"])
        return report_out(row)

    def supplier_options(self) -> builtins.list[SupplierOption]:
        return [SupplierOption(**r) for r in self.repo.supplier_options()]

    # ---- PDF export --------------------------------------------------------------------------------------
    def export_pdf(
        self,
        scope: BranchScope,
        branch_id: uuid.UUID | None,
        d_from: date,
        d_to: date,
        layout: pdf.Layout,
    ) -> tuple[bytes, str, int]:
        """(pdf bytes, file name, number of reports). Branch Users are pinned to their own branch by
        scope.resolve (another branch id -> 403 + audit); the rows themselves come through RLS."""
        if d_from > d_to:
            raise UnprocessableError(
                "Start date is after end date",
                fields={"date_from": ["Start date must not be after end date"]},
            )
        if (d_to - d_from) > timedelta(days=MAX_PDF_DAYS):
            raise UnprocessableError(
                f"Choose a date range of at most {MAX_PDF_DAYS} days",
                fields={"date_to": [f"Range is limited to {MAX_PDF_DAYS} days"]},
            )
        branch = scope.resolve(branch_id)
        rows = self.repo.list_range(branch, d_from, d_to)
        if len(rows) > MAX_PDF_REPORTS:
            raise UnprocessableError(
                f"More than {MAX_PDF_REPORTS} reports match; narrow the branch or the dates"
            )
        branch_label = "All Branches"
        if branch is not None:
            found = self.repo.get_branch(branch)
            branch_label = found["name"] if found else "Branch"
        data = pdf.build_pdf(
            [report_out(r) for r in rows],
            layout=layout,
            branch_label=branch_label,
            date_from=d_from,
            date_to=d_to,
            generated_by=scope.principal.full_name,
            generated_at=datetime.now(BUSINESS_TZ),
        )
        slug = re.sub(r"[^a-z0-9]+", "-", branch_label.lower()).strip("-") or "branch"
        name = f"alsafa-daily-reports-{slug}-{d_from.isoformat()}"
        if d_to != d_from:
            name += f"_{d_to.isoformat()}"
        suffix = "consolidated" if layout == "consolidated" else "individual"
        return data, f"{name}-{suffix}.pdf", len(rows)

    # ---- owner edit (audited; original values stay in audit_log) ----------------------------------------
    def edit(
        self, report_id: uuid.UUID, body: DailyReportEdit, today: date | None = None
    ) -> DailyReportOut:
        today = today or dubai_today()
        if body.report_date > today:
            raise UnprocessableError(
                "Report date cannot be in the future (Dubai time)",
                fields={
                    "report_date": [
                        f"Report date cannot be after today in Dubai ({today.isoformat()})"
                    ]
                },
            )
        branch = self.repo.get_branch(body.branch_id)
        if branch is None or not branch["is_active"]:
            # An unchanged branch may be inactive (a historical report); the database allows that case.
            current = self.repo.get(report_id)
            if current is None:
                raise NotFoundError("Report not found")
            if current["branch_id"] != body.branch_id:
                raise UnprocessableError(
                    "Unknown or inactive branch", fields={"branch_id": ["Select an active branch"]}
                )
        try:
            values = body.model_dump(mode="python", exclude={"expenses", "reason"})
            values["expenses"] = expenses_payload(body.expenses)
            self.repo.edit(report_id, values, body.reason)
            self.session.commit()
        except DBAPIError as exc:
            self.session.rollback()
            if _sqlstate(exc) == "P0002":
                raise NotFoundError("Report not found or already voided") from exc
            if _sqlstate(exc) == _CHECK and "reason" in str(exc.orig):
                raise UnprocessableError(
                    "A reason is required", fields={"reason": ["A reason is required"]}
                ) from exc
            raise self._translate(exc) from exc
        row = self.repo.get(report_id)
        assert row is not None
        return report_out(row)

    def history(self, report_id: uuid.UUID) -> builtins.list[ReportHistoryEntry]:
        """Owner only (audit_log RLS). 404 for an unknown or already-voided report."""
        if self.repo.get(report_id) is None:
            raise NotFoundError("Report not found")
        return [history_entry(r) for r in self.repo.history(report_id)]

    # ---- owner correction (decision D4: audited void; the database function checks the role again) --------
    def void(self, report_id: uuid.UUID, reason: str) -> None:
        try:
            self.repo.void(report_id, reason)
            self.session.commit()
        except DBAPIError as exc:
            self.session.rollback()
            state = _sqlstate(exc)
            if state == "P0002":  # no_data_found
                raise NotFoundError("Report not found or already voided") from exc
            if state == _RLS:
                raise BranchAccessDeniedError() from exc
            if state == _CHECK:
                raise UnprocessableError(
                    "A reason is required", fields={"reason": ["A reason is required"]}
                ) from exc
            raise

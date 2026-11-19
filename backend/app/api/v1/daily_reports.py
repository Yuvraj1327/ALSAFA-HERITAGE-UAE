import logging
import uuid
from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Response, status

from app.api.authz import BranchScope, get_branch_scope
from app.api.deps import DbSession
from app.core.permissions import Action, Module
from app.schemas.dashboard import MissingReports
from app.schemas.reports import (
    DailyReportCreate,
    DailyReportEdit,
    DailyReportOut,
    DailyReportPage,
    ReportHistoryEntry,
    SupplierOption,
    VoidRequest,
)
from app.services.dashboard_service import DashboardService
from app.services.report_service import ReportService
from app.utils.time import dubai_today

router = APIRouter(prefix="/daily-reports", tags=["daily-reports"])
logger = logging.getLogger("app.audit")

ReadScope = Annotated[BranchScope, Depends(get_branch_scope(Module.DAILY_REPORTS, Action.READ))]
CreateScope = Annotated[BranchScope, Depends(get_branch_scope(Module.DAILY_REPORTS, Action.CREATE))]
UpdateScope = Annotated[BranchScope, Depends(get_branch_scope(Module.DAILY_REPORTS, Action.UPDATE))]
ExportScope = Annotated[BranchScope, Depends(get_branch_scope(Module.DAILY_REPORTS, Action.EXPORT))]


@router.post(
    "",
    response_model=DailyReportOut,
    status_code=status.HTTP_201_CREATED,
    summary="Submit a daily report",
)
def create_report(
    body: DailyReportCreate, session: DbSession, scope: CreateScope
) -> DailyReportOut:
    """Branch users submit for their own branch only; the owner chooses the branch. One report per branch/date."""
    return ReportService(session).create(scope, body)


@router.get("", response_model=DailyReportPage, summary="Report history (latest first)")
def list_reports(
    session: DbSession,
    scope: ReadScope,
    branch_id: uuid.UUID | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    page: Annotated[int, Query(ge=1, le=100000)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 25,
) -> DailyReportPage:
    return ReportService(session).list(scope, branch_id, date_from, date_to, page, page_size)


@router.get(
    "/supplier-options",
    response_model=list[SupplierOption],
    summary="Active suppliers (id + name only) for the expense rows of a daily report",
)
def supplier_options(session: DbSession, _scope: CreateScope) -> list[SupplierOption]:
    """Branch Users may pick an existing active supplier; they can never create or change one."""
    return ReportService(session).supplier_options()


# NOTE: "supplier-options", "missing" and "export/pdf" are declared before "/{report_id}" so they are not parsed as an id.
@router.get(
    "/export/pdf",
    summary="Download daily reports as a branded PDF (Owner: any branch; Branch User: own branch)",
    response_class=Response,
    responses={200: {"content": {"application/pdf": {}}}},
)
def export_pdf(
    session: DbSession,
    scope: ExportScope,
    date_from: date,
    date_to: date | None = None,
    branch_id: uuid.UUID | None = None,
    layout: Literal["individual", "consolidated"] = "individual",
) -> Response:
    """Built from the database rows the caller may read. A Branch User is always limited to their own branch."""
    data, filename, count = ReportService(session).export_pdf(
        scope, branch_id, date_from, date_to or date_from, layout
    )
    logger.info(
        "daily_report_pdf user=%s layout=%s reports=%d from=%s to=%s branch=%s",
        scope.principal.user_id, layout, count, date_from, date_to or date_from, branch_id,
    )  # fmt: skip
    return Response(
        content=data,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/missing", response_model=MissingReports, summary="Branches/dates with no report")
def missing_reports(
    session: DbSession,
    scope: ReadScope,
    date_from: date,
    date_to: date | None = None,
    branch_id: uuid.UUID | None = None,
) -> MissingReports:
    branch = scope.resolve(branch_id)
    today = dubai_today()
    end = date_to or date_from
    return DashboardService(session).missing(branch, date_from, min(end, today), today)


@router.get("/{report_id}", response_model=DailyReportOut, summary="One report")
def get_report(report_id: uuid.UUID, session: DbSession, scope: ReadScope) -> DailyReportOut:
    return ReportService(session).get(scope, report_id)


@router.put(
    "/{report_id}",
    response_model=DailyReportOut,
    summary="Owner: edit a submitted report (reason required; audited)",
)
def edit_report(
    report_id: uuid.UUID, body: DailyReportEdit, session: DbSession, _scope: UpdateScope
) -> DailyReportOut:
    """Branch Users have no UPDATE grant (403 here, and no update grant in the database). Collections, difference and
    expense totals are recomputed from the stored values on every read; the original values stay in audit_log."""
    return ReportService(session).edit(report_id, body)


@router.get(
    "/{report_id}/history",
    response_model=list[ReportHistoryEntry],
    summary="Owner: who changed this report, when, why, and the before/after values",
)
def report_history(
    report_id: uuid.UUID, session: DbSession, _scope: UpdateScope
) -> list[ReportHistoryEntry]:
    return ReportService(session).history(report_id)


@router.post(
    "/{report_id}/void",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Owner: void a report with a reason",
)
def void_report(
    report_id: uuid.UUID, body: VoidRequest, session: DbSession, _scope: UpdateScope
) -> Response:
    """The only correction path (decision D4): audited, reason required, frees the date for a fresh report."""
    ReportService(session).void(report_id, body.reason)
    return Response(status_code=status.HTTP_204_NO_CONTENT)

import uuid
from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel

from app.schemas.money import Money
from app.schemas.reports import DailyReportOut

MissingStatus = Literal["ok", "not_configured", "before_start"]


class Collections(BaseModel):
    """Collection breakdown shared by every summary."""

    cash_received: Money
    pos_amount: Money
    bank_transfer: Money
    link_payment: Money
    total_received: Money


class Totals(BaseModel):
    """Everything derived from daily reports (and, where permitted, head-office expenses) for one period."""

    report_count: int
    total_sales: Money
    total_orders: int
    collections: Collections
    not_yet_received: Money
    shop_expenses: Money
    shop_savings: Money
    shop_expenses_excl_savings: Money
    # Payment-based view of the itemised expenses (all derived by app.core.calculations)
    shop_paid: Money = Decimal(0)  # SUM(amount paid)
    shop_unpaid: Money = Decimal(0)  # amount of lines with status Unpaid
    shop_partial_paid: Money = Decimal(0)  # part paid on Partial lines
    shop_outstanding: Money = Decimal(0)  # unpaid + partial remainders
    cash_paid_expenses: Money = Decimal(0)
    bank_paid_expenses: Money = Decimal(0)
    net_cash: Money = Decimal(0)  # cash received - cash paid expenses - savings


class MissingBranch(BaseModel):
    branch_id: uuid.UUID
    branch_name: str
    missing_count: int
    dates: list[date]


class MissingReports(BaseModel):
    """Missing daily reports for a period, only counting days from the reporting start up to today (Dubai)."""

    status: MissingStatus
    reporting_start: date | None
    date_from: date | None  # effective range after clamping to [reporting start, today]
    date_to: date | None
    expected: int
    received: int
    missing_count: int
    branches: list[MissingBranch]


class HeadOfficeSummary(BaseModel):
    total: Money
    savings: Money
    excl_savings: Money
    count: int


class HeadOfficeRow(BaseModel):
    id: uuid.UUID
    expense_date: date
    branch_id: uuid.UUID | None
    branch_name: str | None
    category: str
    amount: Money
    paid_from: str
    is_saving: bool
    note: str | None


class BranchDateView(BaseModel):
    branch_id: uuid.UUID | None  # None = all branches (owner only)
    date_from: date
    date_to: date
    totals: Totals
    reports: list[DailyReportOut]
    missing: MissingReports
    head_office: HeadOfficeSummary | None  # owner only
    head_office_rows: list[HeadOfficeRow] | None
    head_office_unallocated: (
        HeadOfficeSummary | None
    )  # items tagged "All branches", shown beside a single branch


# ---- Owner dashboard ---------------------------------------------------------------------------------------
class Kpis(BaseModel):
    total_sales: Money
    total_received: Money
    not_yet_received: Money
    shop_expenses_excl_savings: Money
    head_office_expenses_excl_savings: Money
    total_savings: Money
    sales_minus_expenses: Money
    total_orders: int
    cash_bank_balance: Money | None  # None when balances are not configured / before the start date
    total_shop_expenses: Money = Decimal(
        0
    )  # itemised expenses + savings (as stored on the reports)
    shop_expenses_paid: Money = Decimal(0)
    shop_expenses_unpaid: Money = Decimal(0)
    shop_partial_payments: Money = Decimal(0)
    shop_expenses_outstanding: Money = Decimal(0)
    net_cash_period: Money = Decimal(
        0
    )  # cash received - cash expenses actually paid - savings, in the period
    net_cash_in_hand: Money | None = None  # Cash in Hand at the end of the period (ledger)
    supplier_outstanding: Money = Decimal(
        0
    )  # owed to all suppliers now: opening + valid dues - valid payments


class BalanceSummary(BaseModel):
    status: Literal["ok", "not_configured", "before_start"]
    reporting_start: date | None
    balance_date: date | None
    cash_in_hand: Money | None
    enbd: Money | None
    adcb: Money | None
    pos_pending: Money | None
    total: Money | None


class BranchRow(BaseModel):
    branch_id: uuid.UUID
    branch_name: str
    report_count: int
    total_sales: Money
    total_orders: int
    total_received: Money
    not_yet_received: Money
    shop_expenses_excl_savings: Money
    shop_savings: Money
    head_office_excl_savings: Money
    missing_count: int | None  # None when missing detection is not configured


class DayRow(BaseModel):
    date: date
    report_count: int
    missing_count: int | None
    total_sales: Money
    total_orders: int
    total_received: Money
    not_yet_received: Money
    shop_expenses_excl_savings: Money
    head_office_expenses_excl_savings: Money
    total_savings: Money
    sales_minus_expenses: Money


class Alert(BaseModel):
    key: str
    title: str
    count: int
    detail: str | None = None
    severity: Literal["warning", "danger", "info"]
    href: str | None = None  # in-app page that lists the records behind the count


class OwnerDashboard(BaseModel):
    mode: Literal["day", "month", "range"]
    preset: str | None = None  # the quick range that was resolved (range mode), else None
    date_from: date
    date_to: date
    today: date
    kpis: Kpis
    balances: BalanceSummary
    missing: MissingReports
    alerts: list[Alert]
    branches: list[BranchRow]
    unallocated_head_office_excl_savings: Money
    days: list[DayRow] | None  # month mode and multi-day ranges

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.money import Money, MoneyIn, PositiveMoneyIn

MAX_EXPENSE_LINES = 100

ExpenseCategory = Literal[
    "Food", "Raw Material", "Stationery", "Repairs & Maintenance", "Petrol", "Other"
]
EXPENSE_CATEGORIES: tuple[str, ...] = (
    "Food",
    "Raw Material",
    "Stationery",
    "Repairs & Maintenance",
    "Petrol",
    "Other",
)
LEGACY_CATEGORY = (
    "Other (Legacy)"  # historical lines created by migration 0021; never accepted as input
)
PaymentStatus = Literal["Paid", "Unpaid", "Partial"]
PaymentAccount = Literal["Cash", "ENBD", "ADCB"]


class ExpenseLineIn(BaseModel):
    """One itemised shop expense row. Savings are NOT an expense: they have their own field on the report."""

    model_config = ConfigDict(extra="forbid")

    category: ExpenseCategory
    description: str = Field(min_length=1, max_length=200)
    amount: PositiveMoneyIn
    remarks: str | None = Field(None, max_length=500)
    supplier_id: uuid.UUID | None = None
    payment_status: PaymentStatus = "Paid"
    payment_account: PaymentAccount | None = None
    amount_paid: MoneyIn | None = Field(
        None, description="Paid: omit or equal to amount. Unpaid: omit or 0. Partial: required."
    )

    @field_validator("description")
    @classmethod
    def _description(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Description must not be blank")
        return v

    @field_validator("remarks")
    @classmethod
    def _remarks(cls, v: str | None) -> str | None:
        return (v.strip() or None) if v is not None else None

    @model_validator(mode="after")
    def _rules(self) -> "ExpenseLineIn":
        paid = self.amount_paid
        if self.payment_status == "Paid":
            if paid is not None and paid != self.amount:
                raise ValueError("A Paid expense must be paid in full")
            self.amount_paid = self.amount
            if self.payment_account is None:
                raise ValueError("Choose the payment account (Cash, ENBD or ADCB)")
        elif self.payment_status == "Unpaid":
            if paid not in (None, Decimal(0)):
                raise ValueError("An Unpaid expense has no amount paid")
            self.amount_paid = Decimal("0.00")
            self.payment_account = None
            if self.supplier_id is None:
                raise ValueError("Select the supplier this unpaid expense is owed to")
        else:  # Partial
            if paid is None or paid <= 0:
                raise ValueError("Enter the amount paid (more than zero)")
            if paid >= self.amount:
                raise ValueError("A Partial payment must be less than the expense amount")
            if self.payment_account is None:
                raise ValueError("Choose the payment account (Cash, ENBD or ADCB)")
            if self.supplier_id is None:
                raise ValueError("Select the supplier the remaining amount is owed to")
        return self


def expenses_payload(lines: list[ExpenseLineIn]) -> list[dict[str, Any]]:
    """API lines -> the JSON the database functions validate again (amounts as exact 2-place strings)."""
    out: list[dict[str, Any]] = []
    for ln in lines:
        d: dict[str, Any] = {
            "category": ln.category,
            "description": ln.description,
            "amount": f"{ln.amount:.2f}",
            "payment_status": ln.payment_status,
            "amount_paid": f"{(ln.amount_paid if ln.amount_paid is not None else Decimal(0)):.2f}",
        }
        if ln.remarks:
            d["remarks"] = ln.remarks
        if ln.supplier_id is not None:
            d["supplier_id"] = str(ln.supplier_id)
        if ln.payment_account is not None:
            d["payment_account"] = ln.payment_account
        out.append(d)
    return out


class DailyReportCreate(BaseModel):
    """Body of POST /daily-reports. Structural and arithmetic rules live here; the Dubai-time future-date rule and
    branch authorization are applied by the service and again by the database function. Unknown fields are rejected,
    so a `created_by`, `role` or the old `shop_expenses` smuggled into the body is an error, never silently used.

    Total shop expenses are NOT sent: they are computed (itemised expenses + savings)."""

    model_config = ConfigDict(extra="forbid")

    report_date: date | None = Field(None, description="Defaults to today in Asia/Dubai")
    branch_id: uuid.UUID | None = Field(
        None, description="Owner: required. Branch user: omit, or own branch only"
    )
    total_sales: MoneyIn
    order_count: int = Field(0, ge=0, le=1_000_000)
    cash_received: MoneyIn = Decimal(0)
    pos_amount: MoneyIn = Decimal(0)
    bank_transfer: MoneyIn = Decimal(0)
    link_payment: MoneyIn = Decimal(0)
    shop_savings: MoneyIn = Decimal(0)
    expenses: list[ExpenseLineIn] = Field(default_factory=list, max_length=MAX_EXPENSE_LINES)

    @model_validator(mode="after")
    def _rules(self) -> "DailyReportCreate":
        if self.total_sales <= 0:
            raise ValueError("Total sales must be greater than zero")
        return self


class DailyReportEdit(BaseModel):
    """Body of PUT /daily-reports/{id} (Owner only). A full replacement of the editable fields (expense rows
    included) plus the mandatory reason; the original values stay in the audit log."""

    model_config = ConfigDict(extra="forbid")

    report_date: date
    branch_id: uuid.UUID
    total_sales: MoneyIn
    order_count: int = Field(ge=0, le=1_000_000)
    cash_received: MoneyIn
    pos_amount: MoneyIn
    bank_transfer: MoneyIn
    link_payment: MoneyIn
    shop_savings: MoneyIn
    expenses: list[ExpenseLineIn] = Field(default_factory=list, max_length=MAX_EXPENSE_LINES)
    reason: str = Field(min_length=3, max_length=500)

    @field_validator("reason")
    @classmethod
    def _reason(cls, v: str) -> str:
        v = v.strip()
        if len(v) < 3:
            raise ValueError("Enter a reason (at least 3 characters)")
        return v

    @model_validator(mode="after")
    def _rules(self) -> "DailyReportEdit":
        if self.total_sales <= 0:
            raise ValueError("Total sales must be greater than zero")
        return self


class ExpenseLineOut(BaseModel):
    id: uuid.UUID
    line_no: int
    category: str  # the six categories, or "Other (Legacy)" for historical entries
    description: str
    amount: Money
    remarks: str | None = None
    supplier_id: uuid.UUID | None = None
    supplier_name: str | None = None
    payment_status: PaymentStatus
    payment_account: PaymentAccount | None = None
    amount_paid: Money
    outstanding: Money
    is_legacy: bool = False


class SupplierOption(BaseModel):
    """What a Branch User may know about a supplier: its name (never balances or terms)."""

    id: uuid.UUID
    name: str


class ReportHistoryEntry(BaseModel):
    """One audit_log row for a report: who changed it, when, why, and the before/after values."""

    id: int
    occurred_at: datetime
    actor_name: str | None
    action: str
    reason: str | None
    changes: list[dict[str, str | None]]


class DailyReportOut(BaseModel):
    id: uuid.UUID
    report_date: date
    branch_id: uuid.UUID
    branch_name: str
    total_sales: Money
    order_count: int
    cash_received: Money
    pos_amount: Money
    bank_transfer: Money
    link_payment: Money
    shop_expenses: Money
    shop_savings: Money
    # Computed by app.core.calculations, never by the client
    total_received: Money
    difference: Money
    check: Literal["Matches", "Diff"]
    shop_expenses_excl_savings: Money
    expenses: list[ExpenseLineOut] = Field(default_factory=list)
    # Payment-based figures (app.core.calculations)
    expenses_paid: Money = Decimal(0)  # SUM(amount paid) on all lines
    expenses_outstanding: Money = Decimal(0)  # SUM(amount - paid): unpaid + partial remainders
    cash_paid_expenses: Money = Decimal(0)  # paid in cash (reduces the branch's cash)
    bank_paid_expenses: Money = Decimal(0)  # paid from ENBD / ADCB
    net_cash: Money = Decimal(0)  # cash received - cash paid expenses - savings
    prepared_by: str | None = None
    created_at: datetime  # submission time
    updated_at: datetime | None = None


class DailyReportPage(BaseModel):
    items: list[DailyReportOut]
    total: int
    page: int
    page_size: int


class VoidRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=3, max_length=500)

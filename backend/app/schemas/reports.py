import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.money import Money, MoneyIn, PositiveMoneyIn

MAX_OTHER_ITEMS = 50


class OtherExpenseItem(BaseModel):
    """One itemised "Other" shop expense. It is part of the report's shop_expenses total, never an addition to it."""

    model_config = ConfigDict(extra="forbid")

    description: str = Field(min_length=1, max_length=200)
    amount: PositiveMoneyIn
    notes: str | None = Field(None, max_length=500)

    @field_validator("description")
    @classmethod
    def _description(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Description must not be blank")
        return v

    @field_validator("notes")
    @classmethod
    def _notes(cls, v: str | None) -> str | None:
        return (v.strip() or None) if v is not None else None


def _check_other(shop_expenses: Decimal, items: list[OtherExpenseItem]) -> None:
    if sum((i.amount for i in items), Decimal(0)) > shop_expenses:
        raise ValueError("Other expenses cannot exceed the shop expenses total")


class DailyReportCreate(BaseModel):
    """Body of POST /daily-reports. Structural and arithmetic rules live here; the Dubai-time future-date rule and
    branch authorization are applied by the service. Unknown fields are rejected, so a `created_by` or `role` smuggled
    into the body is an error, never silently used."""

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
    shop_expenses: MoneyIn = Decimal(0)
    shop_savings: MoneyIn = Decimal(0)
    # shop_expenses is the TOTAL of all shop expenses; these items are the "Other" part of it.
    other_expenses: list[OtherExpenseItem] = Field(default_factory=list, max_length=MAX_OTHER_ITEMS)

    @model_validator(mode="after")
    def _rules(self) -> "DailyReportCreate":
        if self.total_sales <= 0:
            raise ValueError("Total sales must be greater than zero")
        if self.shop_savings > self.shop_expenses:
            raise ValueError("Savings cannot exceed shop expenses")
        _check_other(self.shop_expenses, self.other_expenses)
        return self


class DailyReportEdit(BaseModel):
    """Body of PUT /daily-reports/{id} (Owner only). A full replacement of the editable fields plus the mandatory
    reason; the original values stay in the audit log."""

    model_config = ConfigDict(extra="forbid")

    report_date: date
    branch_id: uuid.UUID
    total_sales: MoneyIn
    order_count: int = Field(ge=0, le=1_000_000)
    cash_received: MoneyIn
    pos_amount: MoneyIn
    bank_transfer: MoneyIn
    link_payment: MoneyIn
    shop_expenses: MoneyIn
    shop_savings: MoneyIn
    other_expenses: list[OtherExpenseItem] = Field(default_factory=list, max_length=MAX_OTHER_ITEMS)
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
        if self.shop_savings > self.shop_expenses:
            raise ValueError("Savings cannot exceed shop expenses")
        _check_other(self.shop_expenses, self.other_expenses)
        return self


class OtherExpenseOut(BaseModel):
    description: str
    amount: Money
    notes: str | None = None


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
    other_expenses: list[OtherExpenseOut] = Field(default_factory=list)
    other_expenses_total: Money = Decimal(0)
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

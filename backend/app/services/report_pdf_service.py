"""Branch-wise Daily Report PDF (ReportLab). Built only from DailyReportOut rows that were read from the database
under the caller's own session (so RLS and BranchScope already decided which reports exist). Amounts are AED, all
times are Asia/Dubai. Two layouts:

* ``individual``   - one branded page per report (one branch, one date);
* ``consolidated`` - one landscape summary table (a row per report) with per-branch subtotals and grand totals.
"""

from __future__ import annotations

import io
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    Image,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from app.core import calculations as calc
from app.schemas.reports import DailyReportOut
from app.utils.time import BUSINESS_TZ

Layout = Literal["individual", "consolidated"]
COMPANY = "ALSAFA Heritage Laundry"
LOGO = Path(__file__).resolve().parent.parent / "assets" / "logo.png"
NAVY = colors.HexColor("#173b63")
LIGHT = colors.HexColor("#eef3f9")
GRID = colors.HexColor("#c9d4e2")
GOOD, WARN = colors.HexColor("#1b7a3d"), colors.HexColor("#b45309")


def _ts(commands: list[Any]) -> TableStyle:
    return TableStyle(commands)


def aed(v: Decimal | int | str) -> str:
    return f"AED {Decimal(v):,.2f}"


def num(v: Decimal | int | str) -> str:
    return f"{Decimal(v):,.2f}"


def dubai(dt: datetime) -> datetime:
    return dt.astimezone(BUSINESS_TZ)


def stamp(dt: datetime) -> str:
    return dubai(dt).strftime("%d-%b-%Y %H:%M") + " (Dubai)"


def _p(text: str, style: ParagraphStyle) -> Paragraph:
    return Paragraph(escape(text), style)  # user text (descriptions, names) is always escaped


def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle("t", parent=base["Title"], textColor=NAVY, fontSize=18, alignment=0, spaceAfter=0),
        "sub": ParagraphStyle("s", parent=base["Normal"], textColor=colors.HexColor("#475569"), fontSize=9),
        "cell": ParagraphStyle("c", parent=base["Normal"], fontSize=9, leading=11),
        "small": ParagraphStyle("sm", parent=base["Normal"], fontSize=7, leading=8.5),
        "smallr": ParagraphStyle("smr", parent=base["Normal"], fontSize=7, leading=8.5, alignment=2),
        "h2": ParagraphStyle("h2", parent=base["Heading2"], textColor=NAVY, fontSize=12, spaceBefore=8, spaceAfter=4),
    }  # fmt: skip


def _header(st: dict[str, ParagraphStyle], title: str, subtitle: str, width: float) -> Table:
    logo = Image(str(LOGO), width=18 * mm, height=18 * mm) if LOGO.exists() else ""
    text = [_p(COMPANY, st["title"]), _p(title, st["sub"]), _p(subtitle, st["sub"])]
    t = Table([[logo, text]], colWidths=[22 * mm, width - 22 * mm])
    t.setStyle(
        _ts(
            [
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LINEBELOW", (0, 0), (-1, 0), 1.5, NAVY),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    return t


def _individual_page(r: DailyReportOut, st: dict[str, ParagraphStyle], width: float) -> list:
    received = calc.total_received(r.cash_received, r.pos_amount, r.bank_transfer, r.link_payment)
    diff = calc.difference(r.total_sales, received)
    verdict = calc.check_label(diff)
    rows: list[tuple[str, str]] = [
        ("Branch Name", r.branch_name),
        ("Report Date", r.report_date.strftime("%d-%b-%Y")),
        ("Total Sales", aed(r.total_sales)),
        ("Number of Orders", f"{r.order_count:,}"),
        ("Cash Received", aed(r.cash_received)),
        ("POS Received", aed(r.pos_amount)),
        ("Bank Transfers", aed(r.bank_transfer)),
        ("Payment Link Collections", aed(r.link_payment)),
        ("Total Collections", aed(received)),
        ("Shop Expenses", aed(r.shop_expenses)),
        ("Savings", aed(r.shop_savings)),
        ("Not Yet Received", aed(calc.not_yet_received(r.total_sales, received))),
        ("Reconciliation Difference", f"{aed(diff)}  ({verdict})"),
        ("Submission Date and Time", stamp(r.created_at)),
        ("Prepared By", r.prepared_by or "—"),
    ]
    data = [[_p(k, st["cell"]), _p(v, st["cell"])] for k, v in rows]
    t = Table(data, colWidths=[width * 0.4, width * 0.6])
    style = [
        ("GRID", (0, 0), (-1, -1), 0.5, GRID),
        ("BACKGROUND", (0, 0), (0, -1), LIGHT),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("BACKGROUND", (0, 2), (-1, 2), colors.white),
    ]
    for i in (2, 8, 12):  # headline rows
        style.append(("BACKGROUND", (0, i), (-1, i), LIGHT))
    t.setStyle(_ts(style))
    out: list = [
        _header(
            st,
            "Daily Sales Report",
            f"{r.branch_name} · {r.report_date.strftime('%d-%b-%Y')}",
            width,
        ),
        Spacer(1, 8),
        t,
    ]
    if r.other_expenses:
        white = ParagraphStyle(
            "w", parent=st["small"], textColor=colors.white, fontName="Helvetica-Bold"
        )
        whiter = ParagraphStyle("wr", parent=white, alignment=2)
        det = [[_p("Other expense", white), _p("Notes", white), _p("Amount", whiter)]]
        det += [
            [
                _p(i.description, st["small"]),
                _p(i.notes or "", st["small"]),
                _p(aed(i.amount), st["smallr"]),
            ]
            for i in r.other_expenses
        ]
        det.append(
            [
                _p("Total other expenses (included in Shop Expenses)", st["small"]),
                _p("", st["small"]),
                _p(aed(r.other_expenses_total), st["smallr"]),
            ]
        )
        dt = Table(det, colWidths=[width * 0.45, width * 0.33, width * 0.22], repeatRows=1)
        dt.setStyle(
            _ts(
                [
                    ("GRID", (0, 0), (-1, -1), 0.5, GRID),
                    ("BACKGROUND", (0, 0), (-1, 0), NAVY),
                    ("SPAN", (0, -1), (1, -1)),
                    ("BACKGROUND", (0, -1), (-1, -1), LIGHT),
                ]
            )
        )
        out += [Spacer(1, 10), _p("Other shop expenses", st["h2"]), dt]
    return out


_TOTAL_KEYS = (
    "total_sales",
    "cash_received",
    "pos_amount",
    "bank_transfer",
    "link_payment",
    "shop_expenses",
    "shop_savings",
)


def _sum(rows: list[DailyReportOut]) -> dict[str, Decimal]:
    t = {k: Decimal(0) for k in _TOTAL_KEYS}
    t["order_count"] = Decimal(0)
    for r in rows:
        for k in _TOTAL_KEYS:
            t[k] += getattr(r, k)
        t["order_count"] += r.order_count
    t["collections"] = t["cash_received"] + t["pos_amount"] + t["bank_transfer"] + t["link_payment"]
    t["not_received"] = t["total_sales"] - t["collections"]
    return t


def _consolidated(
    rows: list[DailyReportOut], st: dict[str, ParagraphStyle], width: float, subtitle: str
) -> list:
    head = [
        "Date",
        "Branch",
        "Orders",
        "Sales",
        "Cash",
        "POS",
        "Bank",
        "Links",
        "Collections",
        "Shop exp.",
        "Savings",
        "Not received",
        "Submitted / Prepared by",
    ]
    data: list[list] = [
        [Paragraph(f'<font color="white"><b>{escape(h)}</b></font>', st["small"]) for h in head]
    ]
    for r in rows:
        received = calc.total_received(
            r.cash_received, r.pos_amount, r.bank_transfer, r.link_payment
        )
        data.append(
            [
                _p(r.report_date.strftime("%d-%b-%Y"), st["small"]),
                _p(r.branch_name, st["small"]),
                _p(f"{r.order_count:,}", st["smallr"]),
                *[
                    _p(num(v), st["smallr"])
                    for v in (
                        r.total_sales,
                        r.cash_received,
                        r.pos_amount,
                        r.bank_transfer,
                        r.link_payment,
                        received,
                        r.shop_expenses,
                        r.shop_savings,
                        calc.not_yet_received(r.total_sales, received),
                    )
                ],
                _p(f"{stamp(r.created_at)} · {r.prepared_by or '—'}", st["small"]),
            ]
        )
    t = _sum(rows)

    def total_row(label: str, tot: dict[str, Decimal]) -> list:
        b = ParagraphStyle("b", parent=st["smallr"], fontName="Helvetica-Bold")
        bl = ParagraphStyle("bl", parent=st["small"], fontName="Helvetica-Bold")
        return [
            _p(label, bl), "", _p(f"{int(tot['order_count']):,}", b),
            *[_p(num(tot[k]), b) for k in ("total_sales", "cash_received", "pos_amount", "bank_transfer", "link_payment", "collections", "shop_expenses", "shop_savings", "not_received")],
            "",
        ]  # fmt: skip

    branches = sorted({r.branch_name for r in rows})
    if len(branches) > 1:
        for name in branches:
            data.append(
                total_row(f"Subtotal {name}", _sum([r for r in rows if r.branch_name == name]))
            )
    data.append(total_row("GRAND TOTAL", t))
    n_sub = len(branches) if len(branches) > 1 else 0
    cw = [0.078, 0.08, 0.05, 0.075, 0.07, 0.07, 0.065, 0.065, 0.08, 0.07, 0.06, 0.077, 0.16]
    tbl = Table(data, colWidths=[width * c for c in cw], repeatRows=1)
    last = len(data) - 1
    style = [
        ("GRID", (0, 0), (-1, -1), 0.4, GRID),
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        (
            "ROWBACKGROUNDS",
            (0, 1),
            (-1, last - n_sub - 1),
            [colors.white, colors.HexColor("#f7f9fc")],
        ),
        ("BACKGROUND", (0, last - n_sub), (-1, last), LIGHT),
        ("SPAN", (0, last), (1, last)),
        ("LINEABOVE", (0, last), (-1, last), 1, NAVY),
    ]
    for i in range(last - n_sub, last):
        style.append(("SPAN", (0, i), (1, i)))
    tbl.setStyle(_ts(style))
    summary = _p(
        f"{len(rows)} report(s) · Total sales {aed(t['total_sales'])} · Collections {aed(t['collections'])} · "
        f"Not yet received {aed(t['not_received'])} · all table amounts in AED",
        st["sub"],
    )
    return [
        _header(st, "Consolidated Daily Sales Report", subtitle, width),
        Spacer(1, 6),
        summary,
        Spacer(1, 6),
        tbl,
    ]


def build_pdf(
    reports: list[DailyReportOut],
    *,
    layout: Layout,
    branch_label: str,
    date_from: date,
    date_to: date,
    generated_by: str,
    generated_at: datetime,
) -> bytes:
    st = _styles()
    page = landscape(A4) if layout == "consolidated" else A4
    margin = 14 * mm
    width = page[0] - 2 * margin
    period = (
        date_from.strftime("%d-%b-%Y")
        if date_from == date_to
        else f"{date_from.strftime('%d-%b-%Y')} to {date_to.strftime('%d-%b-%Y')}"
    )
    story: list = []
    ordered = sorted(reports, key=lambda r: (r.report_date, r.branch_name))
    if layout == "consolidated":
        story = _consolidated(ordered, st, width, f"{branch_label} · {period}")
    else:
        for i, r in enumerate(ordered):
            if i:
                story.append(PageBreak())
            story += _individual_page(r, st, width)
    if not ordered:
        story = [
            _header(st, "Daily Sales Report", f"{branch_label} · {period}", width),
            Spacer(1, 12),
            _p("No daily reports were submitted for this selection.", st["cell"]),
        ]
    foot = f"Generated {dubai(generated_at).strftime('%d-%b-%Y %H:%M')} (Dubai) by {generated_by} · {COMPANY} · AED"

    def decorate(canvas, doc) -> None:  # noqa: ANN001
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(colors.HexColor("#64748b"))
        canvas.drawString(margin, 8 * mm, foot[:150])
        canvas.drawRightString(page[0] - margin, 8 * mm, f"Page {doc.page}")
        canvas.restoreState()

    buf = io.BytesIO()
    SimpleDocTemplate(
        buf, pagesize=page, leftMargin=margin, rightMargin=margin, topMargin=12 * mm, bottomMargin=16 * mm,
        title=f"{COMPANY} daily reports", author=COMPANY, subject=f"{branch_label} {period}",
    ).build(story, onFirstPage=decorate, onLaterPages=decorate)  # fmt: skip
    return buf.getvalue()

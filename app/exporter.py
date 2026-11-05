"""Excel export: one workbook with an "Invoices" sheet and a "Line Items" sheet.

The workbook is built in memory and returned as bytes, so nothing sensitive is
left lying around on disk.
"""

from __future__ import annotations

from io import BytesIO
from typing import Iterable

import pandas as pd
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from app.schemas import InvoiceResult

INVOICE_COLUMNS = [
    "File", "Vendor", "Invoice Number", "Invoice Date", "Due Date", "Currency",
    "Subtotal", "Tax", "Total Amount", "Totals Match", "Validation Notes",
    "Status", "Error",
]
LINE_COLUMNS = [
    "Invoice Number", "File", "Vendor", "Description", "Quantity",
    "Unit Price", "Line Total",
]
MONEY_COLUMNS = {"Subtotal", "Tax", "Total Amount", "Unit Price", "Line Total"}

HEADER_FILL = PatternFill("solid", start_color="1F4E78")
HEADER_FONT = Font(bold=True, color="FFFFFF")
MISMATCH_FILL = PatternFill("solid", start_color="F8CBAD")  # light red
ERROR_FILL = PatternFill("solid", start_color="D9D9D9")     # grey
MONEY_FORMAT = "#,##0.00"


def _invoice_rows(results: list[InvoiceResult]) -> list[dict]:
    rows = []
    for r in results:
        d = r.data
        rows.append({
            "File": r.filename,
            "Vendor": d.vendor_name if d else None,
            "Invoice Number": d.invoice_number if d else None,
            "Invoice Date": d.invoice_date if d else None,
            "Due Date": d.due_date if d else None,
            "Currency": d.currency if d else None,
            "Subtotal": d.subtotal if d else None,
            "Tax": d.tax if d else None,
            "Total Amount": d.total_amount if d else None,
            "Totals Match": ("YES" if d.totals_match else "NO") if d else None,
            "Validation Notes": d.validation_notes if d else None,
            "Status": r.status.upper(),
            "Error": r.error,
        })
    return rows


def _line_rows(results: list[InvoiceResult]) -> list[dict]:
    rows = []
    for r in results:
        if not r.data:
            continue
        for item in r.data.line_items:
            rows.append({
                "Invoice Number": r.data.invoice_number,
                "File": r.filename,
                "Vendor": r.data.vendor_name,
                "Description": item.description,
                "Quantity": item.quantity,
                "Unit Price": item.unit_price,
                "Line Total": item.line_total,
            })
    return rows


def _style_sheet(ws: Worksheet, row_fills: Iterable[PatternFill | None]) -> None:
    headers = [cell.value for cell in ws[1]]

    for cell in ws[1]:
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(vertical="center")
    ws.freeze_panes = "A2"
    if ws.max_row > 1:
        ws.auto_filter.ref = ws.dimensions

    for row_idx, fill in enumerate(row_fills, start=2):
        for cell in ws[row_idx]:
            if fill is not None:
                cell.fill = fill
            if headers[cell.column - 1] in MONEY_COLUMNS:
                cell.number_format = MONEY_FORMAT

    # Approximate auto-fit column widths.
    for col_idx, header in enumerate(headers, start=1):
        letter = get_column_letter(col_idx)
        longest = max(
            (len(str(c.value)) for c in ws[letter] if c.value is not None),
            default=len(str(header)),
        )
        ws.column_dimensions[letter].width = min(max(longest + 2, 10), 60)


def build_excel(results: list[InvoiceResult]) -> bytes:
    """Build the workbook and return it as ``.xlsx`` bytes."""
    invoices_df = pd.DataFrame(_invoice_rows(results), columns=INVOICE_COLUMNS)
    lines_df = pd.DataFrame(_line_rows(results), columns=LINE_COLUMNS)

    # Row highlight per invoice: red = totals mismatch, grey = failed file.
    invoice_fills: list[PatternFill | None] = []
    for r in results:
        if r.status == "error" or r.data is None:
            invoice_fills.append(ERROR_FILL)
        elif not r.data.totals_match:
            invoice_fills.append(MISMATCH_FILL)
        else:
            invoice_fills.append(None)

    line_fills: list[PatternFill | None] = [
        MISMATCH_FILL if not r.data.totals_match else None
        for r in results
        if r.data
        for _ in r.data.line_items
    ]

    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        invoices_df.to_excel(writer, sheet_name="Invoices", index=False)
        lines_df.to_excel(writer, sheet_name="Line Items", index=False)
        _style_sheet(writer.sheets["Invoices"], invoice_fills)
        _style_sheet(writer.sheets["Line Items"], line_fills)
    return buffer.getvalue()

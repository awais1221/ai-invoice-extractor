from io import BytesIO

from openpyxl import load_workbook

from app.exporter import INVOICE_COLUMNS, LINE_COLUMNS, MISMATCH_FILL, build_excel


def _load(results):
    return load_workbook(BytesIO(build_excel(results)))


def _fill_color(cell) -> str:
    return (cell.fill.start_color.rgb or "")[-6:] if cell.fill.fill_type else ""


def test_workbook_has_both_sheets(sample_results):
    wb = _load(sample_results)
    assert wb.sheetnames == ["Invoices", "Line Items"]


def test_headers(sample_results):
    wb = _load(sample_results)
    assert [c.value for c in wb["Invoices"][1]] == INVOICE_COLUMNS
    assert [c.value for c in wb["Line Items"][1]] == LINE_COLUMNS


def test_one_row_per_invoice_including_failures(sample_results):
    ws = _load(sample_results)["Invoices"]
    assert ws.max_row == 1 + len(sample_results)
    files = [ws.cell(row=r, column=1).value for r in range(2, ws.max_row + 1)]
    assert files == ["good.pdf", "bad.pdf", "broken.pdf"]
    assert ws.cell(row=4, column=INVOICE_COLUMNS.index("Status") + 1).value == "ERROR"


def test_line_items_linked_by_invoice_number(sample_results):
    ws = _load(sample_results)["Line Items"]
    # good + bad invoices have 2 line items each; the failed one has none.
    assert ws.max_row == 1 + 4
    numbers = [ws.cell(row=r, column=1).value for r in range(2, ws.max_row + 1)]
    assert numbers == ["INV-001", "INV-001", "INV-002", "INV-002"]


def test_mismatch_rows_are_highlighted(sample_results):
    wb = _load(sample_results)
    inv = wb["Invoices"]
    red = MISMATCH_FILL.start_color.rgb[-6:]
    match_col = INVOICE_COLUMNS.index("Totals Match") + 1

    assert inv.cell(row=2, column=match_col).value == "YES"
    assert _fill_color(inv.cell(row=2, column=1)) != red
    assert inv.cell(row=3, column=match_col).value == "NO"
    assert _fill_color(inv.cell(row=3, column=1)) == red

    lines = wb["Line Items"]
    assert _fill_color(lines.cell(row=2, column=1)) != red  # INV-001 lines
    assert _fill_color(lines.cell(row=4, column=1)) == red  # INV-002 lines


def test_money_values_are_numeric(sample_results):
    ws = _load(sample_results)["Invoices"]
    total = ws.cell(row=2, column=INVOICE_COLUMNS.index("Total Amount") + 1)
    assert total.value == 220
    assert total.number_format == "#,##0.00"


def test_long_notes_wrap_instead_of_being_cut_off(sample_results):
    ws = _load(sample_results)["Invoices"]
    notes_col = INVOICE_COLUMNS.index("Validation Notes") + 1
    cell = ws.cell(row=3, column=notes_col)
    assert "MISMATCH" in cell.value
    assert cell.alignment.wrap_text is True
    assert ws.cell(row=3, column=1).alignment.wrap_text is not True  # only notes wrap


def test_empty_results_still_produce_valid_workbook():
    wb = _load([])
    assert wb["Invoices"].max_row == 1
    assert wb["Line Items"].max_row == 1

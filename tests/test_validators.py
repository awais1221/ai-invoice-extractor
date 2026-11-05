import pytest

from app.schemas import InvoiceData, parse_currency, parse_date, parse_number
from app.validators import apply_validation, validate_totals
from tests.conftest import make_invoice


# ----------------------------- totals checks ------------------------------- #


def test_consistent_invoice_matches(good_invoice):
    ok, notes = validate_totals(good_invoice)
    assert ok is True
    assert notes.startswith("All totals consistent")


def test_line_items_do_not_sum_to_subtotal():
    inv = make_invoice(subtotal=250, total_amount=270)
    ok, notes = validate_totals(inv)
    assert ok is False
    assert "Line items sum 200.00 does not match subtotal 250.00" in notes
    assert "diff 50.00" in notes


def test_subtotal_plus_tax_does_not_equal_total():
    inv = make_invoice(total_amount=230)
    ok, notes = validate_totals(inv)
    assert ok is False
    assert "does not match total 230.00" in notes


def test_rounding_within_tolerance_passes():
    inv = make_invoice(total_amount=220.01)
    ok, _ = validate_totals(inv, tolerance=0.02)
    assert ok is True


def test_rounding_just_outside_tolerance_fails():
    inv = make_invoice(total_amount=220.05)
    ok, _ = validate_totals(inv, tolerance=0.02)
    assert ok is False


def test_float_noise_is_ignored():
    inv = make_invoice(
        line_items=[
            {"description": "a", "quantity": 1, "unit_price": 0.1, "line_total": 0.1},
            {"description": "b", "quantity": 1, "unit_price": 0.2, "line_total": 0.2},
        ],
        subtotal=0.3, tax=0, total_amount=0.3,
    )
    ok, _ = validate_totals(inv, tolerance=0)
    assert ok is True


def test_missing_tax_assumed_zero():
    inv = make_invoice(tax=None, total_amount=200)
    ok, notes = validate_totals(inv)
    assert ok is True
    assert "Tax missing; assumed 0" in notes


def test_missing_total_is_flagged():
    inv = make_invoice(total_amount=None)
    ok, notes = validate_totals(inv)
    assert ok is False
    assert "total amount missing" in notes


def test_missing_subtotal_uses_line_items_sum():
    inv = make_invoice(subtotal=None)
    ok, notes = validate_totals(inv)
    assert ok is True
    assert "Subtotal missing" in notes


def test_missing_subtotal_and_no_line_items_is_flagged():
    inv = make_invoice(subtotal=None, line_items=[])
    ok, notes = validate_totals(inv)
    assert ok is False
    assert "subtotal missing" in notes


def test_no_line_items_still_checks_subtotal_plus_tax():
    inv = make_invoice(line_items=[])
    ok, notes = validate_totals(inv)
    assert ok is True
    assert "No line items found" in notes


def test_line_total_derived_from_quantity_and_price():
    inv = make_invoice(
        line_items=[
            {"description": "Widget", "quantity": 2, "unit_price": 50, "line_total": None},
            {"description": "Service", "quantity": 1, "unit_price": 100},
        ]
    )
    ok, _ = validate_totals(inv)
    assert ok is True


def test_unknown_line_amount_is_flagged():
    inv = make_invoice(line_items=[{"description": "Mystery item"}])
    ok, notes = validate_totals(inv)
    assert ok is False
    assert "some line amounts are missing" in notes


def test_line_arithmetic_error_is_a_warning_only():
    inv = make_invoice(
        line_items=[
            {"description": "Widget", "quantity": 3, "unit_price": 50, "line_total": 100},
            {"description": "Service", "quantity": 1, "unit_price": 100, "line_total": 100},
        ]
    )
    ok, notes = validate_totals(inv)
    assert ok is True  # totals still agree with each other
    assert "'Widget': 3 x 50.00 = 150.00" in notes


def test_empty_invoice_does_not_crash():
    ok, notes = validate_totals(InvoiceData())
    assert ok is False
    assert "Cannot verify" in notes


def test_apply_validation_sets_fields():
    inv = apply_validation(make_invoice(total_amount=999))
    assert inv.totals_match is False
    assert "MISMATCH" in inv.validation_notes


# ------------------------- value cleaning (schemas) ------------------------ #


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("1,234.50", 1234.50),
        ("1.234,50", 1234.50),
        ("€ 10,00", 10.0),
        ("$99", 99.0),
        ("1,234", 1234.0),
        ("(12.50)", -12.5),
        (42, 42.0),
        ("N/A", None),
        ("", None),
        (None, None),
        ("abc", None),
    ],
)
def test_parse_number(raw, expected):
    assert parse_number(raw) == expected


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("2026-03-01", "2026-03-01"),
        ("01.03.2026", "2026-03-01"),
        ("March 1, 2026", "2026-03-01"),
        ("1st March 2026", "2026-03-01"),
        ("sometime soon", "sometime soon"),
        (None, None),
    ],
)
def test_parse_date(raw, expected):
    assert parse_date(raw) == expected


@pytest.mark.parametrize(
    "raw, expected", [("$", "USD"), ("eur", "EUR"), ("€", "EUR"), (None, None)]
)
def test_parse_currency(raw, expected):
    assert parse_currency(raw) == expected


def test_missing_fields_become_none():
    inv = InvoiceData.model_validate({"vendor_name": "Only Vendor"})
    assert inv.invoice_number is None
    assert inv.total_amount is None
    assert inv.line_items == []


def test_garbage_line_items_are_dropped():
    inv = InvoiceData.model_validate({"line_items": ["oops", None, {"description": "ok"}]})
    assert len(inv.line_items) == 1

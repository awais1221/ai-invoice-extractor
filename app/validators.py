"""Totals validation done in Python (the LLM is never asked to do math).

Checks performed:
1. Line items sum to the subtotal.
2. Subtotal + tax equals the total amount.
3. (Warning only) each line's quantity x unit price equals its line total.

A small rounding tolerance is allowed. If a check cannot be performed because
values are missing, the invoice is flagged (``totals_match = False``) so that
nothing unverified slips through silently.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Optional

from app.schemas import InvoiceData, LineItem

DEFAULT_TOLERANCE = 0.02


def _d(value: float) -> Decimal:
    """Float -> Decimal rounded to 2 places (avoids 0.1 + 0.2 float noise)."""
    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _fmt(value: Decimal) -> str:
    return f"{value:,.2f}"


def _close(a: Decimal, b: Decimal, tolerance: float) -> bool:
    return abs(a - b) <= Decimal(str(tolerance))


def _line_amount(item: LineItem) -> Optional[Decimal]:
    """The line total, or quantity x unit price when the total is missing."""
    if item.line_total is not None:
        return _d(item.line_total)
    if item.quantity is not None and item.unit_price is not None:
        return _d(item.quantity * item.unit_price)
    return None


def validate_totals(
    invoice: InvoiceData, tolerance: float = DEFAULT_TOLERANCE
) -> tuple[bool, str]:
    """Return ``(totals_match, validation_notes)`` for an invoice."""
    problems: list[str] = []   # make totals_match False
    warnings: list[str] = []   # informational only

    # --- Check 3: per-line arithmetic (warnings only) ---------------------- #
    for idx, item in enumerate(invoice.line_items, start=1):
        if None not in (item.quantity, item.unit_price, item.line_total):
            expected = _d(item.quantity * item.unit_price)
            actual = _d(item.line_total)
            if not _close(expected, actual, tolerance):
                label = item.description or f"line {idx}"
                warnings.append(
                    f"'{label}': {item.quantity:g} x {_fmt(_d(item.unit_price))} = "
                    f"{_fmt(expected)}, but line total shows {_fmt(actual)}"
                )

    # --- Check 1: line items vs subtotal ----------------------------------- #
    subtotal = _d(invoice.subtotal) if invoice.subtotal is not None else None
    amounts = [_line_amount(item) for item in invoice.line_items]

    if not invoice.line_items:
        warnings.append("No line items found; line-item check skipped")
    elif any(a is None for a in amounts):
        problems.append("Cannot verify line items: some line amounts are missing")
    elif subtotal is None:
        # No printed subtotal: the line items sum becomes the subtotal for check 2.
        subtotal = sum(amounts, Decimal("0"))
        warnings.append(
            f"Subtotal missing; using line items sum {_fmt(subtotal)} instead"
        )
    else:
        items_sum = sum(amounts, Decimal("0"))
        if not _close(items_sum, subtotal, tolerance):
            problems.append(
                f"Line items sum {_fmt(items_sum)} does not match subtotal "
                f"{_fmt(subtotal)} (diff {_fmt(abs(items_sum - subtotal))})"
            )

    # --- Check 2: subtotal + tax vs total ---------------------------------- #
    total = _d(invoice.total_amount) if invoice.total_amount is not None else None
    if total is None:
        problems.append("Cannot verify: total amount missing")
    elif subtotal is None:
        problems.append("Cannot verify: subtotal missing")
    else:
        if invoice.tax is None:
            tax = Decimal("0")
            warnings.append("Tax missing; assumed 0")
        else:
            tax = _d(invoice.tax)
        expected_total = subtotal + tax
        if not _close(expected_total, total, tolerance):
            problems.append(
                f"Subtotal {_fmt(subtotal)} + tax {_fmt(tax)} = "
                f"{_fmt(expected_total)} does not match total {_fmt(total)} "
                f"(diff {_fmt(abs(expected_total - total))})"
            )

    totals_match = not problems
    if totals_match:
        notes = ["All totals consistent"] + warnings
    else:
        notes = ["MISMATCH: " + p for p in problems] + warnings
    return totals_match, "; ".join(notes)


def apply_validation(
    invoice: InvoiceData, tolerance: float = DEFAULT_TOLERANCE
) -> InvoiceData:
    """Return a copy of the invoice with the validation fields filled in."""
    totals_match, notes = validate_totals(invoice, tolerance)
    return invoice.model_copy(
        update={"totals_match": totals_match, "validation_notes": notes}
    )

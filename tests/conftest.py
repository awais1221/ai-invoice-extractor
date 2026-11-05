"""Shared pytest fixtures. Tests never need a real API key."""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.schemas import InvoiceData, InvoiceResult  # noqa: E402
from app.validators import apply_validation  # noqa: E402


def make_invoice(**overrides) -> InvoiceData:
    """A consistent invoice: 2x50 + 1x100 = 200 subtotal, +20 tax = 220."""
    data = {
        "vendor_name": "Test Vendor Ltd",
        "invoice_number": "INV-001",
        "invoice_date": "2026-01-15",
        "due_date": "2026-02-14",
        "currency": "USD",
        "line_items": [
            {"description": "Widget", "quantity": 2, "unit_price": 50, "line_total": 100},
            {"description": "Service", "quantity": 1, "unit_price": 100, "line_total": 100},
        ],
        "subtotal": 200,
        "tax": 20,
        "total_amount": 220,
    }
    data.update(overrides)
    return InvoiceData.model_validate(data)


@pytest.fixture
def good_invoice() -> InvoiceData:
    return make_invoice()


@pytest.fixture
def sample_results() -> list[InvoiceResult]:
    good = apply_validation(make_invoice())
    bad = apply_validation(make_invoice(invoice_number="INV-002", subtotal=250, total_amount=270))
    return [
        InvoiceResult(filename="good.pdf", status="success", source="text", data=good),
        InvoiceResult(filename="bad.pdf", status="success", source="vision", data=bad),
        InvoiceResult(filename="broken.pdf", status="error", error="LLM returned invalid JSON"),
    ]

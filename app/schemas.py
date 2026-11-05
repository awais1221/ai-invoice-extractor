"""Pydantic models for extracted invoice data and API responses.

Every extracted field is optional: if the LLM omits a field (or returns
something unusable) it becomes ``None`` instead of crashing the request.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator

# --------------------------------------------------------------------------- #
# Cleaning helpers
# --------------------------------------------------------------------------- #

_NULL_STRINGS = {"", "n/a", "na", "none", "null", "-", "--", "nil", "unknown"}

_CURRENCY_SYMBOLS = {
    "$": "USD", "US$": "USD", "€": "EUR", "£": "GBP", "¥": "JPY", "₹": "INR",
    "₨": "PKR", "Rs": "PKR", "Rs.": "PKR", "C$": "CAD", "A$": "AUD", "CHF": "CHF",
    "zł": "PLN", "kr": "SEK", "R$": "BRL", "₩": "KRW", "₺": "TRY", "AED": "AED",
}

_DATE_FORMATS = (
    "%Y-%m-%d", "%Y/%m/%d", "%d.%m.%Y", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y",
    "%d %B %Y", "%d %b %Y", "%B %d, %Y", "%b %d, %Y", "%B %d %Y", "%b %d %Y",
    "%d %b, %Y", "%Y.%m.%d",
)


def parse_number(value: Any) -> Optional[float]:
    """Turn values like ``"1,234.50"``, ``"€ 1.234,50"``, ``"$99"`` or
    ``"(12.00)"`` into floats. Returns ``None`` for anything unparseable."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return None

    text = value.strip()
    if text.lower() in _NULL_STRINGS:
        return None

    negative = text.startswith("(") and text.endswith(")") or "-" in text[:2]
    # Keep only digits and separators.
    cleaned = re.sub(r"[^0-9.,]", "", text)
    if not cleaned or not re.search(r"\d", cleaned):
        return None

    if "," in cleaned and "." in cleaned:
        # Whichever separator comes last is the decimal separator.
        if cleaned.rfind(",") > cleaned.rfind("."):
            cleaned = cleaned.replace(".", "").replace(",", ".")  # 1.234,50
        else:
            cleaned = cleaned.replace(",", "")  # 1,234.50
    elif "," in cleaned:
        parts = cleaned.split(",")
        if len(parts) > 2 or (len(parts) == 2 and len(parts[1]) == 3):
            cleaned = cleaned.replace(",", "")  # 1,234 or 1,234,567
        else:
            cleaned = cleaned.replace(",", ".")  # 10,50
    elif cleaned.count(".") > 1:
        cleaned = cleaned.replace(".", "")  # 1.234.567

    try:
        number = float(cleaned)
    except ValueError:
        return None
    return -number if negative else number


def parse_date(value: Any) -> Optional[str]:
    """Normalise a date to ISO ``YYYY-MM-DD``. Unrecognised formats are kept
    as the original string so no information is lost."""
    if value is None:
        return None
    text = str(value).strip()
    if text.lower() in _NULL_STRINGS:
        return None
    candidate = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", text)  # 3rd -> 3
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(candidate, fmt).date().isoformat()
        except ValueError:
            continue
    return text


def parse_currency(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    if text.lower() in _NULL_STRINGS:
        return None
    if text in _CURRENCY_SYMBOLS:
        return _CURRENCY_SYMBOLS[text]
    if re.fullmatch(r"[A-Za-z]{3}", text):
        return text.upper()
    for symbol, code in _CURRENCY_SYMBOLS.items():
        if symbol in text:
            return code
    return text


def clean_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return None if text.lower() in _NULL_STRINGS else text


# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #


class LineItem(BaseModel):
    description: Optional[str] = None
    quantity: Optional[float] = None
    unit_price: Optional[float] = None
    line_total: Optional[float] = None

    @field_validator("description", mode="before")
    @classmethod
    def _text(cls, v: Any) -> Optional[str]:
        return clean_text(v)

    @field_validator("quantity", "unit_price", "line_total", mode="before")
    @classmethod
    def _numbers(cls, v: Any) -> Optional[float]:
        return parse_number(v)


class InvoiceData(BaseModel):
    """Structured invoice data. ``totals_match`` and ``validation_notes`` are
    always computed in Python (see ``validators.py``), never by the LLM."""

    vendor_name: Optional[str] = None
    invoice_number: Optional[str] = None
    invoice_date: Optional[str] = None
    due_date: Optional[str] = None
    currency: Optional[str] = None
    line_items: list[LineItem] = Field(default_factory=list)
    subtotal: Optional[float] = None
    tax: Optional[float] = None
    total_amount: Optional[float] = None
    totals_match: bool = False
    validation_notes: str = ""

    @field_validator("vendor_name", "invoice_number", mode="before")
    @classmethod
    def _text(cls, v: Any) -> Optional[str]:
        return clean_text(v)

    @field_validator("invoice_date", "due_date", mode="before")
    @classmethod
    def _dates(cls, v: Any) -> Optional[str]:
        return parse_date(v)

    @field_validator("currency", mode="before")
    @classmethod
    def _currency(cls, v: Any) -> Optional[str]:
        return parse_currency(v)

    @field_validator("subtotal", "tax", "total_amount", mode="before")
    @classmethod
    def _numbers(cls, v: Any) -> Optional[float]:
        return parse_number(v)

    @field_validator("line_items", mode="before")
    @classmethod
    def _items(cls, v: Any) -> list:
        if not isinstance(v, list):
            return []
        # Drop junk entries (strings, nulls) rather than failing the invoice.
        return [item for item in v if isinstance(item, dict)]


class InvoiceResult(BaseModel):
    filename: str
    status: Literal["success", "error"]
    source: Optional[Literal["text", "vision"]] = None
    data: Optional[InvoiceData] = None
    error: Optional[str] = None


class BatchResponse(BaseModel):
    batch_id: str
    total_files: int
    succeeded: int
    failed: int
    results: list[InvoiceResult]

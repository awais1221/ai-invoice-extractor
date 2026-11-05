"""Extractor tests with the LLM mocked out (no API key / network needed)."""

import json

import pymupdf
import pytest

from app import extractor, llm_client
from app.extractor import ExtractionError, parse_llm_json

VALID_PAYLOAD = {
    "vendor_name": "Mock Supplies Inc",
    "invoice_number": "M-100",
    "invoice_date": "2026-05-01",
    "due_date": None,
    "currency": "USD",
    "line_items": [
        {"description": "Paper", "quantity": 10, "unit_price": 5, "line_total": 50}
    ],
    "subtotal": 50,
    "tax": 5,
    "total_amount": 55,
}


class FakeLLM:
    """Returns queued replies and records every call."""

    def __init__(self, *replies: str):
        self.replies = list(replies)
        self.calls: list[list[dict]] = []

    def __call__(self, messages):
        self.calls.append(messages)
        return self.replies.pop(0)


@pytest.fixture
def text_pdf() -> bytes:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "INVOICE M-100 from Mock Supplies Inc. Total due: 55.00 USD")
    page.insert_text((72, 100), "Paper 10 x 5.00 = 50.00   Subtotal 50.00   Tax 5.00")
    return doc.tobytes()


@pytest.fixture
def blank_pdf() -> bytes:
    doc = pymupdf.open()
    doc.new_page()
    return doc.tobytes()


def install(monkeypatch, fake):
    monkeypatch.setattr(llm_client, "call_llm", fake)
    return fake


# ------------------------------ JSON parsing ------------------------------- #


def test_parse_plain_json():
    inv = parse_llm_json(json.dumps(VALID_PAYLOAD))
    assert inv.vendor_name == "Mock Supplies Inc"
    assert inv.total_amount == 55


def test_parse_json_in_code_fence():
    raw = "Here you go:\n```json\n" + json.dumps(VALID_PAYLOAD) + "\n```"
    assert parse_llm_json(raw).invoice_number == "M-100"


def test_parse_json_with_surrounding_text():
    raw = "Sure! " + json.dumps(VALID_PAYLOAD) + " Hope this helps."
    assert parse_llm_json(raw).currency == "USD"


def test_llm_validation_fields_are_ignored():
    payload = dict(VALID_PAYLOAD, totals_match=True, validation_notes="trust me")
    inv = parse_llm_json(json.dumps(payload))
    assert inv.validation_notes == ""


@pytest.mark.parametrize("raw", ["", "not json at all", "{broken: json", "[1, 2]"])
def test_parse_invalid_json_raises(raw):
    with pytest.raises(ValueError):
        parse_llm_json(raw)


# ------------------------------- retry logic ------------------------------- #


def test_valid_json_first_try(monkeypatch, text_pdf):
    fake = install(monkeypatch, FakeLLM(json.dumps(VALID_PAYLOAD)))
    result = extractor.extract_invoice("inv.pdf", text_pdf)
    assert result.status == "success"
    assert result.source == "text"
    assert result.data.totals_match is True
    assert len(fake.calls) == 1
    # The PDF text (not an image) was sent to the LLM.
    assert "Mock Supplies Inc" in fake.calls[0][1]["content"]


def test_invalid_then_valid_retries_once(monkeypatch, text_pdf):
    fake = install(monkeypatch, FakeLLM("oops, no json", json.dumps(VALID_PAYLOAD)))
    result = extractor.extract_invoice("inv.pdf", text_pdf)
    assert result.status == "success"
    assert len(fake.calls) == 2
    assert "not valid JSON" in fake.calls[1][-1]["content"]


def test_invalid_twice_raises_clear_error(monkeypatch, text_pdf):
    fake = install(monkeypatch, FakeLLM("garbage", "still garbage"))
    with pytest.raises(ExtractionError, match="invalid JSON after one retry"):
        extractor.extract_invoice("inv.pdf", text_pdf)
    assert len(fake.calls) == 2


def test_safe_extract_returns_error_result(monkeypatch, text_pdf):
    install(monkeypatch, FakeLLM("garbage", "still garbage"))
    result = extractor.safe_extract("inv.pdf", text_pdf)
    assert result.status == "error"
    assert "invalid JSON" in result.error


def test_missing_fields_become_null(monkeypatch, text_pdf):
    install(monkeypatch, FakeLLM(json.dumps({"vendor_name": "Partial Co"})))
    result = extractor.extract_invoice("inv.pdf", text_pdf)
    assert result.data.vendor_name == "Partial Co"
    assert result.data.total_amount is None
    assert result.data.totals_match is False
    assert "Cannot verify" in result.data.validation_notes


def test_wrong_totals_are_flagged(monkeypatch, text_pdf):
    install(monkeypatch, FakeLLM(json.dumps(dict(VALID_PAYLOAD, total_amount=60))))
    result = extractor.extract_invoice("inv.pdf", text_pdf)
    assert result.data.totals_match is False
    assert "MISMATCH" in result.data.validation_notes


# --------------------------- vision path & errors -------------------------- #


def test_scanned_pdf_uses_vision(monkeypatch, blank_pdf):
    fake = install(monkeypatch, FakeLLM(json.dumps(VALID_PAYLOAD)))
    result = extractor.extract_invoice("scan.pdf", blank_pdf)
    assert result.source == "vision"
    parts = fake.calls[0][1]["content"]
    assert parts[1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_image_upload_uses_vision(monkeypatch, blank_pdf):
    png = pymupdf.open(stream=blank_pdf, filetype="pdf")[0].get_pixmap().tobytes("png")
    fake = install(monkeypatch, FakeLLM(json.dumps(VALID_PAYLOAD)))
    result = extractor.extract_invoice("photo.png", png)
    assert result.source == "vision"
    assert len(fake.calls) == 1


def test_unsupported_file_type_never_calls_llm(monkeypatch):
    fake = install(monkeypatch, FakeLLM())
    result = extractor.safe_extract("notes.txt", b"hello")
    assert result.status == "error"
    assert "Unsupported file type" in result.error
    assert fake.calls == []


def test_renamed_file_with_wrong_content_is_rejected(monkeypatch):
    install(monkeypatch, FakeLLM())
    result = extractor.safe_extract("fake.pdf", b"this is not a pdf")
    assert result.status == "error"
    assert "not a valid PDF" in result.error


def test_llm_not_configured_gives_clear_error(monkeypatch, text_pdf):
    def not_configured(_messages):
        raise llm_client.LLMNotConfiguredError("LLM is not configured.")

    monkeypatch.setattr(llm_client, "call_llm", not_configured)
    result = extractor.safe_extract("inv.pdf", text_pdf)
    assert result.status == "error"
    assert "not configured" in result.error

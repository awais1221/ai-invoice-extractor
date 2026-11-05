"""Turns an uploaded invoice into validated, structured data.

Pipeline: validate file -> extract text or images -> ask the LLM for JSON ->
parse + validate with Pydantic (retry once on invalid JSON) -> check totals in
Python -> ``InvoiceResult``.
"""

from __future__ import annotations

import base64
import json
import logging
import re

from pydantic import ValidationError

from app import llm_client
from app.config import get_settings
from app.file_handler import ExtractedContent, FileValidationError, extract_content
from app.schemas import InvoiceData, InvoiceResult
from app.validators import apply_validation

logger = logging.getLogger(__name__)


class ExtractionError(Exception):
    """The LLM response could not be turned into invoice data."""


SYSTEM_PROMPT = """You are an expert invoice data extraction system.
Extract the invoice data and reply with ONLY a JSON object (no markdown, no
commentary) using exactly this structure:

{
  "vendor_name": string | null,       // the company that issued the invoice
  "invoice_number": string | null,
  "invoice_date": string | null,      // YYYY-MM-DD if possible
  "due_date": string | null,          // YYYY-MM-DD if possible
  "currency": string | null,          // ISO code, e.g. USD, EUR, GBP
  "line_items": [
    {"description": string | null, "quantity": number | null,
     "unit_price": number | null, "line_total": number | null}
  ],
  "subtotal": number | null,
  "tax": number | null,               // total tax/VAT amount, not the rate
  "total_amount": number | null       // the final amount due
}

Rules:
- Copy numbers EXACTLY as printed on the invoice. Do NOT calculate, fix or
  "correct" any totals, even if they look wrong.
- Write numbers as plain JSON numbers without currency symbols or thousands
  separators (e.g. 1234.5).
- Use null for anything that is not present. Never invent values.
- The vendor is the seller/issuer, not the "Bill To" customer."""

RETRY_PROMPT = (
    "Your previous reply was not valid JSON for the required structure "
    "(error: {error}). Reply again with ONLY the JSON object, nothing else."
)


def build_messages(content: ExtractedContent) -> list[dict]:
    if content.kind == "text":
        user_content: str | list[dict] = (
            "Extract the data from this invoice text:\n\n" + content.text
        )
    else:
        user_content = [{"type": "text", "text": "Extract the data from this invoice image:"}]
        for mime, data in content.images:
            b64 = base64.b64encode(data).decode("ascii")
            user_content.append(
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}
            )
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]


def parse_llm_json(raw: str) -> InvoiceData:
    """Parse the LLM reply into ``InvoiceData``. Raises ``ValueError``."""
    if not raw or not raw.strip():
        raise ValueError("empty response")
    text = raw.strip()
    # Strip ```json ... ``` fences that some models add anyway.
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL | re.IGNORECASE)
    if fenced:
        text = fenced.group(1).strip()
    # Fall back to the outermost {...} block if there is surrounding text.
    if not text.startswith("{"):
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise ValueError("no JSON object found")
        text = text[start : end + 1]

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON: {exc.msg} at position {exc.pos}") from exc
    if isinstance(payload, list) and payload and isinstance(payload[0], dict):
        payload = payload[0]  # some models wrap the object in a list
    if not isinstance(payload, dict):
        raise ValueError("JSON is not an object")

    # Never trust validation fields from the model; they are computed in Python.
    payload.pop("totals_match", None)
    payload.pop("validation_notes", None)
    try:
        return InvoiceData.model_validate(payload)
    except ValidationError as exc:
        raise ValueError(f"schema validation failed: {exc.error_count()} error(s)") from exc


def extract_with_retry(messages: list[dict]) -> InvoiceData:
    """Call the LLM; if the reply is not valid JSON, retry exactly once."""
    raw = llm_client.call_llm(messages)
    try:
        return parse_llm_json(raw)
    except ValueError as first_error:
        logger.warning("Invalid JSON from LLM (%s); retrying once", first_error)
        retry_messages = messages + [
            {"role": "assistant", "content": raw or ""},
            {"role": "user", "content": RETRY_PROMPT.format(error=first_error)},
        ]
        raw = llm_client.call_llm(retry_messages)
        try:
            return parse_llm_json(raw)
        except ValueError as second_error:
            raise ExtractionError(
                f"LLM returned invalid JSON after one retry ({second_error})."
            ) from second_error


def extract_invoice(filename: str, content: bytes) -> InvoiceResult:
    """Full pipeline for one file. Raises on failure (see ``safe_extract``)."""
    logger.info("Processing %s (%.1f KB)", filename, len(content) / 1024)
    extracted = extract_content(filename, content)
    logger.info("%s: using %s extraction", filename, extracted.kind)

    invoice = extract_with_retry(build_messages(extracted))
    invoice = apply_validation(invoice, get_settings().totals_tolerance)
    logger.info("%s: done, totals_match=%s", filename, invoice.totals_match)
    return InvoiceResult(
        filename=filename, status="success", source=extracted.kind, data=invoice
    )


def safe_extract(filename: str, content: bytes) -> InvoiceResult:
    """Like ``extract_invoice`` but never raises: errors become error results.
    Used by the batch endpoint so one bad file cannot break the others."""
    try:
        return extract_invoice(filename, content)
    except FileValidationError as exc:
        message = exc.message
    except (ExtractionError, llm_client.LLMError, llm_client.LLMNotConfiguredError) as exc:
        message = str(exc)
    except Exception:  # pragma: no cover - last-resort safety net
        logger.exception("Unexpected error while processing %s", filename)
        message = "Unexpected error while processing this file."
    logger.warning("%s: failed - %s", filename, message)
    return InvoiceResult(filename=filename, status="error", error=message)

"""API tests using FastAPI's TestClient with the LLM mocked."""

import json
from io import BytesIO

import pymupdf
import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook

from app import llm_client
from app.main import app
from tests.test_extractor import VALID_PAYLOAD


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def pdf_bytes() -> bytes:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "INVOICE M-100 Mock Supplies Inc Paper 10 x 5.00 Total 55.00")
    return doc.tobytes()


@pytest.fixture(autouse=True)
def mock_llm(monkeypatch):
    monkeypatch.setattr(llm_client, "call_llm", lambda _m: json.dumps(VALID_PAYLOAD))


def _pdf(name, data):
    return ("files", (name, data, "application/pdf"))


def test_health(client):
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert "api_key" not in json.dumps(body).lower()


def test_index_page_served(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "AI Invoice Extractor" in r.text


def test_analyze_single_invoice(client, pdf_bytes):
    r = client.post("/analyze-invoice", files={"file": ("inv.pdf", pdf_bytes, "application/pdf")})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "success"
    assert body["data"]["vendor_name"] == "Mock Supplies Inc"
    assert body["data"]["totals_match"] is True


def test_unsupported_type_returns_400(client):
    r = client.post("/analyze-invoice", files={"file": ("notes.txt", b"hi", "text/plain")})
    assert r.status_code == 400
    assert "Unsupported file type" in r.json()["detail"]


def test_empty_file_returns_400(client):
    r = client.post("/analyze-invoice", files={"file": ("empty.pdf", b"", "application/pdf")})
    assert r.status_code == 400
    assert "empty" in r.json()["detail"]


def test_oversize_file_returns_413(client, monkeypatch):
    from app import config

    small = config.Settings(**{**config.get_settings().__dict__, "max_file_mb": 1})
    monkeypatch.setattr("app.main.get_settings", lambda: small)
    monkeypatch.setattr("app.file_handler.get_settings", lambda: small)
    big = b"%PDF" + b"0" * (1024 * 1024 + 10)
    r = client.post("/analyze-invoice", files={"file": ("big.pdf", big, "application/pdf")})
    assert r.status_code == 413
    assert "too large" in r.json()["detail"]


def test_invalid_llm_json_returns_502(client, pdf_bytes, monkeypatch):
    monkeypatch.setattr(llm_client, "call_llm", lambda _m: "not json")
    r = client.post("/analyze-invoice", files={"file": ("inv.pdf", pdf_bytes, "application/pdf")})
    assert r.status_code == 502
    assert "invalid JSON" in r.json()["detail"]


def test_batch_one_bad_file_does_not_break_others(client, pdf_bytes):
    files = [
        _pdf("a.pdf", pdf_bytes),
        ("files", ("bad.txt", b"nope", "text/plain")),
        _pdf("b.pdf", pdf_bytes),
    ]
    r = client.post("/analyze-batch", files=files)
    assert r.status_code == 200
    body = r.json()
    assert body["total_files"] == 3
    assert body["succeeded"] == 2
    assert body["failed"] == 1
    statuses = {res["filename"]: res["status"] for res in body["results"]}
    assert statuses == {"a.pdf": "success", "bad.txt": "error", "b.pdf": "success"}


def test_export_excel_for_batch(client, pdf_bytes):
    batch = client.post("/analyze-batch", files=[_pdf("a.pdf", pdf_bytes)]).json()
    r = client.get("/export-excel", params={"batch_id": batch["batch_id"]})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    assert "attachment" in r.headers["content-disposition"]
    wb = load_workbook(BytesIO(r.content))
    assert wb.sheetnames == ["Invoices", "Line Items"]


def test_export_unknown_batch_returns_404(client):
    r = client.get("/export-excel", params={"batch_id": "does-not-exist"})
    assert r.status_code == 404

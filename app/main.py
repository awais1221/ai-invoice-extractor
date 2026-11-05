"""FastAPI application: API endpoints plus the upload web page."""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections import OrderedDict
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.config import PROJECT_ROOT, get_settings
from app.exporter import build_excel
from app.extractor import ExtractionError, extract_invoice, safe_extract
from app.file_handler import FileValidationError
from app.llm_client import LLMError, LLMNotConfiguredError
from app.logging_config import setup_logging
from app.schemas import BatchResponse, InvoiceResult

logger = logging.getLogger("app")

STATIC_DIR = PROJECT_ROOT / "static"
MAX_BATCH_FILES = 20
MAX_STORED_BATCHES = 20
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

# Recent batch results, kept in memory so the Excel can be downloaded later.
_batches: "OrderedDict[str, list[InvoiceResult]]" = OrderedDict()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    setup_logging()
    settings = get_settings()
    if settings.llm_configured:
        logger.info("LLM configured: model=%s", settings.model_name)  # never the key
    else:
        logger.warning(
            "LLM is NOT configured - copy .env.example to .env and fill in "
            "BASE_URL, API_KEY and MODEL_NAME. The UI will load, but extraction "
            "requests will fail with a clear error."
        )
    yield


app = FastAPI(
    title="AI Invoice Extractor",
    description="Upload invoices (PDF/JPG/PNG), extract structured data with an "
    "LLM, validate the totals and export everything to Excel.",
    version=__version__,
    lifespan=lifespan,
)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


async def _read_upload(upload: UploadFile) -> bytes:
    """Read at most limit+1 bytes so huge uploads are rejected cheaply."""
    limit = get_settings().max_file_bytes
    return await upload.read(limit + 1)


def _store_batch(results: list[InvoiceResult]) -> str:
    batch_id = uuid.uuid4().hex[:12]
    _batches[batch_id] = results
    while len(_batches) > MAX_STORED_BATCHES:
        _batches.popitem(last=False)
    return batch_id


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health", tags=["system"])
async def health() -> dict:
    settings = get_settings()
    return {
        "status": "ok",
        "version": __version__,
        "llm_configured": settings.llm_configured,
        "model": settings.model_name or None,
        "max_file_mb": settings.max_file_mb,
    }


@app.post("/analyze-invoice", response_model=InvoiceResult, tags=["invoices"])
async def analyze_invoice(file: UploadFile = File(...)) -> InvoiceResult:
    """Extract data from a single invoice and return it as JSON."""
    filename = file.filename or "upload"
    content = await _read_upload(file)
    try:
        return await asyncio.to_thread(extract_invoice, filename, content)
    except FileValidationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message)
    except LLMNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except (LLMError, ExtractionError) as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@app.post("/analyze-batch", response_model=BatchResponse, tags=["invoices"])
async def analyze_batch(files: list[UploadFile] = File(...)) -> BatchResponse:
    """Process several invoices. A failing file is reported in its own result
    and never stops the others. Use the returned ``batch_id`` with
    ``GET /export-excel`` to download the combined Excel file."""
    if not files:
        raise HTTPException(status_code=400, detail="No files uploaded.")
    if len(files) > MAX_BATCH_FILES:
        raise HTTPException(
            status_code=400, detail=f"Too many files (max {MAX_BATCH_FILES} per batch)."
        )

    uploads = [(f.filename or f"file_{i + 1}", await _read_upload(f)) for i, f in enumerate(files)]
    semaphore = asyncio.Semaphore(get_settings().batch_concurrency)

    async def process(name: str, data: bytes) -> InvoiceResult:
        async with semaphore:
            return await asyncio.to_thread(safe_extract, name, data)

    results = list(await asyncio.gather(*(process(n, d) for n, d in uploads)))
    succeeded = sum(r.status == "success" for r in results)
    logger.info("Batch done: %d/%d succeeded", succeeded, len(results))

    return BatchResponse(
        batch_id=_store_batch(results),
        total_files=len(results),
        succeeded=succeeded,
        failed=len(results) - succeeded,
        results=results,
    )


@app.get("/export-excel", tags=["invoices"])
async def export_excel(
    batch_id: Optional[str] = Query(None, description="ID returned by /analyze-batch. "
                                    "Defaults to the most recent batch."),
) -> Response:
    """Download a batch as an Excel file ("Invoices" + "Line Items" sheets)."""
    if not _batches:
        raise HTTPException(status_code=404, detail="No batches yet. Analyze some invoices first.")
    if batch_id is None:
        batch_id = next(reversed(_batches))
    results = _batches.get(batch_id)
    if results is None:
        raise HTTPException(status_code=404, detail="Batch not found (it may have expired).")

    content = await asyncio.to_thread(build_excel, results)
    filename = f"invoices_{datetime.now():%Y%m%d_%H%M%S}.xlsx"
    return Response(
        content=content,
        media_type=XLSX_MIME,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )

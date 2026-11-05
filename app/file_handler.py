"""Upload validation and content extraction (text or page images).

PDF strategy:
* Digital PDFs have a text layer, so the text is sent to the LLM (cheap, accurate).
* Scanned PDFs have (almost) no text, so pages are rendered to PNG and sent to
  the vision model instead.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import pymupdf

from app.config import get_settings

logger = logging.getLogger(__name__)

ALLOWED_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png"}
MIN_TEXT_CHARS_PER_PAGE = 30
RENDER_DPI = 150
MAX_TEXT_CHARS = 30_000  # keeps prompts small for free-tier limits


class FileValidationError(Exception):
    """The uploaded file was rejected. ``status_code`` maps to HTTP."""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


@dataclass
class ExtractedContent:
    kind: Literal["text", "vision"]
    text: str = ""
    images: list[tuple[str, bytes]] = field(default_factory=list)  # (mime, bytes)


def _detect_type(content: bytes) -> str | None:
    if content.startswith(b"%PDF"):
        return "pdf"
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if content.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    return None


def validate_upload(filename: str, content: bytes) -> str:
    """Validate an upload and return its real type: ``pdf``, ``png`` or ``jpeg``."""
    settings = get_settings()
    ext = Path(filename or "").suffix.lower()

    if ext not in ALLOWED_EXTENSIONS:
        raise FileValidationError(
            f"Unsupported file type '{ext or 'none'}'. Allowed: PDF, JPG, PNG."
        )
    if not content:
        raise FileValidationError("The file is empty.")
    if len(content) > settings.max_file_bytes:
        raise FileValidationError(
            f"File is too large. The limit is {settings.max_file_mb} MB per file.",
            status_code=413,
        )

    detected = _detect_type(content)
    if detected is None:
        raise FileValidationError(
            "File content is not a valid PDF, JPG or PNG (it may be corrupted "
            "or renamed)."
        )
    expected = "pdf" if ext == ".pdf" else ("png" if ext == ".png" else "jpeg")
    if detected != expected:
        logger.info("%s: extension %s but content is %s; using content type",
                    filename, ext, detected)
    return detected


def extract_pdf(content: bytes) -> ExtractedContent:
    settings = get_settings()
    try:
        doc = pymupdf.open(stream=content, filetype="pdf")
    except Exception as exc:  # corrupted PDF
        raise FileValidationError(f"Could not open PDF: {exc}") from exc

    with doc:
        if doc.page_count == 0:
            raise FileValidationError("The PDF has no pages.")
        if doc.needs_pass:
            raise FileValidationError("The PDF is password protected.")

        pages_text = [page.get_text("text") for page in doc]
        text = "\n\n--- page break ---\n\n".join(t.strip() for t in pages_text)
        chars = sum(len(t.strip()) for t in pages_text)

        if chars >= MIN_TEXT_CHARS_PER_PAGE * doc.page_count:
            return ExtractedContent(kind="text", text=text[:MAX_TEXT_CHARS])

        # Scanned / image-only PDF -> render pages for the vision model.
        images = []
        for page in doc.pages(0, min(doc.page_count, settings.max_pdf_pages)):
            pix = page.get_pixmap(dpi=RENDER_DPI)
            images.append(("image/png", pix.tobytes("png")))
        return ExtractedContent(kind="vision", images=images)


def extract_content(filename: str, content: bytes) -> ExtractedContent:
    """Validate the upload and return what should be sent to the LLM."""
    file_type = validate_upload(filename, content)
    if file_type == "pdf":
        return extract_pdf(content)
    mime = "image/png" if file_type == "png" else "image/jpeg"
    return ExtractedContent(kind="vision", images=[(mime, content)])

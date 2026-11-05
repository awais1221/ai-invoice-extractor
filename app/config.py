"""Application settings loaded from environment variables / a .env file."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Load .env from the project root (does nothing if the file does not exist).
load_dotenv(PROJECT_ROOT / ".env")


def _get_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return default


def _get_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    base_url: str
    api_key: str
    model_name: str
    max_file_mb: int
    max_pdf_pages: int
    totals_tolerance: float
    llm_timeout: int
    batch_concurrency: int
    rate_limit_retries: int = 2
    rate_limit_max_wait: int = 65

    @property
    def max_file_bytes(self) -> int:
        return self.max_file_mb * 1024 * 1024

    @property
    def llm_configured(self) -> bool:
        """True when enough settings exist to call an LLM."""
        placeholder_markers = ("your-", "your_", "xxx", "changeme")
        key = self.api_key.strip().lower()
        has_key = bool(key) and not any(m in key for m in placeholder_markers)
        return has_key and bool(self.base_url) and bool(self.model_name)


@lru_cache
def get_settings() -> Settings:
    return Settings(
        base_url=os.getenv("BASE_URL", "").strip(),
        api_key=os.getenv("API_KEY", "").strip(),
        model_name=os.getenv("MODEL_NAME", "").strip(),
        max_file_mb=_get_int("MAX_FILE_MB", 10),
        max_pdf_pages=_get_int("MAX_PDF_PAGES", 5),
        totals_tolerance=_get_float("TOTALS_TOLERANCE", 0.02),
        llm_timeout=_get_int("LLM_TIMEOUT", 60),
        batch_concurrency=max(1, _get_int("BATCH_CONCURRENCY", 2)),
        rate_limit_retries=max(0, _get_int("RATE_LIMIT_RETRIES", 2)),
        rate_limit_max_wait=_get_int("RATE_LIMIT_MAX_WAIT", 65),
    )

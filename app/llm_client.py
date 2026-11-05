"""Provider-agnostic LLM access via the OpenAI-compatible API.

Any provider exposing an OpenAI-compatible ``/chat/completions`` endpoint works
(Google Gemini, GitHub Models, Groq, Ollama, OpenAI, OpenRouter, ...). Switch
providers by editing BASE_URL / API_KEY / MODEL_NAME in ``.env``.

``call_llm`` is the only function that touches the network, which makes it the
single place tests need to mock.
"""

from __future__ import annotations

import logging
import re
import time
from functools import lru_cache

from openai import (
    APIConnectionError,
    APIError,
    APITimeoutError,
    BadRequestError,
    OpenAI,
    RateLimitError,
)

from app.config import get_settings

logger = logging.getLogger(__name__)


class LLMNotConfiguredError(Exception):
    """BASE_URL / API_KEY / MODEL_NAME are missing from .env."""


class LLMError(Exception):
    """The provider returned an error (rate limit, auth, timeout, ...)."""


_json_mode_supported = True  # flipped off if the provider rejects response_format


@lru_cache
def _get_client() -> OpenAI:
    settings = get_settings()
    return OpenAI(
        base_url=settings.base_url,
        api_key=settings.api_key,
        timeout=settings.llm_timeout,
        max_retries=2,  # SDK retries short network blips / 5xx with backoff
    )


def _retry_delay(exc: RateLimitError) -> float:
    """Seconds the provider asks us to wait after a 429 (default 20s).
    Uses the Retry-After header, or Gemini's "retry in 49.5s" / retryDelay text."""
    header = exc.response.headers.get("retry-after") if exc.response is not None else None
    if header:
        try:
            return float(header)
        except ValueError:
            pass
    match = re.search(r"retry in ([\d.]+)\s*s|retryDelay['\"]?\s*:\s*['\"]([\d.]+)s",
                      f"{exc.body} {exc}", re.IGNORECASE)
    if match:
        return float(match.group(1) or match.group(2))
    return 20.0


def _human_duration(seconds: float) -> str:
    minutes = round(seconds / 60)
    if minutes < 1:
        return f"{seconds:.0f} seconds"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m" if hours else f"{minutes} min"


def _create(client: OpenAI, kwargs: dict):
    """One chat completion, using JSON mode when the provider supports it."""
    global _json_mode_supported
    if _json_mode_supported:
        try:
            return client.chat.completions.create(
                **kwargs, response_format={"type": "json_object"}
            )
        except BadRequestError as exc:
            if "response_format" not in str(exc).lower():
                raise
            logger.info("Provider does not support JSON mode; falling back")
            _json_mode_supported = False
    return client.chat.completions.create(**kwargs)


def _create_with_rate_limit_retry(client: OpenAI, kwargs: dict, settings):
    """Free tiers allow only a few requests per minute. On a 429, wait as long
    as the provider asks (capped) and try again a limited number of times."""
    for attempt in range(settings.rate_limit_retries + 1):
        try:
            return _create(client, kwargs)
        except RateLimitError as exc:
            delay = _retry_delay(exc)
            if delay > settings.rate_limit_max_wait:
                # Not a per-minute limit: typically the free daily quota is used up.
                logger.warning("Provider quota exhausted; retry possible in %s",
                               _human_duration(delay))
                raise LLMError(
                    "The free quota for this model is used up (it resets in about "
                    f"{_human_duration(delay)}). Try again later, or set a different "
                    "MODEL_NAME in .env (each model has its own free quota)."
                ) from exc
            if attempt >= settings.rate_limit_retries:
                raise
            wait = delay + 1  # small safety margin
            logger.info("Rate limited by provider; waiting %.0fs before retry %d/%d",
                        wait, attempt + 1, settings.rate_limit_retries)
            time.sleep(wait)


def call_llm(messages: list[dict]) -> str:
    """Send chat messages and return the assistant's raw text reply."""
    settings = get_settings()
    if not settings.llm_configured:
        raise LLMNotConfiguredError(
            "LLM is not configured. Copy .env.example to .env and set "
            "BASE_URL, API_KEY and MODEL_NAME."
        )

    client = _get_client()
    kwargs = {"model": settings.model_name, "messages": messages, "temperature": 0}
    started = time.perf_counter()
    try:
        response = _create_with_rate_limit_retry(client, kwargs, settings)
    except LLMError:
        raise  # already has a friendly message
    except APITimeoutError as exc:
        logger.warning("LLM request timed out after %ss", settings.llm_timeout)
        raise LLMError(
            "The LLM provider took too long to respond. Please try this file again."
        ) from exc
    except APIConnectionError as exc:
        logger.warning("Could not connect to the LLM provider (APIConnectionError)")
        raise LLMError(
            "Could not connect to the LLM provider (network issue or the service is "
            "busy). Please try this file again."
        ) from exc
    except APIError as exc:
        # Only the status / type is logged; never the request or credentials.
        status = getattr(exc, "status_code", None)
        logger.warning("LLM request failed (status=%s, %s)", status, type(exc).__name__)
        raise LLMError(_friendly_error(exc, status)) from exc
    except Exception as exc:  # network errors, timeouts
        logger.warning("LLM request failed: %s", type(exc).__name__)
        raise LLMError(f"Could not reach the LLM provider ({type(exc).__name__}).") from exc

    elapsed = time.perf_counter() - started
    logger.info("LLM call to model=%s took %.1fs", settings.model_name, elapsed)

    content = response.choices[0].message.content if response.choices else None
    return content or ""


def _friendly_error(exc: APIError, status: int | None) -> str:
    if status == 401 or status == 403:
        return "LLM provider rejected the API key (check API_KEY in .env)."
    if status == 404:
        return "Model or endpoint not found (check BASE_URL and MODEL_NAME in .env)."
    if status == 429:
        return ("LLM rate limit reached (free tiers allow only a few requests per "
                "minute). Wait a minute and retry this file.")
    if status in (500, 502, 503, 504):
        return "The LLM provider is temporarily unavailable or overloaded. Please retry shortly."
    if status == 400 and "image" in str(exc).lower():
        return "The model rejected the image. Make sure MODEL_NAME supports vision."
    return f"LLM provider error (status {status})."

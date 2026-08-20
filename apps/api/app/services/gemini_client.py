"""Shared Gemini client helpers with retries and model fallbacks."""

from __future__ import annotations

import logging
import time
from typing import Any

from fastapi import HTTPException, status
from google import genai

from app.config import settings

logger = logging.getLogger(__name__)

# Prefer configured model, then current Flash variants.
_DEFAULT_FALLBACKS = (
    "gemini-3.6-flash",
    "gemini-flash-latest",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-3.5-flash-lite",
)


def gemini_model_candidates(primary: str | None = None) -> list[str]:
    preferred = (primary or settings.gemini_model or "gemini-3.6-flash").strip()
    out: list[str] = []
    for name in (preferred, *_DEFAULT_FALLBACKS):
        if name and name not in out:
            out.append(name)
    return out


def require_gemini_client(*, timeout_ms: int = 60_000) -> genai.Client:
    if not settings.google_api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="GOOGLE_API_KEY is not configured. Add your Google AI Studio key to .env.",
        )
    return genai.Client(
        api_key=settings.google_api_key,
        http_options={"timeout": timeout_ms},
    )


def _is_retryable(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return any(
        token in msg
        for token in (
            "503",
            "unavailable",
            "high demand",
            "429",
            "resource_exhausted",
            "deadline exceeded",
            "timed out",
            "timeout",
            "temporarily",
        )
    )


def _is_model_unavailable(exc: BaseException) -> bool:
    """Retired / unknown model — skip to the next candidate immediately."""
    msg = str(exc).lower()
    return any(
        token in msg
        for token in (
            "404",
            "not_found",
            "no longer available",
            "is not found",
            "not supported",
        )
    )


def generate_content_with_retries(
    *,
    contents: str,
    config: dict[str, Any] | None = None,
    model: str | None = None,
    max_attempts_per_model: int = 3,
    client: genai.Client | None = None,
) -> Any:
    """
    Call Gemini with exponential backoff and model fallbacks.

    Retries transient 503 / high-demand / rate-limit errors across several Flash models.
    Skips immediately to the next model when a model ID is retired (404).
    """
    gemini = client or require_gemini_client()
    last_exc: Exception | None = None
    candidates = gemini_model_candidates(model)

    for model_name in candidates:
        for attempt in range(max_attempts_per_model):
            try:
                response = gemini.models.generate_content(
                    model=model_name,
                    contents=contents,
                    config=config or {},
                )
                if attempt > 0 or model_name != candidates[0]:
                    logger.info(
                        "gemini_ok model=%s attempt=%s",
                        model_name,
                        attempt + 1,
                    )
                return response
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                if _is_model_unavailable(exc):
                    logger.warning(
                        "gemini_model_unavailable model=%s err=%s — trying next",
                        model_name,
                        exc,
                    )
                    break
                retryable = _is_retryable(exc)
                logger.warning(
                    "gemini_failed model=%s attempt=%s retryable=%s err=%s",
                    model_name,
                    attempt + 1,
                    retryable,
                    exc,
                )
                if not retryable:
                    raise
                if attempt < max_attempts_per_model - 1:
                    time.sleep(1.2 * (2**attempt))
                    continue
                # Exhausted retries for this model — try next fallback.
                break

    raise HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail=(
            "Gemini is temporarily overloaded. Please try again in a moment. "
            f"Last error: {last_exc}"
        ),
    ) from last_exc

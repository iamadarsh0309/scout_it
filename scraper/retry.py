from __future__ import annotations

import logging

from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

logger = logging.getLogger(__name__)

BLOCK_PAGE_MARKERS = (
    "checking your browser",
    "attention required",
    "cf-error-details",
    "unusual traffic",
    "access denied",
)


class BlockedError(Exception):
    """Raised when a response looks like a bot-detection challenge/block page."""


class FetchError(Exception):
    """Raised for non-2xx responses or navigation failures worth retrying."""


def looks_blocked(html: str | None) -> bool:
    if not html:
        return False
    lowered = html.lower()
    return any(marker in lowered for marker in BLOCK_PAGE_MARKERS)


def with_retry(func):
    """Exponential backoff 2s -> 8s -> 30s, max 3 attempts, on transient fetch/block errors.
    Callers are responsible for logging exhausted retries to FailedFetch — this decorator
    only controls retry timing, not failure bookkeeping."""
    return retry(
        retry=retry_if_exception_type((BlockedError, FetchError)),
        wait=wait_exponential(multiplier=2, min=2, max=30),
        stop=stop_after_attempt(3),
        reraise=True,
    )(func)

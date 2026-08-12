"""Retry policy for transient upstream failures.

Shared by the vendor adapters and the judge — both call rate-limited HTTP APIs
thousands of times per run and fail the same ways.

Vendor SDKs raise their own exception types, so classification avoids importing
any of them: it reads the HTTP status off the exception when there is one, and
only falls back to matching phrases when there is not.

Never match bare status numbers against the message. Error text embeds token
counts, request ids and byte offsets, so "...you requested 201429 tokens" — a
permanent oversize-request error — reads as a 429 and would burn the whole
backoff budget. Judge prompts carry a full multi-thousand-character answer, so
that error is routine, not hypothetical.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable

# Most vendor rate limits are minute-windowed, so the budget rides out at least
# one full window. Delays (pre-jitter): 1, 2, 4, 8, 16, 32, 45, 45 -> ~153s;
# with full jitter the floor is ~75s. Past that the limit is sustained, and
# failing the item beats blocking a 100-item run on one upstream throttle.
MAX_ATTEMPTS = 8
_BASE_SEC = 1.0
_CAP_SEC = 45.0

_TRANSIENT_STATUS = frozenset({408, 429, 500, 502, 503, 504, 529})

#: Only consulted when the exception carries no status code of its own.
_TRANSIENT_PHRASES = (
    "rate limit",
    "too many requests",
    "overloaded",
    "service unavailable",
    "internal server error",
    "bad gateway",
    "gateway timeout",
)


def _status_code(exc: BaseException) -> int | None:
    """The HTTP status an SDK attached to the error, if any.

    Stainless-generated clients (anthropic, nimble, parallel) put it on
    `status_code`; httpx-based ones put it on `response.status_code`.
    """
    code = getattr(exc, "status_code", None)
    if code is None:
        code = getattr(getattr(exc, "response", None), "status_code", None)
    return code if isinstance(code, int) else None


def is_account_cap(exc: BaseException) -> bool:
    """An account usage cap, which no amount of backoff will clear.

    Requires both substrings so a research question that merely discusses API
    limits cannot trip it.
    """
    msg = str(exc).lower()
    return "exceeds this api key" in msg and "set usage limit" in msg


def is_transient(exc: BaseException) -> bool:
    """A throttle or upstream hiccup that is worth retrying."""
    if is_account_cap(exc):
        return False
    status = _status_code(exc)
    if status is not None:
        # Authoritative — do not second-guess it with the message text.
        return status in _TRANSIENT_STATUS
    msg = str(exc).lower()
    return any(phrase in msg for phrase in _TRANSIENT_PHRASES)


async def with_retry[T](fn: Callable[[], Awaitable[T]], *, label: str) -> T:
    """Retry an async call on transient failures with full-jitter backoff.

    `fn` is invoked fresh each attempt, so SDKs that build a request internally
    work correctly. Account caps and permanent errors are re-raised at once.
    """
    for attempt in range(MAX_ATTEMPTS):
        try:
            return await fn()
        except Exception as exc:
            if is_account_cap(exc):
                print(f"[{label}] account usage cap hit — not retrying: {exc}", flush=True)
                raise
            if not is_transient(exc) or attempt == MAX_ATTEMPTS - 1:
                raise
            delay = min(_CAP_SEC, _BASE_SEC * (2**attempt)) * (0.5 + random.random() * 0.5)
            print(
                f"[{label}] transient failure (attempt {attempt + 1}/{MAX_ATTEMPTS}); sleeping {delay:.1f}s", flush=True
            )
            await asyncio.sleep(delay)
    raise RuntimeError("unreachable: with_retry must return or raise")

"""Async retry helper shared by samplers, backed by :mod:`tenacity`.

This module previously shipped a hand-rolled exponential-backoff loop. It
was replaced with :class:`tenacity.AsyncRetrying` so the project has one
retry engine across :mod:`synthesis.llm` (which already used tenacity
decorators) and every sampler that uses :func:`retry_async`. The public
surface — :class:`RetryConfig`, :class:`TransientHTTPError`,
:class:`CitationParseError`, :data:`RETRYABLE_STATUSES`,
:func:`retry_async` — is preserved verbatim so existing call sites
continue to work without touching every sampler.

Numerics preserved from the legacy implementation:

* ``max_attempts``  -> :func:`tenacity.stop_after_attempt`
* ``initial_wait`` * 2^(attempt-1), capped at ``max_wait``
  -> :func:`tenacity.wait_exponential(multiplier=initial_wait, max=max_wait, exp_base=2)`
* Retries only on :class:`TransientHTTPError`
  -> :func:`tenacity.retry_if_exception_type(TransientHTTPError)`
* Exhaustion re-raises the last :class:`TransientHTTPError` (not a
  ``RetryError``) -> ``reraise=True``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Final

from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    stop_after_delay,
    wait_exponential,
)

RETRYABLE_STATUSES = {408, 409, 425, 429, 500, 502, 503, 504}

# Hard ceiling on total time the retry loop may spend across all attempts +
# backoff waits. The per-attempt aiohttp timeout (``BaseHTTPPostSampler``
# default 60 s) is unchanged, but with ``max_attempts=5`` and a 60 s
# per-attempt timeout the worst-case retry budget is 5 minutes -- way past
# any sensible eval row deadline. ``stop_after_delay`` short-circuits the
# loop once the wall-clock budget is exhausted regardless of how many
# attempts remain, so a stuck-but-not-timing-out upstream can't blow past
# the row budget. ``stop_after_attempt | stop_after_delay`` fires on the
# first condition that trips, matching ``stop_any`` semantics.
DEFAULT_TOTAL_RETRY_BUDGET_S: Final[float] = 60.0


@dataclass(frozen=True)
class RetryConfig:
    max_attempts: int = 3
    initial_wait: float = 0.5
    max_wait: float = 5.0


class TransientHTTPError(RuntimeError):
    def __init__(self, status: int, message: str = "", body: str | None = None):
        super().__init__(message or f"transient HTTP {status}")
        self.status = status
        self.body = body


class CitationParseError(RuntimeError):
    def __init__(self, *, provider: str, tag_body: str, original_exc: Exception):
        super().__init__(f"{provider} citation parse error: {original_exc}")
        self.provider = provider
        self.tag_body = tag_body
        self.original_exc = original_exc


async def retry_async[T](func: Callable[[], Awaitable[T]], config: RetryConfig | None = None) -> T:
    """Retry ``func`` on :class:`TransientHTTPError` via tenacity.

    Matches the legacy loop's wait schedule:
    ``initial_wait`` * 2^(attempt-1), capped at ``max_wait``, up to
    ``max_attempts`` attempts total. After exhaustion the last
    :class:`TransientHTTPError` is re-raised unchanged.

    Also enforces :data:`DEFAULT_TOTAL_RETRY_BUDGET_S` (60 s) as a
    wall-clock ceiling across all attempts + waits, so a hung-but-not-
    timing-out upstream can't burn the row budget waiting for retry slots
    that will never converge.
    """
    cfg = config or RetryConfig()
    async for attempt in AsyncRetrying(
        stop=(stop_after_attempt(cfg.max_attempts) | stop_after_delay(DEFAULT_TOTAL_RETRY_BUDGET_S)),
        wait=wait_exponential(multiplier=cfg.initial_wait, max=cfg.max_wait, exp_base=2),
        retry=retry_if_exception_type(TransientHTTPError),
        reraise=True,
    ):
        with attempt:
            return await func()
    # Unreachable: ``reraise=True`` re-raises the captured exception once
    # ``stop_after_attempt`` fires inside the ``with attempt:`` block.
    raise AssertionError("retry_async: tenacity loop exited without raising or returning")

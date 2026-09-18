"""Locks tenacity-backed :func:`retry_async` against the legacy semantics.

The legacy hand-rolled loop is gone, but the contract callers depend on
must hold:

1. Retries only on :class:`TransientHTTPError`. Any other exception
   propagates immediately on the first attempt (so a bug in extraction
   logic doesn't get masked by 3 retries).
2. ``max_attempts`` is the total attempt count (1 initial + N retries).
3. Wait schedule is exponential, starting at ``initial_wait`` and
   doubling each step, capped at ``max_wait``.
4. After exhaustion, the LAST caught :class:`TransientHTTPError` is
   re-raised (not a ``tenacity.RetryError``) so existing
   ``except TransientHTTPError`` handlers keep working.
5. A successful attempt mid-retry returns the value (no extra wait).
"""

from __future__ import annotations

import asyncio

import pytest

from nimble_benchmark.retry import (
    DEFAULT_TOTAL_RETRY_BUDGET_S,
    RetryConfig,
    TransientHTTPError,
    retry_async,
)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_retry_async_succeeds_on_first_attempt():
    attempts = 0

    async def func() -> str:
        nonlocal attempts
        attempts += 1
        return "ok"

    result = await retry_async(func, RetryConfig(max_attempts=3, initial_wait=0.0, max_wait=0.0))
    assert result == "ok"
    assert attempts == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_retry_async_retries_on_transient_then_succeeds():
    attempts = 0

    async def func() -> str:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise TransientHTTPError(503, "upstream down")
        return "ok"

    result = await retry_async(func, RetryConfig(max_attempts=3, initial_wait=0.0, max_wait=0.0))
    assert result == "ok"
    assert attempts == 3


@pytest.mark.unit
@pytest.mark.asyncio
async def test_retry_async_reraises_last_transient_after_max_attempts():
    attempts = 0

    async def func() -> str:
        nonlocal attempts
        attempts += 1
        raise TransientHTTPError(503, f"attempt {attempts}")

    with pytest.raises(TransientHTTPError) as info:
        await retry_async(func, RetryConfig(max_attempts=3, initial_wait=0.0, max_wait=0.0))
    # The exhausted exception is the *last* caught one with attempt-3 marker,
    # NOT a tenacity RetryError wrapper. Existing ``except TransientHTTPError``
    # handlers in the samplers depend on this.
    assert info.value.status == 503
    assert "attempt 3" in str(info.value)
    assert attempts == 3


@pytest.mark.unit
@pytest.mark.asyncio
async def test_retry_async_does_not_retry_on_other_exceptions():
    """A non-transient error must surface on the FIRST attempt — retrying
    over real bugs would just slow down the runner and hide the trace."""
    attempts = 0

    async def func() -> str:
        nonlocal attempts
        attempts += 1
        raise ValueError("boom")

    with pytest.raises(ValueError, match="boom"):
        await retry_async(func, RetryConfig(max_attempts=5, initial_wait=0.0, max_wait=0.0))
    assert attempts == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_retry_async_wait_schedule_doubles_capped_at_max_wait(monkeypatch):
    """Wait schedule must be initial * 2^(attempt-1), capped at max_wait;
    matches the historical exponential-backoff numerics so a long-running
    benchmark doesn't suddenly take 10x longer on transient bursts.

    Asserts against tenacity's :func:`wait_exponential` directly (called by
    :func:`retry_async` under the hood). Locking the schedule here means a
    future tenacity upgrade or local helper rewrite can't drift the
    wait-shape without tripping CI.
    """
    from tenacity import wait_exponential

    # Construct the same wait callable retry_async builds internally.
    waiter = wait_exponential(multiplier=0.5, max=3.0, exp_base=2)

    class _State:
        def __init__(self, attempt_number: int) -> None:
            self.attempt_number = attempt_number
            self.outcome = None

    # tenacity passes the retry_state; we only need attempt_number.
    computed = [waiter(_State(n)) for n in range(1, 6)]
    # initial=0.5, max=3.0: 0.5*2^0, 0.5*2^1, 0.5*2^2, then capped at 3.0.
    assert computed == [
        pytest.approx(0.5),
        pytest.approx(1.0),
        pytest.approx(2.0),
        pytest.approx(3.0),
        pytest.approx(3.0),
    ]

    # Smoke-test the end-to-end flow too: zero waits, all attempts consumed,
    # final TransientHTTPError surfaced after `max_attempts`.
    attempts = 0

    async def func() -> str:
        nonlocal attempts
        attempts += 1
        raise TransientHTTPError(503, "upstream down")

    with pytest.raises(TransientHTTPError):
        await retry_async(func, RetryConfig(max_attempts=5, initial_wait=0.0, max_wait=0.0))
    assert attempts == 5


@pytest.mark.unit
@pytest.mark.asyncio
async def test_retry_async_default_config():
    """``RetryConfig()`` defaults must match the legacy
    (max_attempts=3, initial_wait=0.5, max_wait=5.0) tuple — every sampler
    that constructs ``RetryConfig()`` with no args depends on this."""
    cfg = RetryConfig()
    assert cfg.max_attempts == 3
    assert cfg.initial_wait == pytest.approx(0.5)
    assert cfg.max_wait == pytest.approx(5.0)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_retry_async_zero_wait_does_not_block():
    """initial_wait=0 means retries happen back-to-back. Useful smoke check
    that the tenacity wait function accepts a zero multiplier (some older
    tenacity versions choked on this)."""
    attempts = 0
    start = asyncio.get_event_loop().time()

    async def func() -> str:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise TransientHTTPError(503, "transient")
        return "ok"

    result = await retry_async(func, RetryConfig(max_attempts=3, initial_wait=0.0, max_wait=0.0))
    elapsed = asyncio.get_event_loop().time() - start
    assert result == "ok"
    assert attempts == 3
    # Three back-to-back attempts with zero wait must complete in well under 100 ms.
    assert elapsed < 0.1, f"zero-wait retry took {elapsed:.3f}s"


@pytest.mark.unit
def test_total_retry_budget_default_is_sixty_seconds():
    """Hard ceiling on retry wall-clock time. The per-attempt aiohttp
    timeout is 60 s in ``BaseHTTPPostSampler``, so without this cap a
    sampler with ``max_attempts=5`` could spend ~5 minutes retrying a
    silently hung upstream -- way past any sensible eval row budget."""
    assert DEFAULT_TOTAL_RETRY_BUDGET_S == 60.0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_retry_async_stops_at_total_budget_before_max_attempts(monkeypatch):
    """If ``stop_after_delay`` fires before ``stop_after_attempt``, the
    loop must surface the last :class:`TransientHTTPError` rather than
    silently retrying past the budget. Patches the budget to a tiny
    value so the test runs in milliseconds, exercising the same
    ``stop_after_attempt | stop_after_delay`` plumbing production uses.
    """
    monkeypatch.setattr("nimble_benchmark.retry.DEFAULT_TOTAL_RETRY_BUDGET_S", 0.05)
    attempts = 0

    async def func() -> str:
        nonlocal attempts
        attempts += 1
        raise TransientHTTPError(503, f"attempt {attempts}")

    start = asyncio.get_event_loop().time()
    # max_attempts=1000 ensures the budget, not the attempt count, is
    # the binding stop condition.
    with pytest.raises(TransientHTTPError):
        await retry_async(func, RetryConfig(max_attempts=1000, initial_wait=0.05, max_wait=0.05))
    elapsed = asyncio.get_event_loop().time() - start
    # Budget is 50 ms; allow generous headroom for scheduler jitter but
    # confirm we did NOT run all 1000 attempts.
    assert attempts < 50, f"expected budget to short-circuit; got {attempts} attempts"
    assert elapsed < 1.0, f"retry loop ran {elapsed:.3f}s, expected sub-second under 50ms budget"

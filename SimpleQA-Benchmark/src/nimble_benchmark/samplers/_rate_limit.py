"""Process-wide token-bucket rate limiter shared by third-party samplers.

Several upstream search APIs cap QPS per API key:

* Exa: 10 QPS default on ``/search``
  (https://exa.ai/docs/reference/rate-limits).
* Parallel: 600 requests/minute default on ``POST /v1/search`` == 10 RPS
  (https://docs.parallel.ai/getting-started/rate-limits).
* Nimble: rate-limited per *product*; the search_<depth> products allow
  10 RPS (observed live). Throttled on the same terms as every other lane --
  an n=1000 run without self-throttling lost 4.4% of nimble_search rows to
  429s that survived retries. Override: ``NIMBLE_RATE_PER_SECOND``.
* Brave: 1 RPS on the free tier, with a 2,000 request/month allowance
  (https://api-dashboard.search.brave.com/app/subscriptions/subscribe). This is
  the tightest ceiling of any lane -- a 500-row run takes ~8.5 minutes for the
  Brave lane alone. Override with ``BRAVE_RATE_PER_SECOND`` on a paid plan.

Without self-throttling, the eval runner's parallel fan-out (≈20 concurrent
tasks per sampler at the default ``--max-concurrent-tasks``) instantly bursts
past those ceilings: ~20 RPS crushes a 1.67 RPS key 12x
over, and the 2026-05-27 n=500 SimpleQA run saw a ~77 % ``failed_after_retries``
rate on an un-throttled lane -- ``retry_async`` cannot recover from a
sustained 429 storm because each retry just consumes another token the
server already refused.

This module keys limiters on ``(scope, api_key, rate_per_second)``. ``scope`` is
a short string per provider (``"exa"``, ``"parallel"``) so two samplers
configured against the *same* key + scope (e.g. ``exa_search_auto`` and
``exa_search_fast`` sharing a key) share one token bucket -- matching the
provider's API-key-scoped quota model. Different keys (or different scopes)
get independent buckets.

The limiter is acquired *before* each HTTP attempt -- including retries --
so a backoff-driven retry storm still respects the global rate ceiling.
"""

from __future__ import annotations

from aiolimiter import AsyncLimiter

_LIMITERS: dict[tuple[str, str, float], AsyncLimiter] = {}


def get_limiter(scope: str, api_key: str, rate_per_second: float) -> AsyncLimiter:
    """Return the process-wide :class:`AsyncLimiter` for ``(scope, api_key, rate)``.

    Sharing by ``(scope, api_key, rate_per_second)`` means two samplers
    configured against the same key under the same scope (e.g. both
    ``exa_search_auto`` and ``exa_search_fast`` using one Exa key) hit the
    same token bucket -- which is what the provider's per-key quota
    actually enforces. A test or a higher-tier caller that needs a
    different rate constructs a separate limiter under a different cache
    entry (the cache key includes the rate to keep tests from accidentally
    reusing a stale instance).
    """
    cache_key = (scope, api_key, rate_per_second)
    limiter = _LIMITERS.get(cache_key)
    if limiter is None:
        limiter = AsyncLimiter(max_rate=rate_per_second, time_period=1.0)
        _LIMITERS[cache_key] = limiter
    return limiter


def reset_limiters() -> None:
    """Clear the cache (test-only helper). Production code does not call this."""
    _LIMITERS.clear()

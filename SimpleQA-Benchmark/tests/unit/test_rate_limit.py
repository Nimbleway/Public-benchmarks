"""Tests for the shared per-(scope, key, rate) rate-limiter cache.

The generic ``_rate_limit`` module backs Exa, Parallel and Brave (and any
future third-party sampler whose upstream has a per-key QPS cap). The cache
contract that matters for production correctness:

* Two samplers under the *same* scope + key share one bucket so a
  multi-sampler run respects the provider's aggregate per-key quota.
* A *different* scope or a *different* key gets an independent bucket
  so Parallel-vs-Exa or free-vs-paid keys don't throttle each other.
* The first acquire is immediate so the autouse reset fixture above
  doesn't add a 1 s warmup to every third-party sampler test on fast
  machines.
"""

from __future__ import annotations

import asyncio

import pytest

from nimble_benchmark.samplers._rate_limit import get_limiter, reset_limiters


@pytest.fixture
def _clean_cache():
    reset_limiters()
    yield
    reset_limiters()


@pytest.mark.unit
def test_same_scope_and_key_returns_shared_limiter(_clean_cache):
    """exa_search_auto + exa_search_fast using one Exa key MUST get the
    same bucket -- the QPS quota is API-key-scoped, and a per-sampler bucket
    would silently allow 2x the documented rate."""
    a = get_limiter("exa", "shared-key", 9.0)
    b = get_limiter("exa", "shared-key", 9.0)
    assert a is b


@pytest.mark.unit
def test_different_scope_isolates_limiters_for_same_key(_clean_cache):
    """A user who happens to set the same value for the PARALLEL and EXA keys
    must NOT see them share a bucket -- they're different upstream quotas."""
    parallel = get_limiter("parallel", "same-string", 9.0)
    exa = get_limiter("exa", "same-string", 9.0)
    assert parallel is not exa


@pytest.mark.unit
def test_different_key_isolates_limiters(_clean_cache):
    """Free-plan key + paid-plan key in the same run must NOT share a
    bucket; the higher-budget key shouldn't be throttled to the lower."""
    free = get_limiter("brave", "free-key", 1.0)
    paid = get_limiter("brave", "paid-key", 15.0)
    assert free is not paid


@pytest.mark.unit
def test_different_rate_caches_independently_for_same_scope_and_key(_clean_cache):
    """A test that overrides the rate on the same scope+key must not
    collide with the production-rate cached bucket; isolates test
    overrides from production samplers."""
    default = get_limiter("exa", "key", 1.5)
    fast = get_limiter("exa", "key", 100.0)
    assert default is not fast


@pytest.mark.unit
def test_reset_clears_cache(_clean_cache):
    """reset_limiters MUST drop cached buckets so tests can start fresh."""
    first = get_limiter("exa", "key", 1.5)
    reset_limiters()
    second = get_limiter("exa", "key", 1.5)
    assert first is not second


@pytest.mark.unit
@pytest.mark.asyncio
async def test_first_acquire_is_immediate(_clean_cache):
    """AsyncLimiter starts with a full bucket -- the very first acquire
    must not wait. Otherwise the autouse conftest reset would add a
    ~0.7s warmup to every rate-limited sampler test on fast machines."""
    limiter = get_limiter("exa", "key", 1.5)
    loop = asyncio.get_running_loop()
    t0 = loop.time()
    async with limiter:
        pass
    elapsed = loop.time() - t0
    assert elapsed < 0.05, f"first acquire took {elapsed:.3f}s (expected ~0)"

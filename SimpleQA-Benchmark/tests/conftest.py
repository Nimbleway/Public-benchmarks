import pytest

pytest_plugins = ["pytest_asyncio"]


@pytest.fixture(autouse=True)
def _reset_shared_limiter_cache():
    """Reset the generic ``_rate_limit`` cache used by the Exa/Parallel samplers.

    Without this, a test that builds e.g. ``ExaSearchSampler`` followed
    by a second test that builds another ``ExaSearchSampler`` against
    the same key would share the same module-level token bucket; the
    second test's first acquire could observe a non-zero wait depending
    on the schedule of the first test, causing flake on fast machines.
    """
    from nimble_benchmark.samplers._rate_limit import reset_limiters

    reset_limiters()
    yield
    reset_limiters()

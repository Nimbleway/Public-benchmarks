import pytest
from aioresponses import aioresponses
from yarl import URL

from nimble_benchmark.config import Settings
from nimble_benchmark.preflight import preflight_or_die


@pytest.mark.unit
@pytest.mark.asyncio
async def test_preflight_probes_healthcheck_and_search():
    with aioresponses() as mocked:
        mocked.get("http://localhost:8002/healthcheck", status=200, payload={"status": "ok"})
        mocked.post("http://localhost:8002/search", status=200, payload={"results": []})

        await preflight_or_die(base_url="http://localhost:8002", api_key="k", sampler_names=["nimble_search"])


@pytest.mark.unit
@pytest.mark.asyncio
async def test_preflight_allows_gateway_without_healthcheck():
    with aioresponses() as mocked:
        mocked.get("https://sdk.nimbleway.com/v1/healthcheck", status=404, payload={"detail": "not found"})
        mocked.post("https://sdk.nimbleway.com/v1/search", status=200, payload={"results": []})

        await preflight_or_die(base_url="https://sdk.nimbleway.com/v1", api_key="k", sampler_names=["nimble_search"])


@pytest.mark.unit
@pytest.mark.asyncio
async def test_preflight_uses_bearer_auth_for_gateway():
    with aioresponses() as mocked:
        mocked.get("https://sdk.nimbleway.com/v1/healthcheck", status=404, payload={"detail": "not found"})
        mocked.post("https://sdk.nimbleway.com/v1/search", status=200, payload={"results": []})

        await preflight_or_die(
            base_url="https://sdk.nimbleway.com/v1", api_key="token", sampler_names=["nimble_search"]
        )

        request = mocked.requests[("POST", URL("https://sdk.nimbleway.com/v1/search"))][0]
        assert request.kwargs["headers"]["Authorization"] == "Bearer token"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_preflight_rejects_unexpected_search_4xx():
    with aioresponses() as mocked:
        mocked.get("https://sdk.nimbleway.com/v1/healthcheck", status=404, payload={"detail": "not found"})
        mocked.post("https://sdk.nimbleway.com/v1/search", status=422, payload={"detail": "bad query"})

        with pytest.raises(RuntimeError, match="nimble_search /search preflight failed with status 422"):
            await preflight_or_die(
                base_url="https://sdk.nimbleway.com/v1",
                api_key="k",
                sampler_names=["nimble_search"],
            )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_preflight_search_auth_failure_raises():
    with aioresponses() as mocked:
        mocked.get("http://localhost:8002/healthcheck", status=200, payload={"status": "ok"})
        mocked.post("http://localhost:8002/search", status=403, payload={"detail": "forbidden"})

        with pytest.raises(RuntimeError, match="nimble_search /search auth failed"):
            await preflight_or_die(base_url="http://localhost:8002", api_key="k", sampler_names=["nimble_search"])


@pytest.mark.unit
@pytest.mark.asyncio
async def test_preflight_dedups_lanes_sharing_one_prod_connection():
    """Nimble lanes share one base URL + key, so a roster naming several of them
    collapses to a single healthcheck + /search probe. The unregistered lane
    names are deliberate: preflight keys off the connection, not the roster."""
    settings = Settings(
        nimble_api_key="prod-key",
        nimble_base_url="https://prod.example.com",
        _env_file=None,
    )

    with aioresponses() as mocked:
        mocked.get("https://prod.example.com/healthcheck", status=200, payload={"status": "ok"})
        mocked.post("https://prod.example.com/search", status=200, payload={"results": []})

        await preflight_or_die(
            settings=settings,
            sampler_names=["nimble_search"],
        )

        assert len(mocked.requests[("POST", URL("https://prod.example.com/search"))]) == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_preflight_skips_network_for_third_party_apis():
    settings = Settings(
        nimble_api_key="legacy-key",
        exa_api_key="exa-key",
        brave_search_api_key="brave-key",
        _env_file=None,
    )

    assert await preflight_or_die(settings=settings, sampler_names=["exa_search_auto", "brave_search"]) == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_preflight_aggregates_missing_third_party_creds(monkeypatch):
    monkeypatch.delenv("EXA_API_KEY", raising=False)
    monkeypatch.delenv("BRAVE_SEARCH_API_KEY", raising=False)
    settings = Settings(nimble_api_key="legacy-key", _env_file=None)

    with pytest.raises(RuntimeError) as exc_info:
        await preflight_or_die(settings=settings, sampler_names=["exa_search_auto", "brave_search"])

    message = str(exc_info.value)
    assert "EXA_API_KEY" in message
    assert "BRAVE_SEARCH_API_KEY" in message


@pytest.mark.unit
@pytest.mark.asyncio
async def test_preflight_returns_empty_skip_list_on_clean_preflight():
    """The function returns ``[]`` -- a list, not None -- so callers can
    always iterate. Pin the contract so a future refactor doesn't regress
    to ``None``."""
    with aioresponses() as mocked:
        mocked.get("http://localhost:8002/healthcheck", status=200, payload={"status": "ok"})
        mocked.post("http://localhost:8002/search", status=200, payload={"results": []})

        skipped = await preflight_or_die(base_url="http://localhost:8002", api_key="k", sampler_names=["nimble_search"])

    assert skipped == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_preflight_prod_5xx_on_search_raises():
    """Every remaining lane is prod and explicitly opted into
    (``LENIENT_LANES`` is empty), so a 5xx aborts rather than silently
    dropping the baseline row from the published leaderboard."""
    settings = Settings(
        nimble_api_key="prod-key",
        nimble_base_url="https://prod.example.com",
        _env_file=None,
    )

    with aioresponses() as mocked:
        mocked.get("https://prod.example.com/healthcheck", status=200, payload={"status": "ok"})
        mocked.post("https://prod.example.com/search", status=503, payload={"detail": "prod down"})

        with pytest.raises(RuntimeError, match="nimble_search /search preflight failed with status 503"):
            await preflight_or_die(settings=settings, sampler_names=["nimble_search"])


@pytest.mark.unit
@pytest.mark.asyncio
async def test_preflight_prod_5xx_on_healthcheck_raises():
    settings = Settings(
        nimble_api_key="prod-key",
        nimble_base_url="https://prod.example.com",
        _env_file=None,
    )

    with aioresponses() as mocked:
        mocked.get("https://prod.example.com/healthcheck", status=500, payload={"detail": "down"})

        with pytest.raises(RuntimeError, match="nimble_search healthcheck failed with status 500"):
            await preflight_or_die(settings=settings, sampler_names=["nimble_search"])

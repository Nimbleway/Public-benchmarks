import asyncio
import json
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any, get_args

import pytest
from aioresponses import aioresponses
from httpx import Request, Response
from yarl import URL

from nimble_benchmark import retry
from nimble_benchmark.config import Settings
from nimble_benchmark.models import ProviderResponse, ProviderUsage, RequestStatus, RetrievalChunk
from nimble_benchmark.samplers import build_samplers
from nimble_benchmark.samplers.base import BaseSampler
from nimble_benchmark.samplers.firecrawl_search import FirecrawlSearchSampler
from nimble_benchmark.samplers.nimble_search import NimbleSearchSampler


@pytest.fixture
def search_payload():
    return json.loads(Path("tests/fixtures/nimble_search.json").read_text())


@pytest.mark.unit
def test_request_status_literal_includes_citation_parse_error():
    assert "citation_parse_error" in get_args(RequestStatus)


@pytest.mark.unit
def test_provider_usage_dataclass_defaults():
    assert ProviderUsage() == ProviderUsage(input_tokens=None, output_tokens=None)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_citation_parse_error_propagates_through_sample():
    class CitationFailingSampler(BaseSampler):
        async def get_search_results(self, query):
            raise retry.CitationParseError(provider=self.name, tag_body="{bad", original_exc=ValueError("bad json"))

        def extract_chunks(self, raw):
            return []

    response = await CitationFailingSampler(name="fake_answer").sample("q")

    assert response.status == "citation_parse_error"
    assert response.provider == "fake_answer"
    assert response.chunks == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_citation_parse_error_during_normalization_propagates_through_sample():
    class CitationFailingSampler(BaseSampler):
        async def get_search_results(self, query):
            return {"answer": "bad citation"}

        def extract_chunks(self, raw):
            raise retry.CitationParseError(provider=self.name, tag_body="{bad", original_exc=ValueError("bad json"))

    response = await CitationFailingSampler(name="fake_answer").sample("q")

    assert response.status == "citation_parse_error"
    assert response.provider == "fake_answer"
    assert response.chunks == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_sample_keeps_response_metadata_per_concurrent_task():
    slow_can_return = asyncio.Event()

    class HeaderSampler(BaseSampler):
        async def get_search_results(self, query):
            if query == "slow":
                self._last_headers = {"Server-Timing": "total;dur=10"}
                await slow_can_return.wait()
            else:
                self._last_headers = {"Server-Timing": "total;dur=20"}
                slow_can_return.set()
            return {"results": []}

        def extract_chunks(self, raw):
            return []

    sampler = HeaderSampler(name="header_sampler")

    slow_response, fast_response = await asyncio.gather(sampler.sample("slow"), sampler.sample("fast"))

    assert slow_response.latency.internal_response_time_ms == 10
    assert fast_response.latency.internal_response_time_ms == 20


@pytest.mark.unit
def test_nimble_search_uses_bearer_auth_header():
    sampler = NimbleSearchSampler(name="nimble_search", base_url="http://localhost:8002", api_key="token")

    assert sampler._headers()["Authorization"] == "Bearer token"


@pytest.mark.unit
def test_nimble_search_normalizes_chunks(search_payload):
    sampler = NimbleSearchSampler(name="nimble_search", base_url="http://localhost:8002", api_key="k")
    chunks = sampler.extract_chunks(search_payload)

    assert chunks[0].url == "https://en.wikipedia.org/wiki/Paris"
    assert chunks[0].position == 0
    assert sampler.response_kind() == "search_results"


@pytest.mark.unit
def test_nimble_search_full_content_reaches_description_not_just_extra_snippets():
    """Regression: eval-side synthesis (runner.synthesize_answer) formats
    only title/url/description -- it never reads extra_snippets. A
    full_content=True response's scraped page text used to be parked
    exclusively in extra_snippets, making it visible to the UMBRELA judge
    but invisible to the very answer that judge's score was meant to
    explain. It must show up in `description` too."""
    sampler = NimbleSearchSampler(name="nimble_search", base_url="http://localhost:8002", api_key="k")
    raw = {
        "results": [
            {
                "url": "https://example.com/page",
                "title": "Example",
                "description": "Short blurb.",
                "content": "The full scraped page body, much longer than the blurb.",
            }
        ]
    }

    chunks = sampler.extract_chunks(raw)

    assert "Short blurb." in chunks[0].description
    assert "The full scraped page body, much longer than the blurb." in chunks[0].description


@pytest.mark.unit
@pytest.mark.asyncio
async def test_nimble_search_keeps_v1_gateway_path(search_payload):
    sampler = NimbleSearchSampler(name="nimble_search", base_url="https://sdk.nimbleway.com/v1", api_key="token")
    with aioresponses() as mocked:
        mocked.post("https://sdk.nimbleway.com/v1/search", status=200, payload=search_payload)
        await sampler.sample("What is the capital of France?")

    request = mocked.requests[("POST", URL("https://sdk.nimbleway.com/v1/search"))][0]
    assert request.kwargs["headers"]["Authorization"] == "Bearer token"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_nimble_search_sample_returns_provider_response(search_payload):
    sampler = NimbleSearchSampler(name="nimble_search", base_url="http://localhost:8002", api_key="k")
    with aioresponses() as mocked:
        mocked.post(
            "http://localhost:8002/search",
            status=200,
            payload=search_payload,
            headers={"Server-Timing": "total;dur=25.5"},
        )
        response = await sampler.sample("What is the capital of France?")

    assert isinstance(response, ProviderResponse)
    assert response.response_kind == "search_results"
    assert [chunk.url for chunk in response.chunks][:1] == ["https://en.wikipedia.org/wiki/Paris"]
    assert response.latency.internal_response_time_ms == 25.5


# ----------------------------------------------------------------------
# provider_response_time_ms -- the published latency column.
#
# These pin the one property that makes it publishable: it measures the
# upstream round trip and nothing of ours. The bug they regress against timed
# the whole ``sample`` call, so the column carried the token-bucket wait: on a
# lane throttled to R req/s driven by W workers every row reported ~W/R
# seconds no matter how fast the upstream answered, which made a 1 req/s lane
# and a 9 req/s lane converge on the same number and rank identically.
# ----------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_provider_response_time_excludes_rate_limiter_wait(search_payload):
    from nimble_benchmark.samplers._rate_limit import reset_limiters

    reset_limiters()
    sampler = NimbleSearchSampler(
        name="nimble_search",
        base_url="http://localhost:8002",
        api_key="limiter-wait-key",
        rate_per_second=2.0,
    )
    # Fill the bucket (capacity == max_rate == 2) so the sampled row has to
    # wait ~0.5 s for a token to drain before its request can go out.
    await sampler._limiter.acquire()
    await sampler._limiter.acquire()

    with aioresponses() as mocked:
        mocked.post("http://localhost:8002/search", status=200, payload=search_payload)
        response = await sampler.sample("What is the capital of France?")
    reset_limiters()

    assert response.status == "ok"
    # The wall clock saw the queue wait...
    assert response.latency.request_response_time_ms >= 400
    # ...and the published column did not.
    assert response.latency.provider_response_time_ms is not None
    assert response.latency.provider_response_time_ms < 200


@pytest.mark.unit
@pytest.mark.asyncio
async def test_provider_response_time_excludes_retry_backoff(search_payload):
    """A retried row reports the round trip of the attempt that answered,
    not that attempt plus the backoff sleep before it."""
    from nimble_benchmark.samplers._rate_limit import reset_limiters

    reset_limiters()
    sampler = NimbleSearchSampler(
        name="nimble_search",
        base_url="http://localhost:8002",
        api_key="backoff-key",
        retry_config=retry.RetryConfig(max_attempts=2, initial_wait=0.5, max_wait=0.5),
    )

    with aioresponses() as mocked:
        mocked.post("http://localhost:8002/search", status=503, payload={"detail": "try later"})
        mocked.post("http://localhost:8002/search", status=200, payload=search_payload)
        response = await sampler.sample("What is the capital of France?")
    reset_limiters()

    assert response.status == "ok"
    assert response.latency.request_response_time_ms >= 400
    assert response.latency.provider_response_time_ms is not None
    assert response.latency.provider_response_time_ms < 200


@pytest.mark.unit
@pytest.mark.asyncio
async def test_provider_response_time_is_none_when_no_request_went_out():
    """No network attempt means there is no upstream latency to report. ``None``
    keeps the row out of the percentiles instead of contributing a bogus
    fast/slow sample built from harness time."""

    class ExplodingSampler(BaseSampler):
        async def get_search_results(self, query):
            raise RuntimeError("blew up before the request")

        def extract_chunks(self, raw):
            return []

    response = await ExplodingSampler(name="fake_search").sample("q")

    assert response.status == "failed_after_retries"
    assert response.latency.provider_response_time_ms is None
    assert response.latency.request_response_time_ms >= 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_provider_response_time_records_a_failed_attempt(search_payload):
    """A row that reached the upstream and got a non-retryable rejection still
    has a real round trip -- that is what tells you whether it failed fast or
    timed out, so it is recorded rather than dropped."""
    sampler = NimbleSearchSampler(name="nimble_search", base_url="http://localhost:8002", api_key="k")
    with aioresponses() as mocked:
        mocked.post("http://localhost:8002/search", status=422, payload={"detail": "invalid"})
        response = await sampler.sample("bad")

    assert response.status == "validation_reject"
    assert response.latency.provider_response_time_ms is not None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_nimble_search_validation_reject_status():
    sampler = NimbleSearchSampler(name="nimble_search", base_url="http://localhost:8002", api_key="k")
    with aioresponses() as mocked:
        mocked.post("http://localhost:8002/search", status=422, payload={"detail": "invalid"})
        response = await sampler.sample("bad")

    assert response.status == "validation_reject"
    assert response.chunks == []


@pytest.mark.unit
def test_nimble_search_sampler_omits_debug_params_and_never_requests_an_answer() -> None:
    """``nimble_search*`` lanes emit no ``debug_params`` block -- the
    search-quality benchmark exercises only the documented /search surface --
    and pin ``include_answer`` off, because the graded answer is always
    synthesized eval-side and a server-side one would be paid-for output
    nothing reads."""
    sampler = NimbleSearchSampler(name="nimble_search", base_url="http://test", api_key="k")
    payload = sampler._build_payload("hello")
    assert "debug_params" not in payload
    assert payload["include_answer"] is False


@pytest.mark.unit
def test_build_samplers_nimble_lanes_emit_no_debug_params_by_default() -> None:
    """Every Nimble lane must build a payload with no ``debug_params`` block
    and no server-side answer request."""
    settings = Settings(nimble_api_key="prod-key", _env_file=None)

    samplers = build_samplers(settings=settings, sampler_names=["nimble_search"])

    assert [sampler.name for sampler in samplers] == ["nimble_search"]
    for sampler in samplers:
        payload = sampler._build_payload("q")
        assert "debug_params" not in payload, sampler.name
        assert payload["include_answer"] is False, sampler.name


class _MinimalHTTPPostSampler:
    """Tiny concrete subclass used by the base-class unit tests.

    Defined as a plain class shell here because the base class needs the
    instance's name + base_url + api_key + endpoint + headers + validation
    shape to exercise its branches. The fixture below constructs one per
    test so individual cases can override hooks (validation status set,
    retryable status check, validation reject shape).
    """


def _make_base_http_post_sampler(
    *,
    base_url: str = "https://api.example.test",
    validation_reject_statuses: frozenset[int] | None = None,
    is_retryable_status=None,
    reject_response=None,
):
    """Build a concrete BaseHTTPPostSampler with sensible defaults so each
    test can poke individual hooks without re-declaring the whole subclass."""
    from nimble_benchmark.retry import RetryConfig
    from nimble_benchmark.samplers._http_post_base import BaseHTTPPostSampler

    class _Sampler(BaseHTTPPostSampler):
        def _endpoint(self) -> str:
            return "/search"

        def _headers(self) -> dict[str, str]:
            return {"Authorization": "Bearer test", "Content-Type": "application/json"}

        def _build_payload(self, query: str) -> dict:
            return {"query": query}

        def _validation_reject_response(self) -> dict:
            return reject_response if reject_response is not None else {"results": []}

    if validation_reject_statuses is not None:
        _Sampler._validation_reject_statuses = validation_reject_statuses
    if is_retryable_status is not None:
        _Sampler._is_retryable_status = lambda self, status: is_retryable_status(status)

    return _Sampler(
        name="test_sampler",
        api_key="k",
        base_url=base_url,
        timeout=5.0,
        retry_config=RetryConfig(max_attempts=2, initial_wait=0.0, max_wait=0.0),
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_base_http_post_sampler_happy_path_returns_parsed_json():
    sampler = _make_base_http_post_sampler()
    with aioresponses() as mocked:
        mocked.post("https://api.example.test/search", status=200, payload={"results": [{"url": "x"}]})
        raw = await sampler.get_search_results("hello")
    assert raw == {"results": [{"url": "x"}]}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_base_http_post_sampler_validation_reject_default_400_and_422():
    """400 and 422 both map to the provider-specific validation-reject shape
    with the ``_status`` sentinel attached for the runner to surface."""
    sampler = _make_base_http_post_sampler()
    with aioresponses() as mocked:
        mocked.post("https://api.example.test/search", status=422, payload={"detail": "bad"})
        raw = await sampler.get_search_results("hello")
    assert raw == {"results": [], "_status": "validation_reject"}
    assert sampler._last_status == "validation_reject"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_base_http_post_sampler_custom_validation_reject_override():
    """Subclasses can widen/narrow the validation-reject status set."""
    sampler = _make_base_http_post_sampler(validation_reject_statuses=frozenset({418}))
    with aioresponses() as mocked:
        # 422 is no longer treated as a validation reject -- the base falls
        # back to raise_for_status which produces a failed_after_retries
        # status when wrapped by the runner's `sample()`.
        mocked.post("https://api.example.test/search", status=418, payload={"detail": "teapot"})
        raw = await sampler.get_search_results("hello")
    assert raw == {"results": [], "_status": "validation_reject"}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_base_http_post_sampler_retries_then_succeeds():
    """A 429 raises TransientHTTPError, the retry wrapper retries, then
    succeeds on the second attempt."""
    sampler = _make_base_http_post_sampler()
    with aioresponses() as mocked:
        mocked.post("https://api.example.test/search", status=429, body="rate limited")
        mocked.post("https://api.example.test/search", status=200, payload={"results": [{"url": "ok"}]})
        raw = await sampler.get_search_results("hello")
    assert raw == {"results": [{"url": "ok"}]}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_base_http_post_sampler_captures_last_headers():
    """The shared loop must always populate ``_last_headers`` so
    ``parse_server_timing_total_dur`` can read it for the latency metric."""
    sampler = _make_base_http_post_sampler()
    with aioresponses() as mocked:
        mocked.post(
            "https://api.example.test/search",
            status=200,
            payload={"results": []},
            headers={"Server-Timing": "total;dur=42.5"},
        )
        await sampler.get_search_results("hello")
    assert sampler._last_headers.get("Server-Timing") == "total;dur=42.5"


# === New search-only samplers (Section 1) =============================================


@pytest.mark.unit
def test_exa_search_payload_carries_type_summary_contents_and_num_results() -> None:
    """``type`` is sent explicitly even for ``auto`` (Exa's own default) so the
    request on the wire records which lane produced the row."""
    from nimble_benchmark.samplers.exa_search import ExaSearchSampler

    sampler = ExaSearchSampler(name="exa_search_auto", api_key="ek")
    payload = sampler._build_payload("messi age")
    assert payload == {
        "query": "messi age",
        "type": "auto",
        "numResults": 10,
        "contents": {"summary": True},
    }
    assert sampler._headers()["x-api-key"] == "ek"


@pytest.mark.unit
def test_exa_search_rejects_unknown_type() -> None:
    """The legacy ``neural`` / ``keyword`` / ``hybrid`` names are not accepted:
    Exa's current docs call them legacy terminology, not a supported setting."""
    from nimble_benchmark.samplers.exa_search import ExaSearchSampler

    for bad_type in ("neural", "keyword", "hybrid", "turbo"):
        with pytest.raises(ValueError, match="search_type must be one of"):
            ExaSearchSampler(name="exa_search_auto", api_key="ek", search_type=bad_type)


@pytest.mark.unit
def test_exa_search_response_kind_is_search_results() -> None:
    from nimble_benchmark.samplers.exa_search import ExaSearchSampler

    sampler = ExaSearchSampler(name="exa_search_auto", api_key="ek")
    assert sampler.response_kind() == "search_results"


@pytest.mark.unit
def test_exa_search_extract_chunks_drops_url_only_results_without_summary() -> None:
    """Exa's search endpoint sometimes returns URL+title-only entries with no
    summary. Those carry no judgeable passage so we drop them."""
    from nimble_benchmark.samplers.exa_search import ExaSearchSampler

    sampler = ExaSearchSampler(name="exa_search_auto", api_key="ek")
    raw = {
        "results": [
            {"url": "https://a.example", "title": "A", "summary": "summary a"},
            {"url": "https://drop.example", "title": "no summary"},
            {"url": "", "title": "no url", "summary": "irrelevant"},
            {"url": "https://b.example", "title": "B", "summary": "summary b"},
        ]
    }
    chunks = sampler.extract_chunks(raw)
    assert [chunk.url for chunk in chunks] == ["https://a.example", "https://b.example"]


@pytest.mark.unit
def test_parallel_search_payload_shape_and_auth_header() -> None:
    """``search_queries`` is Parallel's only required field — ``objective``
    alone is rejected upstream — so both carry the raw SimpleQA question.
    ``max_results`` lives under ``advanced_settings``, not at the top level."""
    from nimble_benchmark.samplers.parallel_search import ParallelSearchSampler

    sampler = ParallelSearchSampler(name="parallel_search_basic", api_key="pk", mode="basic")
    payload = sampler._build_payload("who won the 2018 fifa world cup")
    assert payload == {
        "objective": "who won the 2018 fifa world cup",
        "search_queries": ["who won the 2018 fifa world cup"],
        "mode": "basic",
        "advanced_settings": {"max_results": 10},
    }
    assert sampler._headers()["x-api-key"] == "pk"
    assert sampler._endpoint() == "/v1/search"


@pytest.mark.unit
def test_parallel_search_max_chars_per_result_nests_under_excerpt_settings() -> None:
    """Only emit ``excerpt_settings`` when a cap is configured, so the default
    run sends Parallel's own per-result budget rather than pinning one."""
    from nimble_benchmark.samplers.parallel_search import ParallelSearchSampler

    default = ParallelSearchSampler(name="parallel_search_basic", api_key="pk")
    assert "excerpt_settings" not in default._build_payload("q")["advanced_settings"]

    capped = ParallelSearchSampler(name="parallel_search_basic", api_key="pk", max_chars_per_result=1500)
    assert capped._build_payload("q")["advanced_settings"]["excerpt_settings"] == {"max_chars_per_result": 1500}


@pytest.mark.unit
def test_parallel_search_response_kind_is_search_results() -> None:
    from nimble_benchmark.samplers.parallel_search import ParallelSearchSampler

    sampler = ParallelSearchSampler(name="parallel_search_basic", api_key="pk")
    assert sampler.response_kind() == "search_results"


@pytest.mark.unit
def test_parallel_search_rejects_unknown_mode() -> None:
    from nimble_benchmark.samplers.parallel_search import ParallelSearchSampler

    with pytest.raises(ValueError, match="mode must be one of"):
        ParallelSearchSampler(name="parallel_search_basic", api_key="pk", mode="deep")


@pytest.mark.unit
def test_parallel_search_extract_chunks_joins_excerpts_and_drops_empty() -> None:
    """Parallel returns multiple ``excerpts`` per URL. They are joined into
    ``description`` because the eval-side synthesizer only reads
    title/url/description — anything parked in ``extra_snippets`` would be
    judged but never visible to synthesis. Results with no excerpt text carry
    no judgeable passage, so they are dropped like Exa's summary-less rows."""
    from nimble_benchmark.samplers.parallel_search import ParallelSearchSampler

    sampler = ParallelSearchSampler(name="parallel_search_basic", api_key="pk")
    raw = {
        "search_id": "s1",
        "session_id": "sess1",
        "results": [
            {"url": "https://a.example", "title": "A", "excerpts": ["first", "second"]},
            {"url": "https://drop.example", "title": "no excerpts", "excerpts": []},
            {"url": "", "title": "no url", "excerpts": ["irrelevant"]},
            {"url": "https://b.example", "title": "B", "excerpts": ["only"]},
        ],
    }

    chunks = sampler.extract_chunks(raw)

    assert [chunk.url for chunk in chunks] == ["https://a.example", "https://b.example"]
    assert chunks[0].description == "first\n\nsecond"
    assert chunks[0].extra_snippets == []
    assert [chunk.position for chunk in chunks] == [0, 3]


@pytest.mark.unit
def test_build_samplers_exa_search_auto_requires_key() -> None:
    settings = Settings(openai_api_key="oai", _env_file=None)
    with pytest.raises(ValueError, match="exa_search_auto requires EXA_API_KEY"):
        build_samplers(settings=settings, sampler_names=["exa_search_auto"])


@pytest.mark.unit
def test_build_samplers_exa_search_fast_requires_key() -> None:
    settings = Settings(openai_api_key="oai", _env_file=None)
    with pytest.raises(ValueError, match="exa_search_fast requires EXA_API_KEY"):
        build_samplers(settings=settings, sampler_names=["exa_search_fast"])


@pytest.mark.unit
def test_build_samplers_exa_lanes_pin_their_types() -> None:
    """The two Exa search lanes exist so both product tiers are always measured;
    the type is pinned per lane, never read from the environment."""
    settings = Settings(exa_api_key="ek", openai_api_key="oai", _env_file=None)
    built = build_samplers(
        settings=settings,
        sampler_names=["exa_search_auto", "exa_search_fast"],
    )
    types = {sampler.name: sampler.search_type for sampler in built}
    assert types == {"exa_search_auto": "auto", "exa_search_fast": "fast"}
    payload_types = {sampler.name: sampler._build_payload("q")["type"] for sampler in built}
    assert payload_types == {"exa_search_auto": "auto", "exa_search_fast": "fast"}


@pytest.mark.unit
def test_exa_search_type_env_var_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    """An ``EXA_SEARCH_TYPE`` in someone's .env must not collapse both lanes onto
    one type (Settings uses ``extra="ignore"``)."""
    monkeypatch.setenv("EXA_SEARCH_TYPE", "fast")
    settings = Settings(exa_api_key="ek", openai_api_key="oai", _env_file=None)
    assert not hasattr(settings, "exa_search_type")
    built = build_samplers(settings=settings, sampler_names=["exa_search_auto"])
    assert next(s for s in built if s.name == "exa_search_auto").search_type == "auto"


@pytest.mark.unit
def test_build_samplers_parallel_search_basic_requires_key() -> None:
    settings = Settings(openai_api_key="oai", _env_file=None)
    with pytest.raises(ValueError, match="parallel_search_basic requires PARALLEL_API_KEY"):
        build_samplers(settings=settings, sampler_names=["parallel_search_basic"])


@pytest.mark.unit
def test_build_samplers_parallel_search_turbo_requires_key() -> None:
    settings = Settings(openai_api_key="oai", _env_file=None)
    with pytest.raises(ValueError, match="parallel_search_turbo requires PARALLEL_API_KEY"):
        build_samplers(settings=settings, sampler_names=["parallel_search_turbo"])


@pytest.mark.unit
def test_build_samplers_parallel_lanes_pin_their_modes() -> None:
    """The two Parallel lanes exist so both product tiers are always measured;
    the mode is pinned per lane, never read from the environment."""
    settings = Settings(parallel_api_key="pk", openai_api_key="oai", _env_file=None)
    built = build_samplers(
        settings=settings,
        sampler_names=["parallel_search_basic", "parallel_search_turbo"],
    )
    modes = {sampler.name: sampler.mode for sampler in built}
    assert modes == {"parallel_search_basic": "basic", "parallel_search_turbo": "turbo"}
    payload_modes = {sampler.name: sampler._build_payload("q")["mode"] for sampler in built}
    assert payload_modes == {"parallel_search_basic": "basic", "parallel_search_turbo": "turbo"}


@pytest.mark.unit
def test_parallel_search_mode_env_var_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    """A stale ``PARALLEL_SEARCH_MODE`` in someone's .env must not collapse both
    lanes onto one mode (Settings uses ``extra="ignore"``)."""
    monkeypatch.setenv("PARALLEL_SEARCH_MODE", "turbo")
    settings = Settings(parallel_api_key="pk", openai_api_key="oai", _env_file=None)
    assert not hasattr(settings, "parallel_search_mode")
    built = build_samplers(settings=settings, sampler_names=["parallel_search_basic"])
    assert next(s for s in built if s.name == "parallel_search_basic").mode == "basic"


@pytest.mark.unit
def test_parallel_lanes_share_one_rate_limit_bucket() -> None:
    """Parallel's 600/min quota is per API key, not per lane, so running both
    modes must not double the request rate against it."""
    settings = Settings(parallel_api_key="pk", openai_api_key="oai", _env_file=None)
    built = build_samplers(
        settings=settings,
        sampler_names=["parallel_search_basic", "parallel_search_turbo"],
    )
    limiters = {id(sampler._limiter) for sampler in built}
    assert len(limiters) == 1


@pytest.mark.unit
def test_nimble_lanes_throttle_per_product_bucket():
    """Nimble's server caps each *product* at 10 RPS (search_fast and
    search_lite are separate buckets) -- an unthrottled n=1000 run lost 4.4% of
    rows to 429s that outlived the retry budget. Lanes on the same product must
    share one limiter; different products must not."""
    from nimble_benchmark.samplers._rate_limit import reset_limiters
    from nimble_benchmark.samplers.nimble_search import NimbleSearchSampler

    reset_limiters()
    try:
        fast = NimbleSearchSampler(name="nimble_search", base_url="http://t", api_key="k", search_depth="fast")
        lite = NimbleSearchSampler(name="nimble_search", base_url="http://t", api_key="k", search_depth="lite")
        lite2 = NimbleSearchSampler(name="nimble_search", base_url="http://t", api_key="k", search_depth="lite")

        assert fast.rate_per_second == 9.0
        assert fast._limiter is not lite._limiter  # different products
        assert lite._limiter is lite2._limiter  # same product -> shared bucket
    finally:
        reset_limiters()


@pytest.mark.unit
def test_nimble_rate_env_override(monkeypatch):
    from nimble_benchmark.samplers.nimble_search import NimbleSearchSampler

    monkeypatch.setenv("NIMBLE_RATE_PER_SECOND", "25")
    sampler = NimbleSearchSampler(name="nimble_search", base_url="http://t", api_key="k")
    assert sampler.rate_per_second == 25.0


@pytest.mark.unit
def test_nimble_search_follows_the_configured_depth() -> None:
    """``nimble_search`` is the one depth-configurable lane: NIMBLE_SEARCH_DEPTH
    is what selects the product tier the row measures, and the recorded config
    is what lets the Cost column price it at that tier."""
    settings = Settings(nimble_api_key="prod", nimble_search_depth="deep", openai_api_key="oai", _env_file=None)

    built = build_samplers(settings=settings, sampler_names=["nimble_search"])
    lane = built[0]

    assert lane.search_depth == "deep"
    assert lane._build_payload("q")["search_depth"] == "deep"


@pytest.mark.unit
def test_nimble_search_omits_full_content_when_not_opted_in() -> None:
    """``full_content`` defaults to false server-side, so an opted-out lane must
    not send the key at all: a deployment that predates the flag can reject an
    unknown field with a 400/422."""
    from nimble_benchmark.samplers.nimble_search import NimbleSearchSampler

    sampler = NimbleSearchSampler(name="nimble_search", base_url="http://t", api_key="k", search_depth="fast")

    assert sampler.full_content is False
    assert "full_content" not in sampler._build_payload("q")


@pytest.mark.unit
def test_full_content_does_not_split_the_product_rate_bucket() -> None:
    """``full_content`` is a request flag, not a product tier, so it must not
    open a second 9 RPS bucket against the same ``search_fast`` ceiling. Two
    independent buckets would double the issue rate and re-trigger the 429s the
    self-throttle exists to prevent."""
    from nimble_benchmark.samplers._rate_limit import reset_limiters
    from nimble_benchmark.samplers.nimble_search import NimbleSearchSampler

    reset_limiters()
    try:
        fast = NimbleSearchSampler(name="nimble_search", base_url="http://t", api_key="k", search_depth="fast")
        full = NimbleSearchSampler(
            name="nimble_search",
            base_url="http://t",
            api_key="k",
            search_depth="fast",
            full_content=True,
        )
        assert full._limiter is fast._limiter
    finally:
        reset_limiters()


@pytest.mark.unit
def test_brave_search_defaults_operators_off_for_simpleqa():
    """REGRESSION GUARD, not a style preference. Brave defaults
    ``operators=true``, which treats SimpleQA's quoted article/book/song titles
    as required exact-match phrases; the phrase rarely appears verbatim on an
    indexed page, so ~12% of the dataset came back empty in the original Brave
    run. Flipping this default back silently craters recall."""
    from nimble_benchmark.samplers.brave_search import BraveSearchSampler

    sampler = BraveSearchSampler(name="brave_search", api_key="bk")
    assert sampler.operators is False
    params = sampler._build_payload('who wrote "Song of Myself"')
    assert params["operators"] == "false"
    # Booleans must be lowercase strings -- aiohttp will not encode Python bools
    # into a query string.
    assert params["extra_snippets"] == "true"
    assert isinstance(params["extra_snippets"], str)


@pytest.mark.unit
def test_brave_search_uses_get_with_subscription_token_header():
    from nimble_benchmark.samplers.brave_search import BraveSearchSampler

    sampler = BraveSearchSampler(name="brave_search", api_key="bk")
    assert sampler._endpoint() == "/res/v1/web/search"
    headers = sampler._headers()
    assert headers["X-Subscription-Token"] == "bk"
    assert headers["Accept"] == "application/json"
    assert sampler._build_payload("q") == {
        "q": "q",
        "count": 10,
        "extra_snippets": "true",
        "operators": "false",
    }


@pytest.mark.unit
def test_brave_search_treats_422_as_validation_reject_not_retry():
    """Brave 422s on some long queries and will never accept them, so retrying
    only burns the 2,000/month free allowance. 429 and 5xx must still retry."""
    from nimble_benchmark.samplers.brave_search import BraveSearchSampler

    sampler = BraveSearchSampler(name="brave_search", api_key="bk")
    assert 422 in sampler._validation_reject_statuses
    assert sampler._validation_reject_response() == {"web": {"results": []}}
    assert sampler._is_retryable_status(429) is True
    assert sampler._is_retryable_status(503) is True
    assert sampler._is_retryable_status(422) is False


@pytest.mark.unit
def test_brave_search_default_rate_is_free_tier_cap():
    """1 RPS is Brave's free-tier hard cap and the tightest ceiling of any lane."""
    from nimble_benchmark.samplers.brave_search import BraveSearchSampler

    assert BraveSearchSampler(name="brave_search", api_key="bk").rate_per_second == 1.0
    assert BraveSearchSampler(name="brave_search", api_key="bk", rate_per_second=20.0).rate_per_second == 20.0


@pytest.mark.unit
def test_brave_search_rate_honors_env_override(monkeypatch):
    from nimble_benchmark.samplers.brave_search import BraveSearchSampler

    monkeypatch.setenv("BRAVE_RATE_PER_SECOND", "20")
    assert BraveSearchSampler(name="brave_search", api_key="bk").rate_per_second == 20.0


@pytest.mark.unit
def test_brave_search_extract_chunks_reads_nested_web_results():
    """Brave nests results under ``web.results``, not a top-level ``results``.
    Rows with neither a description nor any extra snippet carry no judgeable
    passage and are dropped."""
    from nimble_benchmark.samplers.brave_search import BraveSearchSampler

    sampler = BraveSearchSampler(name="brave_search", api_key="bk")
    raw = {
        "web": {
            "results": [
                {"url": "https://a.example", "title": "A", "description": "desc a", "extra_snippets": ["x", "y"]},
                {"url": "https://drop.example", "title": "empty", "description": "", "extra_snippets": []},
                {"url": "", "title": "no url", "description": "irrelevant"},
                {"url": "https://b.example", "title": "B", "description": "", "extra_snippets": ["only snippet"]},
            ]
        }
    }

    chunks = sampler.extract_chunks(raw)

    assert [chunk.url for chunk in chunks] == ["https://a.example", "https://b.example"]
    assert chunks[0].extra_snippets == ["x", "y"]
    assert chunks[1].description == ""
    assert chunks[1].extra_snippets == ["only snippet"]


@pytest.mark.unit
def test_build_samplers_brave_search_requires_key():
    settings = Settings(openai_api_key="oai", _env_file=None)
    with pytest.raises(ValueError, match="brave_search requires BRAVE_SEARCH_API_KEY"):
        build_samplers(settings=settings, sampler_names=["brave_search"])


@pytest.mark.unit
def test_exa_default_rate_is_safe_for_default_quota():
    """Exa's documented default is 10 QPS on /search and /answer. We
    target 9 to leave a small safety margin; a regression above 10
    would put us back in retry-loop territory under bursty fan-out."""
    from nimble_benchmark.samplers.exa_search import (
        DEFAULT_EXA_RATE_PER_SECOND,
        ExaSearchSampler,
    )

    assert DEFAULT_EXA_RATE_PER_SECOND <= 10.0
    sampler = ExaSearchSampler(name="exa_search_auto", api_key="k")
    assert sampler.rate_per_second == DEFAULT_EXA_RATE_PER_SECOND


@pytest.mark.unit
def test_exa_rate_overridable_via_env_var(monkeypatch):
    from nimble_benchmark.samplers.exa_search import ExaSearchSampler

    monkeypatch.setenv("EXA_RATE_PER_SECOND", "20")
    sampler = ExaSearchSampler(name="exa_search_auto", api_key="k")
    assert sampler.rate_per_second == 20.0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_base_http_post_sampler_acquires_limiter_for_every_attempt():
    """Limiter MUST be acquired inside ``attempt()`` so a retry-on-429
    still pays the rate-limit cost. If it were acquired once outside the
    retry loop, the second attempt would bypass the throttle and bust
    the per-key QPS ceiling -- exactly the failure we're fixing."""
    from unittest.mock import AsyncMock, MagicMock

    sampler = _make_base_http_post_sampler()
    fake_limiter = MagicMock()
    fake_limiter.__aenter__ = AsyncMock(return_value=fake_limiter)
    fake_limiter.__aexit__ = AsyncMock(return_value=None)
    sampler._limiter = fake_limiter

    with aioresponses() as mocked:
        mocked.post("https://api.example.test/search", status=429, body="rate limited")
        mocked.post("https://api.example.test/search", status=200, payload={"results": []})
        await sampler.get_search_results("hello")

    # Two attempts -> two limiter acquires.
    assert fake_limiter.__aenter__.await_count == 2


@pytest.mark.unit
@pytest.mark.asyncio
async def test_base_http_post_sampler_without_limiter_still_works():
    """Samplers that don't pass a limiter (e.g. Nimble's own server,
    where we control the backend) must keep behaving exactly as before
    -- the limiter plumbing is strictly opt-in."""
    sampler = _make_base_http_post_sampler()
    assert sampler._limiter is None
    with aioresponses() as mocked:
        mocked.post("https://api.example.test/search", status=200, payload={"results": [{"url": "x"}]})
        raw = await sampler.get_search_results("hello")
    assert raw == {"results": [{"url": "x"}]}


# ----------------------------------------------------------------------
# firecrawl_search -- SDK-backed lane (AsyncFirecrawl.search), not aiohttp.
# ----------------------------------------------------------------------


class FakeFirecrawlClient:
    """Stand-in for ``AsyncFirecrawl``: records calls, replays queued errors.

    ``errors`` are raised in order before the success value is returned, which
    is how the retry test drives a 429-then-200 sequence.
    """

    def __init__(self, data: Any = None, errors: list[Exception] | None = None) -> None:
        self.data = data
        self.errors = list(errors or [])
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def search(self, query: str, **kwargs: Any) -> Any:
        self.calls.append((query, kwargs))
        if self.errors:
            raise self.errors.pop(0)
        return self.data


def _make_firecrawl_search_data(results: list[dict[str, Any]]) -> Any:
    from firecrawl.v2.types import SearchData, SearchResultWeb

    return SearchData(web=[SearchResultWeb(**result) for result in results])


def _firecrawl_error(status: int, message: str = "boom") -> Exception:
    from firecrawl.v2.utils.error_handler import FirecrawlError

    return FirecrawlError(message, status)


def _make_firecrawl_sampler(
    monkeypatch: pytest.MonkeyPatch, client: FakeFirecrawlClient, **kwargs: Any
) -> FirecrawlSearchSampler:
    sampler = FirecrawlSearchSampler(name="firecrawl_search", api_key="fc-key", **kwargs)
    monkeypatch.setattr(sampler, "_get_client", lambda: client)
    return sampler


@pytest.mark.unit
def test_firecrawl_search_is_search_kind() -> None:
    sampler = FirecrawlSearchSampler(name="firecrawl_search", api_key="fc-key")

    assert sampler.response_kind() == "search_results"
    assert sampler.extract_usage({"web": []}) == ProviderUsage()


@pytest.mark.unit
def test_firecrawl_search_kwargs_pin_web_source_and_server_timeout() -> None:
    """``sources`` is sent explicitly rather than left to the server default so
    the recorded config fully determines the request, and the server-side
    ``timeout`` (milliseconds) is pinned to our own per-attempt budget instead
    of the SDK's 5-minute default -- otherwise a slow upstream outlives the row."""
    sampler = FirecrawlSearchSampler(name="firecrawl_search", api_key="fc-key", timeout=30.0)

    assert sampler._search_kwargs() == {"limit": 10, "sources": ["web"], "timeout": 30_000}


@pytest.mark.unit
def test_firecrawl_search_num_results_avoids_colliding_with_run_limit() -> None:
    """The knob is ``num_results`` (not the SDK's ``limit``) because the
    per-sampler recorded config already carries the run's row ``limit``; two keys
    named ``limit`` would collide in sampler_config_firecrawl_search.json."""
    sampler = FirecrawlSearchSampler(name="firecrawl_search", api_key="fc-key", num_results=3)

    assert sampler.num_results == 3
    assert sampler._search_kwargs()["limit"] == 3
    assert not hasattr(sampler, "limit")


@pytest.mark.unit
def test_firecrawl_search_canonicalizes_api_url_in_recorded_config() -> None:
    """``api_url`` is recorded as ``base_url`` in the per-sampler config the run
    writes, so a trailing slash must not change it: two runs configured
    identically apart from that slash would otherwise look differently
    parameterized in their artifacts. Every aiohttp sampler already normalizes
    its ``base_url`` the same way."""
    from nimble_benchmark.cli import _sampler_config

    args = SimpleNamespace(limit=5, random_state=0)
    bare = FirecrawlSearchSampler(name="firecrawl_search", api_key="fc-key")
    slashed = FirecrawlSearchSampler(name="firecrawl_search", api_key="fc-key", api_url="https://api.firecrawl.dev/")

    assert slashed.api_url == bare.api_url == "https://api.firecrawl.dev"
    assert _sampler_config(slashed, args) == _sampler_config(bare, args)
    assert _sampler_config(bare, args)["base_url"] == "https://api.firecrawl.dev"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_firecrawl_search_sample_normalizes_sdk_models_to_plain_dicts(monkeypatch: pytest.MonkeyPatch) -> None:
    """``raw`` is persisted to CSV, so the pydantic models the SDK returns have
    to be flattened to plain data before they leave the sampler."""
    client = FakeFirecrawlClient(
        _make_firecrawl_search_data(
            [
                {"url": "https://a.example", "title": "A", "description": "first", "position": 1},
                {"url": "https://b.example", "title": "B", "description": "second", "position": 2},
            ]
        )
    )
    sampler = _make_firecrawl_sampler(monkeypatch, client)

    response = await sampler.sample("who won the 2018 fifa world cup")

    assert response.status == "ok"
    assert response.response_kind == "search_results"
    assert client.calls[0][0] == "who won the 2018 fifa world cup"
    assert client.calls[0][1] == {"limit": 10, "sources": ["web"], "timeout": 60_000}
    assert all(isinstance(result, dict) for result in response.raw["web"])
    assert [chunk.url for chunk in response.chunks] == ["https://a.example", "https://b.example"]
    assert [chunk.description for chunk in response.chunks] == ["first", "second"]


@pytest.mark.unit
def test_firecrawl_search_extract_chunks_drops_urlless_but_keeps_empty_snippets() -> None:
    """A result with no URL carries nothing the URL metrics can score, so it
    goes. A result with a URL but no ``description`` stays: unlike Exa's
    generated summary, Firecrawl's description is a plain SERP snippet, and
    dropping the row would silently cost the lane a ranked URL on NDCG@10 /
    Recall@10. Positions stay tied to the provider's own ordering."""
    sampler = FirecrawlSearchSampler(name="firecrawl_search", api_key="fc-key")

    chunks = sampler.extract_chunks(
        {
            "web": [
                {"url": "https://a.example", "title": "A", "description": "snippet"},
                {"url": "", "title": "no url", "description": "dropped"},
                {"url": "https://b.example", "title": "B"},
                "not-a-dict",
            ]
        }
    )

    assert chunks == [
        RetrievalChunk(url="https://a.example", title="A", description="snippet", extra_snippets=[], position=0),
        RetrievalChunk(url="https://b.example", title="B", description="", extra_snippets=[], position=2),
    ]
    assert sampler.extract_chunks({}) == []
    assert sampler.extract_chunks({"web": None}) == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_firecrawl_search_treats_400_as_validation_reject(monkeypatch: pytest.MonkeyPatch) -> None:
    client = FakeFirecrawlClient(errors=[_firecrawl_error(400, "Bad Request: Failed to search.")])
    sampler = _make_firecrawl_sampler(monkeypatch, client)

    response = await sampler.sample("bad query")

    assert response.status == "validation_reject"
    assert response.raw["web"] == []
    assert response.chunks == []
    # One attempt only -- the upstream will refuse this query again.
    assert len(client.calls) == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_firecrawl_search_treats_client_side_validation_error_as_reject(monkeypatch: pytest.MonkeyPatch) -> None:
    """The SDK validates the request before sending it (empty query, bad limit)
    and raises ``ValueError``. Same outcome as a server 400: retrying is futile."""
    client = FakeFirecrawlClient(errors=[ValueError("Query cannot be empty")])
    sampler = _make_firecrawl_sampler(monkeypatch, client)

    response = await sampler.sample("   ")

    assert response.status == "validation_reject"
    assert response.raw["web"] == []
    assert len(client.calls) == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_firecrawl_search_retries_rate_limit_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    client = FakeFirecrawlClient(
        _make_firecrawl_search_data([{"url": "https://a.example", "title": "A", "description": "s"}]),
        errors=[_firecrawl_error(429, "Rate Limit Exceeded: Failed to search.")],
    )
    sampler = _make_firecrawl_sampler(
        monkeypatch,
        client,
        retry_config=retry.RetryConfig(max_attempts=2, initial_wait=0.0, max_wait=0.0),
    )

    response = await sampler.sample("retry")

    assert response.status == "ok"
    assert len(client.calls) == 2
    assert [chunk.url for chunk in response.chunks] == ["https://a.example"]


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 402, 403])
async def test_firecrawl_search_does_not_swallow_auth_or_billing_errors(
    monkeypatch: pytest.MonkeyPatch, status: int
) -> None:
    """401/402/403 are run-level problems (dead key, exhausted credits), not
    per-query rejects. They must fail the row so errors.md groups them under
    auth/billing instead of the row quietly recording zero results."""
    client = FakeFirecrawlClient(errors=[_firecrawl_error(status, "Payment Required: Failed to search.")])
    sampler = _make_firecrawl_sampler(monkeypatch, client)

    response = await sampler.sample("q")

    assert response.status == "failed_after_retries"
    assert response.chunks == []
    assert len(client.calls) == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_firecrawl_search_wraps_transport_errors_as_transient(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    client = FakeFirecrawlClient(
        errors=[httpx.ConnectError("connection refused"), httpx.ConnectError("connection refused")]
    )
    sampler = _make_firecrawl_sampler(
        monkeypatch,
        client,
        retry_config=retry.RetryConfig(max_attempts=2, initial_wait=0.0, max_wait=0.0),
    )

    response = await sampler.sample("q")

    assert response.status == "failed_after_retries"
    # Transport failures are retried; auth/billing ones (above) are not.
    assert len(client.calls) == 2


@pytest.mark.unit
@pytest.mark.asyncio
async def test_firecrawl_search_acquires_limiter_on_every_attempt(monkeypatch: pytest.MonkeyPatch) -> None:
    """Same invariant the aiohttp base class enforces: a retry must not bypass
    the per-key QPS gate just because the first attempt already paid for it."""
    from unittest.mock import AsyncMock, MagicMock

    client = FakeFirecrawlClient(
        _make_firecrawl_search_data([{"url": "https://a.example", "description": "s"}]),
        errors=[_firecrawl_error(500, "Internal Server Error")],
    )
    sampler = _make_firecrawl_sampler(
        monkeypatch,
        client,
        retry_config=retry.RetryConfig(max_attempts=2, initial_wait=0.0, max_wait=0.0),
    )
    fake_limiter = MagicMock()
    fake_limiter.__aenter__ = AsyncMock(return_value=fake_limiter)
    fake_limiter.__aexit__ = AsyncMock(return_value=None)
    sampler._limiter = fake_limiter

    await sampler.get_search_results("hello")

    assert fake_limiter.__aenter__.await_count == 2


@pytest.mark.unit
def test_firecrawl_search_rate_and_timeout_are_env_tunable(monkeypatch: pytest.MonkeyPatch) -> None:
    from nimble_benchmark.samplers._rate_limit import reset_limiters

    reset_limiters()
    default = FirecrawlSearchSampler(name="firecrawl_search", api_key="fc-key")
    # Default sits above Firecrawl's free tier (10/min) on purpose; the free
    # plan needs FIRECRAWL_RATE_PER_SECOND=0.16. See the module docstring.
    assert default.rate_per_second == 1.0
    assert default._timeout_s == 60.0

    monkeypatch.setenv("FIRECRAWL_RATE_PER_SECOND", "0.16")
    monkeypatch.setenv("FIRECRAWL_TIMEOUT_S", "12.5")
    tuned = FirecrawlSearchSampler(name="firecrawl_search", api_key="fc-key")
    assert tuned.rate_per_second == 0.16
    assert tuned._timeout_s == 12.5

    # Explicit caller arg still wins over the env var.
    explicit = FirecrawlSearchSampler(name="firecrawl_search", api_key="fc-key", rate_per_second=4.0)
    assert explicit.rate_per_second == 4.0
    reset_limiters()


@pytest.mark.unit
def test_firecrawl_search_client_is_built_lazily_and_reused(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sampler construction must not build transport (build_samplers runs it,
    and so does every unit test), and the client is reused across queries."""
    from nimble_benchmark.samplers import firecrawl_search as module

    sampler = module.FirecrawlSearchSampler(name="firecrawl_search", api_key="fc-key")
    assert sampler._client is None

    built: list[dict[str, Any]] = []

    class _StubClient:
        def __init__(self, **kwargs: Any) -> None:
            built.append(kwargs)

    monkeypatch.setattr(module, "AsyncFirecrawl", _StubClient)

    first = sampler._get_client()
    assert sampler._get_client() is first
    assert len(built) == 1
    # Retry belongs to retry_async, not the SDK's own loop -- otherwise the
    # SDK would retry inside the limiter gate and bypass the QPS ceiling.
    assert built[0]["max_retries"] == 1
    assert built[0]["timeout"] == 60.0
    assert built[0]["api_url"] == "https://api.firecrawl.dev"


@pytest.mark.unit
def test_build_samplers_firecrawl_search_requires_key() -> None:
    settings = Settings(openai_api_key="oai", _env_file=None)
    with pytest.raises(ValueError, match="firecrawl_search requires FIRECRAWL_API_KEY"):
        build_samplers(settings=settings, sampler_names=["firecrawl_search"])

    configured = Settings(firecrawl_api_key="fc-key", openai_api_key="oai", _env_file=None)
    built = build_samplers(settings=configured, sampler_names=["firecrawl_search"])
    assert built[0].name == "firecrawl_search"
    assert built[0].api_key == "fc-key"
    assert built[0].num_results == 10


def _install_firecrawl_mock_transport(sampler: FirecrawlSearchSampler, handler: Callable[[Request], Response]) -> None:
    """Point the SDK's httpx client at an in-memory transport.

    Reaches through the SDK's private client because ``firecrawl-py`` exposes
    no injection seam. Worth the reach: the tests above mock at the
    ``AsyncFirecrawl.search`` boundary and so cannot catch an SDK upgrade that
    changes request shaping, response parsing, or error typing -- which is
    exactly what this lane delegates to the SDK for.
    """
    import httpx

    sampler._get_client()._v2_client.async_http_client._client = httpx.AsyncClient(
        base_url="https://api.firecrawl.dev",
        transport=httpx.MockTransport(handler),
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_firecrawl_search_end_to_end_through_the_sdk() -> None:
    """Full stack against a mocked transport: our kwargs -> the SDK's wire
    payload -> its response parsing -> our chunks."""
    import httpx

    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        assert request.url.path == "/v2/search"
        return httpx.Response(
            200,
            json={
                "success": True,
                "data": {
                    "web": [
                        {
                            "url": "https://en.wikipedia.org/wiki/2018_FIFA_World_Cup",
                            "title": "2018 FIFA World Cup",
                            "description": "France won the final 4-2.",
                            "position": 1,
                        },
                        {"url": "https://www.fifa.com/worldcup/2018", "title": "FIFA", "position": 2},
                    ]
                },
            },
        )

    sampler = FirecrawlSearchSampler(name="firecrawl_search", api_key="fc-key")
    _install_firecrawl_mock_transport(sampler, handler)

    response = await sampler.sample("who won the 2018 fifa world cup")

    assert response.status == "ok"
    # ``sources`` is expanded by the SDK from ["web"] to [{"type": "web"}].
    assert seen[0]["query"] == "who won the 2018 fifa world cup"
    assert seen[0]["sources"] == [{"type": "web"}]
    assert seen[0]["limit"] == 10
    assert seen[0]["timeout"] == 60_000
    assert [chunk.url for chunk in response.chunks] == [
        "https://en.wikipedia.org/wiki/2018_FIFA_World_Cup",
        "https://www.fifa.com/worldcup/2018",
    ]
    assert response.chunks[0].description == "France won the final 4-2."
    assert response.chunks[1].description == ""


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "expected_status", "expected_attempts"),
    [
        # Throttling and 5xx are worth another attempt...
        (429, "failed_after_retries", 2),
        (500, "failed_after_retries", 2),
        # ...a rejected query is not: Firecrawl will refuse it again.
        (400, "validation_reject", 1),
        # ...and a dead key or empty wallet is a run-level problem, so it fails
        # the row immediately instead of burning the retry budget per query.
        (401, "failed_after_retries", 1),
        (402, "failed_after_retries", 1),
    ],
)
async def test_firecrawl_search_maps_sdk_errors_by_status(
    status: int, expected_status: str, expected_attempts: int
) -> None:
    """Pins the mapping through the SDK's own error handler, so a change to its
    exception types or messages fails here rather than in a live run."""
    import httpx

    from nimble_benchmark.samplers._rate_limit import reset_limiters

    reset_limiters()
    attempts: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(request)
        return httpx.Response(status, json={"error": "upstream said no"})

    sampler = FirecrawlSearchSampler(
        name="firecrawl_search",
        api_key="fc-key",
        retry_config=retry.RetryConfig(max_attempts=2, initial_wait=0.0, max_wait=0.0),
    )
    _install_firecrawl_mock_transport(sampler, handler)

    response = await sampler.sample("q")

    assert response.status == expected_status
    assert len(attempts) == expected_attempts
    assert response.chunks == []
    reset_limiters()

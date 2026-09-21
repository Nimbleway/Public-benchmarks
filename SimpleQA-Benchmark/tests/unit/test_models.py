import pytest

from nimble_benchmark.models import ProviderLatency, ProviderResponse, RetrievalChunk


@pytest.mark.unit
def test_provider_response_serializes_chunks():
    response = ProviderResponse(
        provider="nimble_search",
        query="what is paris",
        response_kind="search_results",
        chunks=[
            RetrievalChunk(
                url="https://example.com", title="Example", description="Paris", extra_snippets=[], position=0
            )
        ],
        api_answer=None,
        latency=ProviderLatency(request_response_time_ms=123.4, internal_response_time_ms=12.3),
        status="ok",
        raw={"results": []},
    )

    assert response.chunks_as_dicts() == [
        {
            "url": "https://example.com",
            "title": "Example",
            "description": "Paris",
            "extra_snippets": [],
            "position": 0,
        }
    ]

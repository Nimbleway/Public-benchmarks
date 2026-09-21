import os

import pytest

from nimble_benchmark.config import Settings
from nimble_benchmark.samplers.nimble_search import NimbleSearchSampler


@pytest.mark.integration
@pytest.mark.asyncio
async def test_nimble_search_real():
    if not os.getenv("NIMBLE_API_KEY"):
        pytest.skip("NIMBLE_API_KEY not set")
    settings = Settings()
    sampler = NimbleSearchSampler(
        name="nimble_search",
        base_url=settings.nimble_base_url,
        api_key=settings.nimble_api_key.get_secret_value(),
        include_answer=False,
    )

    raw = await sampler.get_search_results("What is the capital of France?")

    assert raw.get("results")

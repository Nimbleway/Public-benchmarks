"""Tests for the runner's rate-aware worker caps and failure aggregation."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from nimble_benchmark import runner
from nimble_benchmark.runner import effective_sampler_concurrency, run_benchmark


@pytest.mark.unit
def test_effective_sampler_concurrency_caps_rate_limited_lanes():
    # Brave free tier: 1 req/s. 40 workers would just camp on the limiter.
    brave = SimpleNamespace(name="brave_search", rate_per_second=1.0)
    assert effective_sampler_concurrency(brave, 40) == 10

    # A 2.5 req/s lane -> 25-worker ceiling.
    slow = SimpleNamespace(name="slow_search", rate_per_second=2.5)
    assert effective_sampler_concurrency(slow, 40) == 25


@pytest.mark.unit
def test_effective_sampler_concurrency_keeps_request_when_rate_allows():
    fast = SimpleNamespace(name="nimble_search", rate_per_second=9.0)
    assert effective_sampler_concurrency(fast, 40) == 40


@pytest.mark.unit
def test_effective_sampler_concurrency_ignores_unthrottled_samplers():
    unthrottled = SimpleNamespace(name="unthrottled_search")
    assert effective_sampler_concurrency(unthrottled, 40) == 40
    zero_rate = SimpleNamespace(name="weird", rate_per_second=0.0)
    assert effective_sampler_concurrency(zero_rate, 40) == 40


@pytest.mark.unit
@pytest.mark.asyncio
async def test_run_benchmark_lets_healthy_samplers_finish_when_one_fails(monkeypatch, tmp_path):
    """One sampler blowing up must not cancel its siblings mid-run: every
    lane finishes (writing whatever rows it could) before the combined
    failure is raised.
    """
    finished: list[str] = []

    async def fake_run_one_sampler(*, sampler, **kwargs):
        if sampler.name == "bad":
            raise RuntimeError("bad sampler exploded")
        finished.append(sampler.name)
        return Path(f"/tmp/{sampler.name}.csv")

    monkeypatch.setattr(runner, "run_one_sampler", fake_run_one_sampler)

    samplers = [SimpleNamespace(name="good_a"), SimpleNamespace(name="bad"), SimpleNamespace(name="good_b")]
    with pytest.raises(RuntimeError, match=r"1/3 sampler\(s\) failed: bad"):
        await run_benchmark(
            samplers=samplers,
            dataset_name="simpleqa",
            run_dir=tmp_path,
            synthesis_model="gpt-4o",
            judge_model="gpt-4o",
            judge_prompt_variant="passage",
            judge_relevance_threshold=2,
            limit=1,
            random_state=0,
            max_concurrent_tasks=2,
            sampler_configs={name: {} for name in ("good_a", "bad", "good_b")},
        )

    assert sorted(finished) == ["good_a", "good_b"]

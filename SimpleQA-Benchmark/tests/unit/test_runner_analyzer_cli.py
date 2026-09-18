import argparse
import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest
from pydantic import SecretStr

from nimble_benchmark.analyzer import aggregate_run
from nimble_benchmark.cli import _amain, build_parser
from nimble_benchmark.config import Settings
from nimble_benchmark.models import ProviderLatency, ProviderResponse, ProviderUsage, RetrievalChunk
from nimble_benchmark.report import write_run_md
from nimble_benchmark.runner import (
    _REQUEST_ERROR_MAX_CHARS,
    MAX_RUN_DIR_NAME_BYTES,
    _extract_request_error,
    make_run_dir,
    response_to_rows,
    synthesize_answer,
    write_sampler_config,
)


@pytest.fixture(autouse=True)
def _stub_eval_side_synthesis(monkeypatch):
    """Every graded answer is synthesized eval-side, so any test that reaches
    ``response_to_rows`` with a successful response would otherwise call
    OpenAI. Stub it with a deterministic string; the tests that care about the
    answerless path override this with their own stub."""

    async def fake_synth(query, formatted, model):
        return "synthesized answer"

    monkeypatch.setattr("nimble_benchmark.runner.synth", fake_synth)


@pytest.mark.unit
def test_make_run_dir_keeps_short_rosters_verbatim(tmp_path: Path) -> None:
    """The elision must not churn names that already fit -- an ``all_apis``
    run directory has to keep reading as its lane list."""
    run_dir = make_run_dir(
        results_root=str(tmp_path),
        dataset="simpleqa",
        samplers=["nimble_search", "exa_search_auto"],
        limit=10,
        timestamp=datetime(2026, 8, 9, 13, 2, 15),
    )

    assert run_dir.name == "run_20260809_130215_benchmark_simpleqa_nimble_search+exa_search_auto_n10"


@pytest.mark.unit
def test_make_run_dir_elides_a_roster_that_would_exceed_the_filesystem_limit(tmp_path: Path) -> None:
    """REGRESSION GUARD. APFS/ext4 cap one path component at 255 bytes, and the
    name embedded every lane verbatim, so adding the 15th lane produced a
    258-byte name and killed a full ``all_apis`` dispatch with
    ``OSError: [Errno 63] File name too long`` before it wrote a single row."""
    samplers = [f"sampler_number_{index:02d}" for index in range(40)]

    run_dir = make_run_dir(
        results_root=str(tmp_path),
        dataset="simpleqa",
        samplers=samplers,
        limit=500,
        timestamp=datetime(2026, 8, 9, 13, 2, 15),
    )

    assert len(run_dir.name.encode()) <= MAX_RUN_DIR_NAME_BYTES
    assert run_dir.is_dir()
    # The sortable timestamp prefix survives -- insights finds the previous run
    # by lexicographic sibling order, so it must stay ahead of any elision.
    assert run_dir.name.startswith("run_20260809_130215_benchmark_simpleqa_")
    assert run_dir.name.endswith("_n500")
    # Leading lanes stay readable; the tail is summarized by count.
    assert "sampler_number_00" in run_dir.name
    assert "more-" in run_dir.name


@pytest.mark.unit
def test_make_run_dir_gives_distinct_names_to_rosters_sharing_a_prefix(tmp_path: Path) -> None:
    """Two oversized rosters that agree on their leading lanes must not collapse
    onto one directory -- the second run would overwrite the first one's
    artifacts."""
    shared = [f"sampler_number_{index:02d}" for index in range(40)]
    timestamp = datetime(2026, 8, 9, 13, 2, 15)

    first = make_run_dir(
        results_root=str(tmp_path), dataset="simpleqa", samplers=shared, limit=500, timestamp=timestamp
    )
    second = make_run_dir(
        results_root=str(tmp_path),
        dataset="simpleqa",
        samplers=[*shared[:-1], "a_different_final_lane"],
        limit=500,
        timestamp=timestamp,
    )

    assert first.name != second.name


@pytest.mark.unit
@pytest.mark.parametrize(
    ("dataset", "limit"),
    [
        # ``--limit`` only validates ``> 0``, so a caller can supply an int whose
        # decimal form alone fills the whole name...
        ("simpleqa", 10**400),
        # ...and a dataset name is likewise unbounded.
        ("d" * 400, 10),
        # Both at once: nothing variable is left to trim.
        ("d" * 400, 10**400),
    ],
)
def test_make_run_dir_stays_within_the_limit_when_the_fixed_parts_alone_overflow(
    dataset: str, limit: int, tmp_path: Path
) -> None:
    """Trimming lanes cannot rescue a name whose non-roster parts already exceed
    the cap -- the roster budget goes negative and even the bare elision marker
    overflows. The finished name must still fit, or ``mkdir`` raises errno 63."""
    run_dir = make_run_dir(
        results_root=str(tmp_path),
        dataset=dataset,
        samplers=["nimble_search", "exa_search_auto"],
        limit=limit,
        timestamp=datetime(2026, 8, 9, 13, 2, 15),
    )

    assert len(run_dir.name.encode()) <= MAX_RUN_DIR_NAME_BYTES
    assert run_dir.is_dir()
    # The sortable prefix survives even in the degenerate form, so previous-run
    # ordering in ``insights`` keeps working.
    assert run_dir.name.startswith("run_20260809_130215_")


@pytest.mark.unit
def test_make_run_dir_digest_fallback_separates_runs_differing_only_past_the_cap(tmp_path: Path) -> None:
    """The fallback drops the dataset and limit from the visible name, so it has
    to fold them into the digest -- otherwise two runs sharing a timestamp would
    land in one directory and the second would overwrite the first's rows."""
    long_dataset = "d" * 400
    common = {
        "results_root": str(tmp_path),
        "samplers": ["nimble_search"],
        "timestamp": datetime(2026, 8, 9, 13, 2, 15),
    }

    first = make_run_dir(dataset=long_dataset, limit=10, **common)
    second = make_run_dir(dataset=long_dataset, limit=500, **common)
    third = make_run_dir(dataset=long_dataset + "x", limit=10, **common)

    assert len({first.name, second.name, third.name}) == 3


@pytest.mark.unit
@pytest.mark.asyncio
async def test_synthesize_answer_formats_chunks_for_the_synthesizer(monkeypatch):
    """The synthesizer only ever sees ``title``/``url``/``description`` -- a
    sampler that stashes text anywhere else is judged on passages the graded
    answer was never allowed to use."""
    captured = {}

    async def fake_synth(query, formatted, model):
        captured["query"] = query
        captured["formatted"] = formatted
        captured["model"] = model
        return "synthesized answer"

    monkeypatch.setattr("nimble_benchmark.runner.synth", fake_synth)

    response = ProviderResponse(
        provider="nimble_search",
        query="q",
        response_kind="search_results",
        chunks=[
            RetrievalChunk(
                url="https://a.example",
                title="A",
                description="first",
                extra_snippets=["ignored"],
                position=0,
            )
        ],
        api_answer=None,
        latency=ProviderLatency(100.0, None),
        status="ok",
        raw={},
    )

    assert await synthesize_answer(response=response, synthesis_model="gpt-4o") == "synthesized answer"
    assert captured["query"] == "q"
    assert captured["model"] == "gpt-4o"
    assert captured["formatted"] == ["[A](https://a.example)\n first"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_response_to_rows_grades_an_answer_lane_on_its_own_answer(monkeypatch):
    """An answer-kind lane is graded on the text it returned. Reaching for the
    synthesizer here would both burn an OpenAI call and measure the wrong
    thing -- the point of the Answer table is the model's own answer."""

    async def fake_judge_chunks(**kwargs):
        return [3]

    async def never_called_synth(query, formatted, model):
        raise AssertionError("an answer lane must not be synthesized for")

    monkeypatch.setattr("nimble_benchmark.runner.judge_chunks", fake_judge_chunks)
    monkeypatch.setattr("nimble_benchmark.runner.synth", never_called_synth)
    # No answer lane ships today, so the ``api`` branch is exercised through a
    # synthetic one rather than a registered name.
    monkeypatch.setattr("nimble_benchmark.runner.answer_source_for_provider", lambda provider: "api")

    class Dataset:
        def __init__(self):
            self.graded = None

        async def grader(self, query, ground_truth, predicted):
            self.graded = predicted
            return {"score_name": "is_correct"}

    dataset = Dataset()
    response = ProviderResponse(
        provider="example_answer_lane",
        query="q",
        response_kind="answer_with_citations",
        chunks=[RetrievalChunk("https://a.com", "A", "cited passage", [], 0)],
        api_answer="the model's own answer",
        latency=ProviderLatency(100.0, None),
        status="ok",
        raw={},
    )

    rows = await response_to_rows(
        response=response,
        dataset=dataset,
        ground_truth="gt",
        gold_urls=["https://a.com"],
        synthesis_model="gpt-4o",
        judge_model="gpt-4o",
        judge_prompt_variant="passage",
        judge_relevance_threshold=2,
    )

    assert len(rows) == 1
    assert rows[0]["response_kind"] == "answer_with_citations"
    assert rows[0]["answer_source_used"] == "api"
    assert rows[0]["generated_answer"] == "the model's own answer"
    assert dataset.graded == "the model's own answer"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_response_to_rows_marks_an_answer_lane_that_returned_nothing(monkeypatch):
    """An answer lane that 200s with empty text is ``is_not_attempted``, not a
    failure: the request succeeded, so it must not inflate ``failure_rate`` --
    it is in the accuracy denominator and scores as a miss instead."""

    async def fake_judge_chunks(**kwargs):
        return []

    monkeypatch.setattr("nimble_benchmark.runner.judge_chunks", fake_judge_chunks)
    monkeypatch.setattr("nimble_benchmark.runner.answer_source_for_provider", lambda provider: "api")

    rows = await response_to_rows(
        response=_empty_answer_response(provider="example_answer_lane", kind="answer_with_citations"),
        dataset=_NeverCalledGrader(),
        ground_truth="gt",
        gold_urls=[],
        synthesis_model="gpt-4o",
        judge_model="gpt-4o",
        judge_prompt_variant="passage",
        judge_relevance_threshold=2,
    )

    assert len(rows) == 1
    assert rows[0]["answer_source_used"] == "api"
    assert rows[0]["request_status"] == "ok"
    assert rows[0]["evaluation_result"] == "is_not_attempted"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_response_to_rows_copies_retrieval_metrics(monkeypatch):
    async def fake_judge_chunks(**kwargs):
        return [3]

    monkeypatch.setattr("nimble_benchmark.runner.judge_chunks", fake_judge_chunks)

    class Dataset:
        async def grader(self, query, ground_truth, predicted):
            return {"score_name": "is_correct"}

    response = ProviderResponse(
        provider="nimble_search",
        query="q",
        response_kind="search_results",
        chunks=[RetrievalChunk("https://a.com", "A", "relevant", [], 0)],
        api_answer=None,
        latency=ProviderLatency(100.0, 50.0),
        usage=ProviderUsage(input_tokens=10, output_tokens=20),
        status="ok",
        raw={},
    )

    rows = await response_to_rows(
        response=response,
        dataset=Dataset(),
        ground_truth="gt",
        gold_urls=["https://a.com"],
        synthesis_model="gpt-4o",
        judge_model="gpt-4o",
        judge_prompt_variant="passage",
        judge_relevance_threshold=2,
    )

    assert rows[0]["response_kind"] == "search_results"
    assert rows[0]["evaluation_result"] == "is_correct"
    assert rows[0]["usage_input_tokens"] == 10
    assert rows[0]["usage_output_tokens"] == 20
    assert rows[0]["ndcg_at_5"] == pytest.approx(1.0)
    assert rows[0]["recall_at_5_llm"] == 1.0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_response_to_rows_includes_answer_type_and_topic(monkeypatch):
    async def fake_judge_chunks(**kwargs):
        return []

    monkeypatch.setattr("nimble_benchmark.runner.judge_chunks", fake_judge_chunks)

    class Dataset:
        async def grader(self, query, ground_truth, predicted):
            return {"score_name": "is_correct"}

    response = ProviderResponse(
        provider="nimble_search",
        query="q",
        response_kind="search_results",
        chunks=[],
        api_answer=None,
        latency=ProviderLatency(100.0, 50.0),
        status="ok",
        raw={},
    )

    rows = await response_to_rows(
        response=response,
        dataset=Dataset(),
        ground_truth="gt",
        gold_urls=[],
        synthesis_model="gpt-4o",
        judge_model="gpt-4o",
        judge_prompt_variant="passage",
        judge_relevance_threshold=2,
        answer_type="Person",
        topic="Music",
    )

    assert rows[0]["answer_type"] == "Person"
    assert rows[0]["topic"] == "Music"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_response_to_rows_always_includes_answer_type_and_topic_keys(monkeypatch):
    async def fake_judge_chunks(**kwargs):
        return []

    monkeypatch.setattr("nimble_benchmark.runner.judge_chunks", fake_judge_chunks)

    class Dataset:
        async def grader(self, query, ground_truth, predicted):
            return {"score_name": "is_correct"}

    response = ProviderResponse(
        provider="nimble_search",
        query="q",
        response_kind="search_results",
        chunks=[],
        api_answer=None,
        latency=ProviderLatency(100.0, 50.0),
        status="ok",
        raw={},
    )

    rows = await response_to_rows(
        response=response,
        dataset=Dataset(),
        ground_truth="gt",
        gold_urls=[],
        synthesis_model="gpt-4o",
        judge_model="gpt-4o",
        judge_prompt_variant="passage",
        judge_relevance_threshold=2,
    )

    assert "answer_type" in rows[0]
    assert "topic" in rows[0]
    assert rows[0]["answer_type"] is None
    assert rows[0]["topic"] is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_response_to_rows_coerces_non_scalar_answer_type_and_topic_to_none(monkeypatch):
    async def fake_judge_chunks(**kwargs):
        return []

    monkeypatch.setattr("nimble_benchmark.runner.judge_chunks", fake_judge_chunks)

    class Dataset:
        async def grader(self, query, ground_truth, predicted):
            return {"score_name": "is_correct"}

    response = ProviderResponse(
        provider="nimble_search",
        query="q",
        response_kind="search_results",
        chunks=[],
        api_answer=None,
        latency=ProviderLatency(100.0, 50.0),
        status="ok",
        raw={},
    )

    rows = await response_to_rows(
        response=response,
        dataset=Dataset(),
        ground_truth="gt",
        gold_urls=[],
        synthesis_model="gpt-4o",
        judge_model="gpt-4o",
        judge_prompt_variant="passage",
        judge_relevance_threshold=2,
        answer_type=["Person", "Place"],
        topic={"name": "Music"},
    )

    assert rows[0]["answer_type"] is None
    assert rows[0]["topic"] is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_response_to_rows_preserves_citation_parse_error_status():
    class Dataset:
        async def grader(self, query, ground_truth, predicted):
            raise AssertionError("non-ok status rows should not be graded")

    response = ProviderResponse(
        provider="tavily_search_basic",
        query="q",
        response_kind="search_results",
        chunks=[],
        api_answer=None,
        latency=ProviderLatency(100.0, None),
        status="citation_parse_error",
        raw={"error": "bad citation"},
    )

    rows = await response_to_rows(
        response=response,
        dataset=Dataset(),
        ground_truth="gt",
        gold_urls=[],
        synthesis_model="gpt-4o",
        judge_model="gpt-4o",
        judge_prompt_variant="passage",
        judge_relevance_threshold=2,
        skip_llm_judge=True,
    )

    assert len(rows) == 1
    assert rows[0]["request_status"] == "citation_parse_error"
    assert rows[0]["answer_source_used"] == "synth"
    # The error string the sampler base stashed in ``raw`` must reach
    # the CSV so future failures aren't a diagnostic black hole.
    assert rows[0]["request_error"] == "bad citation"


@pytest.mark.unit
def test_extract_request_error_returns_empty_on_ok_status():
    """``ok`` rows must NOT carry an error string -- otherwise the CSV
    column reads as noise on the happy path and analysts can't grep
    failure-only rows cleanly."""
    assert _extract_request_error("ok", {"results": [{"url": "x"}]}) == ""


@pytest.mark.unit
def test_extract_request_error_returns_error_field_when_present():
    """On any non-ok status, surface the sampler-base ``error`` key
    verbatim -- that's what holds the upstream exception string captured
    in ``samplers/base.py``."""
    msg = "TransientHTTPError: 429 Too Many Requests"
    assert _extract_request_error("failed_after_retries", {"error": msg}) == msg


@pytest.mark.unit
def test_extract_request_error_truncates_overlong_messages():
    """Some upstreams return verbose HTML/JSON error bodies. A 50 KB
    error per row would blow up the CSV artifact when most rows fail
    (the 77 % failure case). 1 KB is enough for status code + snippet."""
    huge = {"error": "x" * (_REQUEST_ERROR_MAX_CHARS + 500)}
    out = _extract_request_error("failed_after_retries", huge)
    assert len(out) == _REQUEST_ERROR_MAX_CHARS


@pytest.mark.unit
def test_extract_request_error_falls_back_to_json_dump_when_no_error_key():
    """If a future sampler stores its failure in a different key, we
    still want SOMETHING in the column rather than a silent empty
    string that re-creates today's diagnostic black hole."""
    out = _extract_request_error("failed_after_retries", {"detail": "validation failed"})
    assert "validation failed" in out


@pytest.mark.unit
def test_extract_request_error_handles_missing_raw_safely():
    """A pathological sampler that returns ``raw=None`` must not crash
    the runner -- the column just stays empty."""
    assert _extract_request_error("failed_after_retries", None) == ""
    assert _extract_request_error("failed_after_retries", {}) == ""


@pytest.mark.unit
def test_write_sampler_config_emits_schema_version(tmp_path):
    """The recorded config stamps the artifact's schema version, so a reader
    (the report's configuration table, the pricing lookup) can tell which shape
    it is parsing."""
    write_sampler_config(tmp_path, "nimble_search", {"search_depth": "fast"})

    data = json.loads((Path(tmp_path) / "sampler_config_nimble_search.json").read_text())
    assert data["_schema_version"] == 1
    assert data["search_depth"] == "fast"


@pytest.mark.unit
def test_analyzer_aggregates_response_kind(tmp_path):
    pd.DataFrame(
        [
            {
                "provider": "nimble_search",
                "response_kind": "search_results",
                "answer_source_used": "synth",
                "evaluation_result": "is_correct",
                "request_status": "ok",
                "request_response_time_ms": 100.0,
                "internal_response_time_ms": 50.0,
                "ndcg_at_10": 1.0,
                "recall_at_10_llm": 1.0,
            }
        ]
    ).to_csv(tmp_path / "dataset_simpleqa_raw_results_nimble_search.csv", index=False)

    aggregate = aggregate_run(run_dir=tmp_path, dataset_name="simpleqa")

    assert aggregate.iloc[0]["provider"] == "nimble_search"
    assert aggregate.iloc[0]["response_kind"] == "search_results"
    assert aggregate.iloc[0]["accuracy_score"] == 1.0


@pytest.mark.unit
def test_analyzer_leaves_accuracy_blank_when_grader_skipped(tmp_path):
    pd.DataFrame(
        [
            {
                "provider": "nimble_search",
                "query": "q1",
                "response_kind": "search_results",
                "answer_source_used": "synth",
                "evaluation_result": "not_evaluated",
                "request_status": "ok",
                "request_response_time_ms": 100.0,
                "answer_type": "Person",
                "topic": "Music",
            },
            {
                "provider": "nimble_search",
                "query": "q2",
                "response_kind": "search_results",
                "answer_source_used": "synth",
                "evaluation_result": "not_evaluated",
                "request_status": "ok",
                "request_response_time_ms": 110.0,
                "answer_type": "Person",
                "topic": "Music",
            },
        ]
    ).to_csv(tmp_path / "dataset_simpleqa_raw_results_nimble_search.csv", index=False)

    aggregate = aggregate_run(run_dir=tmp_path, dataset_name="simpleqa")
    by_answer_type = pd.read_csv(tmp_path / "analyzed_by_answer_type.csv")
    by_topic = pd.read_csv(tmp_path / "analyzed_by_topic.csv")

    assert pd.isna(aggregate.iloc[0]["accuracy_score"])
    assert pd.isna(by_answer_type.iloc[0]["accuracy_score"])
    assert pd.isna(by_topic.iloc[0]["accuracy_score"])


@pytest.mark.unit
def test_accuracy_counts_not_attempted_as_a_miss(tmp_path):
    """``is_not_attempted`` is in the accuracy denominator and scores 0.

    The request reached the provider and came back 200, so a refusal or an
    empty payload is a question the lane was served and did not answer -- it
    costs accuracy exactly like a wrong answer. Only ``not_evaluated`` (the
    request never landed) leaves the denominator; ``failure_rate`` covers it.

    1 correct / 1 incorrect / 2 not-attempted / 1 not-evaluated -> 1/4 = 0.25.
    Under the old attempts-only denominator this was 1/2 = 0.5.
    """
    verdicts = [
        ("q1", "is_correct", "ok"),
        ("q2", "is_incorrect", "ok"),
        ("q3", "is_not_attempted", "ok"),
        ("q4", "is_not_attempted", "ok"),
        ("q5", "not_evaluated", "failed_after_retries"),
    ]
    pd.DataFrame(
        [
            {
                "provider": "nimble_search",
                "response_kind": "search_results",
                "answer_source_used": "synth",
                "query": query,
                "evaluation_result": verdict,
                "request_status": status,
                "request_response_time_ms": 100.0,
            }
            for query, verdict, status in verdicts
        ]
    ).to_csv(tmp_path / "dataset_simpleqa_raw_results_nimble_search.csv", index=False)

    aggregate = aggregate_run(run_dir=tmp_path, dataset_name="simpleqa")

    row = aggregate.iloc[0]
    assert row["accuracy_score"] == 0.25
    # The reliability axis is unchanged: only the dead request counts as a
    # failure, so a not-attempted row is visible in accuracy and nowhere else.
    assert row["problem_count"] == 5
    assert row["successful_problem_count"] == 4
    assert row["failure_count"] == 1


@pytest.mark.unit
def test_accuracy_is_none_when_every_row_is_not_evaluated(tmp_path):
    """A lane whose requests all died has no accuracy, not 0.0.

    ``not_evaluated`` stays out of the denominator, so an all-failed lane must
    render as absent rather than as a lane that answered everything wrong.
    """
    pd.DataFrame(
        [
            {
                "provider": "nimble_search",
                "response_kind": "search_results",
                "answer_source_used": "synth",
                "query": query,
                "evaluation_result": "not_evaluated",
                "request_status": "failed_after_retries",
                "request_response_time_ms": None,
            }
            for query in ("q1", "q2")
        ]
    ).to_csv(tmp_path / "dataset_simpleqa_raw_results_nimble_search.csv", index=False)

    aggregate = aggregate_run(run_dir=tmp_path, dataset_name="simpleqa")

    assert pd.isna(aggregate.iloc[0]["accuracy_score"])


@pytest.mark.unit
def test_aggregator_groups_by_answer_type(tmp_path):
    pd.DataFrame(
        [
            {
                "provider": "nimble_search",
                "response_kind": "search_results",
                "answer_source_used": "synth",
                "answer_type": "Person",
                "topic": "Music",
                "evaluation_result": "is_correct",
                "query": "q1",
                "request_status": "ok",
                "request_response_time_ms": 100.0,
            },
            {
                "provider": "nimble_search",
                "response_kind": "search_results",
                "answer_source_used": "synth",
                "answer_type": "Person",
                "topic": "Music",
                "evaluation_result": "is_incorrect",
                "query": "q2",
                "request_status": "ok",
                "request_response_time_ms": 110.0,
            },
            {
                "provider": "nimble_search",
                "response_kind": "search_results",
                "answer_source_used": "synth",
                "answer_type": "Place",
                "topic": "Geography",
                "evaluation_result": "is_correct",
                "query": "q3",
                "request_status": "ok",
                "request_response_time_ms": 120.0,
            },
        ]
    ).to_csv(tmp_path / "dataset_simpleqa_raw_results_nimble_search.csv", index=False)

    aggregate_run(run_dir=tmp_path, dataset_name="simpleqa")

    by_answer_type = pd.read_csv(tmp_path / "analyzed_by_answer_type.csv")
    assert set(by_answer_type["answer_type"]) == {"Person", "Place"}
    assert by_answer_type.loc[by_answer_type["answer_type"] == "Person", "problem_count"].iloc[0] == 2
    assert by_answer_type.loc[by_answer_type["answer_type"] == "Person", "accuracy_score"].iloc[0] == 0.5
    assert by_answer_type.loc[by_answer_type["answer_type"] == "Place", "accuracy_score"].iloc[0] == 1.0


@pytest.mark.unit
def test_aggregator_groups_by_topic(tmp_path):
    pd.DataFrame(
        [
            {
                "provider": "tavily_search_basic",
                "response_kind": "search_results",
                "answer_source_used": "synth",
                "answer_type": "Person",
                "topic": "Music",
                "evaluation_result": "is_correct",
                "query": "q1",
                "request_status": "ok",
                "request_response_time_ms": 100.0,
            },
            {
                "provider": "tavily_search_basic",
                "response_kind": "search_results",
                "answer_source_used": "synth",
                "answer_type": "Date",
                "topic": "Music",
                "evaluation_result": "is_incorrect",
                "query": "q2",
                "request_status": "ok",
                "request_response_time_ms": 110.0,
            },
            {
                "provider": "tavily_search_basic",
                "response_kind": "search_results",
                "answer_source_used": "synth",
                "answer_type": "Place",
                "topic": "Geography",
                "evaluation_result": "is_correct",
                "query": "q3",
                "request_status": "ok",
                "request_response_time_ms": 120.0,
            },
        ]
    ).to_csv(tmp_path / "dataset_simpleqa_raw_results_tavily_search_basic.csv", index=False)

    aggregate_run(run_dir=tmp_path, dataset_name="simpleqa")

    by_topic = pd.read_csv(tmp_path / "analyzed_by_topic.csv")
    assert set(by_topic["topic"]) == {"Music", "Geography"}
    assert by_topic.loc[by_topic["topic"] == "Music", "problem_count"].iloc[0] == 2
    assert by_topic.loc[by_topic["topic"] == "Music", "accuracy_score"].iloc[0] == 0.5
    assert by_topic.loc[by_topic["topic"] == "Geography", "accuracy_score"].iloc[0] == 1.0


@pytest.mark.unit
def test_aggregator_orders_topic_breakdown_by_count_desc_then_topic_asc(tmp_path):
    pd.DataFrame(
        [
            {
                "provider": "tavily_search_basic",
                "response_kind": "search_results",
                "answer_source_used": "synth",
                "topic": "Zoology",
                "evaluation_result": "is_correct",
                "query": "q1",
                "request_status": "ok",
                "request_response_time_ms": 100.0,
            },
            {
                "provider": "tavily_search_basic",
                "response_kind": "search_results",
                "answer_source_used": "synth",
                "topic": "Art",
                "evaluation_result": "is_correct",
                "query": "q2",
                "request_status": "ok",
                "request_response_time_ms": 100.0,
            },
            {
                "provider": "tavily_search_basic",
                "response_kind": "search_results",
                "answer_source_used": "synth",
                "topic": "Art",
                "evaluation_result": "is_incorrect",
                "query": "q3",
                "request_status": "ok",
                "request_response_time_ms": 100.0,
            },
            {
                "provider": "tavily_search_basic",
                "response_kind": "search_results",
                "answer_source_used": "synth",
                "topic": "Music",
                "evaluation_result": "is_correct",
                "query": "q4",
                "request_status": "ok",
                "request_response_time_ms": 100.0,
            },
            {
                "provider": "tavily_search_basic",
                "response_kind": "search_results",
                "answer_source_used": "synth",
                "topic": "Music",
                "evaluation_result": "is_incorrect",
                "query": "q5",
                "request_status": "ok",
                "request_response_time_ms": 100.0,
            },
        ]
    ).to_csv(tmp_path / "dataset_simpleqa_raw_results_tavily_search_basic.csv", index=False)

    aggregate_run(run_dir=tmp_path, dataset_name="simpleqa")

    by_topic = pd.read_csv(tmp_path / "analyzed_by_topic.csv")
    assert list(by_topic["topic"]) == ["Art", "Music", "Zoology"]


@pytest.mark.unit
def test_aggregator_suppresses_breakdown_csvs_when_metadata_absent(tmp_path):
    pd.DataFrame(
        [
            {
                "provider": "nimble_search",
                "response_kind": "search_results",
                "answer_source_used": "synth",
                "evaluation_result": "is_correct",
                "query": "q1",
                "request_status": "ok",
                "request_response_time_ms": 100.0,
            }
        ]
    ).to_csv(tmp_path / "dataset_simpleqa_raw_results_nimble_search.csv", index=False)

    aggregate_run(run_dir=tmp_path, dataset_name="simpleqa")

    assert not (tmp_path / "analyzed_by_answer_type.csv").exists()
    assert not (tmp_path / "analyzed_by_topic.csv").exists()


@pytest.mark.unit
def test_analyzer_groups_by_provider_and_lane_key(tmp_path):
    pd.DataFrame(
        [
            {
                "provider": "nimble_search",
                "response_kind": "search_results",
                "answer_source_used": "synth",
                "evaluation_result": "is_correct",
                "query": "q1",
                "request_status": "ok",
                "request_response_time_ms": 100.0,
            },
            {
                "provider": "nimble_search",
                "response_kind": "search_results",
                "answer_source_used": "synth",
                "evaluation_result": "is_incorrect",
                "query": "q2",
                "request_status": "ok",
                "request_response_time_ms": 110.0,
            },
        ]
    ).to_csv(tmp_path / "dataset_simpleqa_raw_results_nimble_search.csv", index=False)

    aggregate = aggregate_run(run_dir=tmp_path, dataset_name="simpleqa")

    # ``answer_source_used`` is the constant ``synth`` now, so the grouping
    # collapses to one row per (provider, response_kind) with both queries
    # rolled into its accuracy.
    assert set(aggregate["answer_source"]) == {"synth"}
    assert len(aggregate) == 1
    assert aggregate["accuracy_score"].iloc[0] == 0.5


@pytest.mark.unit
def test_analyzer_counts_citation_parse_error(tmp_path):
    pd.DataFrame(
        [
            {
                "provider": "tavily_search_basic",
                "response_kind": "search_results",
                "answer_source_used": "synth",
                "evaluation_result": "is_incorrect",
                "query": "q1",
                "request_status": "citation_parse_error",
                "request_response_time_ms": 100.0,
            },
            {
                "provider": "tavily_search_basic",
                "response_kind": "search_results",
                "answer_source_used": "synth",
                "evaluation_result": "is_correct",
                "query": "q2",
                "request_status": "ok",
                "request_response_time_ms": 110.0,
            },
        ]
    ).to_csv(tmp_path / "dataset_simpleqa_raw_results_tavily_search_basic.csv", index=False)

    aggregate = aggregate_run(run_dir=tmp_path, dataset_name="simpleqa")

    assert aggregate.iloc[0]["citation_parse_error_count"] == 1


@pytest.mark.unit
def test_analyzer_aggregates_usage_metrics_and_skips_all_none(tmp_path):
    pd.DataFrame(
        [
            {
                "provider": "exa_search_auto",
                "response_kind": "search_results",
                "answer_source_used": "synth",
                "evaluation_result": "is_correct",
                "query": "q1",
                "request_status": "ok",
                "request_response_time_ms": 100.0,
                "usage_input_tokens": 10,
                "usage_output_tokens": 20,
            },
            {
                "provider": "exa_search_auto",
                "response_kind": "search_results",
                "answer_source_used": "synth",
                "evaluation_result": "is_incorrect",
                "query": "q2",
                "request_status": "ok",
                "request_response_time_ms": 110.0,
                "usage_input_tokens": 30,
                "usage_output_tokens": 40,
            },
        ]
    ).to_csv(tmp_path / "dataset_simpleqa_raw_results_exa_search_auto.csv", index=False)
    pd.DataFrame(
        [
            {
                "provider": "tavily_search_basic",
                "response_kind": "search_results",
                "answer_source_used": "synth",
                "evaluation_result": "is_correct",
                "query": "q3",
                "request_status": "citation_parse_error",
                "request_response_time_ms": 120.0,
                "usage_input_tokens": None,
                "usage_output_tokens": None,
            }
        ]
    ).to_csv(tmp_path / "dataset_simpleqa_raw_results_tavily_search_basic.csv", index=False)

    aggregate = aggregate_run(run_dir=tmp_path, dataset_name="simpleqa")
    written = pd.read_csv(tmp_path / "analyzed_results.csv")

    exa = aggregate.loc[aggregate["provider"] == "exa_search_auto"].iloc[0]
    assert exa["usage_input_tokens_sum"] == 40
    assert exa["usage_input_tokens_mean"] == 20
    assert exa["usage_output_tokens_sum"] == 60
    assert exa["usage_output_tokens_mean"] == 30

    nimble = aggregate.loc[aggregate["provider"] == "tavily_search_basic"].iloc[0]
    assert pd.isna(nimble["usage_input_tokens_sum"])
    assert pd.isna(nimble["usage_input_tokens_mean"])
    assert pd.isna(nimble["usage_output_tokens_sum"])
    assert pd.isna(nimble["usage_output_tokens_mean"])

    columns = list(written.columns)
    assert columns.index("citation_parse_error_count") < columns.index("usage_input_tokens_sum")
    assert len(columns) == len(set(columns))


@pytest.mark.unit
def test_report_wrong_answer_samples_section(tmp_path):
    """run.md gets up to 5 graded-incorrect rows per (provider, answer_source).
    Refusals are excluded, pipes/newlines are sanitized so the MD table
    survives, and a run with no incorrect rows gets no section at all."""
    pd.DataFrame(
        [
            {
                "provider": "nimble_search",
                "response_kind": "search_results",
                "answer_source": "synth",
                "accuracy_score": 0.5,
                "ndcg_at_10": 0.5,
                "recall_at_10_llm": 0.5,
                "provider_response_time_ms_p50": 100.0,
                "provider_response_time_ms_p95": 150.0,
            }
        ]
    ).to_csv(tmp_path / "analyzed_results.csv", index=False)
    rows = [
        {
            "provider": "nimble_search",
            "answer_source_used": "synth",
            "evaluation_result": "is_incorrect",
            "query": f"question {i}?",
            "ground_truth": "1953",
            "generated_answer": "It | was\n1958",
        }
        for i in range(7)  # 7 incorrect -> only 5 sampled, count still says 7
    ]
    rows.append(
        {
            "provider": "nimble_search",
            "answer_source_used": "synth",
            "evaluation_result": "is_not_attempted",
            "query": "refused question?",
            "ground_truth": "42",
            "generated_answer": "I could not find this.",
        }
    )
    pd.DataFrame(rows).to_csv(tmp_path / "dataset_simpleqa_raw_results_nimble_search.csv", index=False)

    write_run_md(
        run_dir=tmp_path,
        args={"dataset": "simpleqa", "limit": 8, "answer_source": "synth"},
        started_at=datetime(2026, 1, 1, 0, 0, 0),
        finished_at=datetime(2026, 1, 1, 0, 1, 0),
    )
    report = (tmp_path / "run.md").read_text()

    assert "## Wrong-answer samples" in report
    assert "### nimble_search (synth) — 7 incorrect" in report
    assert report.count("| question ") == 5  # capped at 5 of the 7
    assert "refused question?" not in report  # refusals excluded
    assert "It \\| was 1958" in report  # pipe escaped, newline collapsed


@pytest.mark.unit
def test_report_omits_wrong_answer_section_when_all_correct(tmp_path):
    pd.DataFrame(
        [
            {
                "provider": "nimble_search",
                "answer_source_used": "synth",
                "evaluation_result": "is_correct",
                "query": "q?",
                "ground_truth": "a",
                "generated_answer": "a",
            }
        ]
    ).to_csv(tmp_path / "dataset_simpleqa_raw_results_nimble_search.csv", index=False)

    write_run_md(
        run_dir=tmp_path,
        args={"dataset": "simpleqa", "limit": 1, "answer_source": "synth"},
        started_at=datetime(2026, 1, 1, 0, 0, 0),
        finished_at=datetime(2026, 1, 1, 0, 1, 0),
    )
    assert "## Wrong-answer samples" not in (tmp_path / "run.md").read_text()


@pytest.mark.unit
def test_failures_json_classifies_root_causes(tmp_path):
    """failures.json must carry every failed row with a machine-assigned
    failure_kind + retrieval_diagnosis, full chunk context, and per-lane
    counts -- and exclude correct rows and skip-grader artifacts."""
    import json as jsonlib

    from nimble_benchmark.report import write_failures_json

    chunks_gold_top = jsonlib.dumps([{"position": 0, "url": "https://gold.example", "title": "t", "description": "d"}])
    chunks_junk = jsonlib.dumps(
        [{"position": 0, "url": "https://dictionary.com/browse/in", "title": "in", "description": "x" * 500}]
    )
    base = {
        "provider": "tavily_search_basic",
        "answer_source_used": "synth",
        "ground_truth": "1953",
        "request_status": "ok",
        "request_error": None,
        "llm_grades_json": "[3, 0]",
        "request_response_time_ms": 1200.5,
        "answer_type": "date",
        "topic": "Art",
        "recall_at_10": 1.0,
        "ndcg_at_10": 1.0,
    }
    rows = [
        # gold at rank 1, still wrong -> generation miss
        {
            **base,
            "query": "q-genmiss",
            "generated_answer": "1958",
            "evaluation_result": "is_incorrect",
            "chunks_json": chunks_gold_top,
            "mrr": 1.0,
            "hit_at_10": 1,
            "recall_at_10_llm": 1.0,
        },
        # gold deep in list -> ranking problem
        {
            **base,
            "query": "q-ranklow",
            "generated_answer": "1958",
            "evaluation_result": "is_incorrect",
            "chunks_json": chunks_gold_top,
            "mrr": 0.1,
            "hit_at_10": 1,
            "recall_at_10_llm": 1.0,
        },
        # gold absent, junk chunks, refused -> bad_retrieval refusal
        {
            **base,
            "query": "q-junk",
            "generated_answer": "I don't know",
            "evaluation_result": "is_not_attempted",
            "chunks_json": chunks_junk,
            "mrr": 0.0,
            "hit_at_10": 0,
            "recall_at_10_llm": 0.0,
        },
        # provider call died -> request_failure regardless of grade
        {
            **base,
            "query": "q-dead",
            "generated_answer": None,
            "evaluation_result": "not_evaluated",
            "request_status": "failed_after_retries",
            "request_error": '{"error": 429}',
            "chunks_json": "[]",
            "mrr": 0.0,
            "hit_at_10": 0,
            "recall_at_10_llm": 0.0,
        },
        # correct row -> excluded
        {
            **base,
            "query": "q-fine",
            "generated_answer": "1953",
            "evaluation_result": "is_correct",
            "chunks_json": chunks_gold_top,
            "mrr": 1.0,
            "hit_at_10": 1,
            "recall_at_10_llm": 1.0,
        },
        # skip-grader artifact -> excluded
        {
            **base,
            "query": "q-skipped",
            "generated_answer": "1953",
            "evaluation_result": "not_evaluated",
            "chunks_json": chunks_gold_top,
            "mrr": 1.0,
            "hit_at_10": 1,
            "recall_at_10_llm": 1.0,
        },
    ]
    pd.DataFrame(rows).to_csv(tmp_path / "dataset_simpleqa_raw_results_tavily_search_basic.csv", index=False)

    write_failures_json(run_dir=tmp_path)
    payload = jsonlib.loads((tmp_path / "failures.json").read_text())

    assert payload["total_failures"] == 4
    by_query = {f["query"]: f for f in payload["failures"]}
    assert by_query["q-genmiss"]["failure_kind"] == "wrong_answer"
    assert by_query["q-genmiss"]["retrieval_diagnosis"] == "gold_in_top2_generation_miss"
    assert by_query["q-ranklow"]["retrieval_diagnosis"] == "gold_found_ranked_low"
    assert by_query["q-junk"]["failure_kind"] == "refusal"
    assert by_query["q-junk"]["retrieval_diagnosis"] == "bad_retrieval"
    assert by_query["q-dead"]["failure_kind"] == "request_failure"
    assert by_query["q-dead"]["retrieval_diagnosis"] is None
    assert "q-fine" not in by_query and "q-skipped" not in by_query
    # chunk context survives, long descriptions truncated
    assert by_query["q-genmiss"]["chunks"][0]["url"] == "https://gold.example"
    assert len(by_query["q-junk"]["chunks"][0]["description"]) <= 400
    assert by_query["q-genmiss"]["llm_grades"] == [3, 0]
    lane = payload["counts_by_lane"]["tavily_search_basic/synth"]
    assert lane["wrong_answer:gold_in_top2_generation_miss"] == 1
    assert lane["request_failure"] == 1


@pytest.mark.unit
def test_report_surfaces_citation_parse_error_count(tmp_path):
    pd.DataFrame(
        [
            {
                "provider": "tavily_search_basic",
                "response_kind": "search_results",
                "answer_source": "synth",
                "accuracy_score": 0.0,
                "ndcg_at_10": 0.0,
                "recall_at_10_llm": 0.0,
                "provider_response_time_ms_p50": 100.0,
                "provider_response_time_ms_p95": 150.0,
                "citation_parse_error_count": 1,
            }
        ]
    ).to_csv(tmp_path / "analyzed_results.csv", index=False)

    write_run_md(
        run_dir=tmp_path,
        args={"dataset": "simpleqa", "limit": 1, "answer_source": "synth"},
        started_at=datetime(2026, 1, 1, 0, 0, 0),
        finished_at=datetime(2026, 1, 1, 0, 1, 0),
    )

    report = (tmp_path / "run.md").read_text()
    # The header now carries an ``n`` column (successful/total breakdown
    # from the reliability gate). The legacy fixture used here doesn't
    # populate ``problem_count``/``successful_problem_count`` so the
    # column renders as ``—``.
    assert "| provider | n | accuracy | parse errors | p50 ms | p95 ms |" in report
    assert "| tavily_search_basic | — | 0.0% | 1 | 100 | 150 |" in report


@pytest.mark.unit
def test_run_md_includes_latency_by_stage_section(tmp_path):
    """`run.md` carries a `## Latency by stage` section attributing wall-clock
    time to the discrete pipeline stages parsed from each sampler's
    `Server-Timing` response header. Nimble lanes populate per-stage cells;
    third-party APIs that don't emit Server-Timing render `—`."""
    pd.DataFrame(
        [
            {
                "provider": "nimble_search_deep",
                "response_kind": "search_results",
                "answer_source": "synth",
                "problem_count": 500,
                "accuracy_score": 0.91,
                "ndcg_at_10": 0.43,
                "recall_at_10_llm": 0.83,
                "provider_response_time_ms_p50": 12_973.0,
                "provider_response_time_ms_p95": 18_703.0,
                # Deliberately far above the round trip: the harness wall clock
                # carries the limiter queue wait, and the stage table renders
                # both so the gap is visible rather than conflated.
                "request_response_time_ms_p50": 17_500.0,
                "internal_response_time_ms_p50": 12_400.0,
                "stage_plan_ms_mean": 1_600.0,
                "stage_plan_ms_p50": 1_500.0,
                "stage_plan_ms_p95": 2_200.0,
                "stage_search_ms_mean": 5_000.0,
                "stage_search_ms_p50": 4_800.0,
                "stage_search_ms_p95": 8_000.0,
                "stage_synthesis_ms_mean": 6_300.0,
                "stage_synthesis_ms_p50": 6_000.0,
                "stage_synthesis_ms_p95": 9_500.0,
            },
            {
                "provider": "exa_search_auto",
                "response_kind": "search_results",
                "answer_source": "synth",
                "problem_count": 500,
                "accuracy_score": 0.65,
                "ndcg_at_10": 0.30,
                "recall_at_10_llm": 0.55,
                "provider_response_time_ms_p50": 4_500.0,
                "provider_response_time_ms_p95": 7_400.0,
                "request_response_time_ms_p50": 9_000.0,
                "internal_response_time_ms_p50": None,
                "stage_plan_ms_mean": None,
                "stage_plan_ms_p50": None,
                "stage_plan_ms_p95": None,
                "stage_search_ms_mean": None,
                "stage_search_ms_p50": None,
                "stage_search_ms_p95": None,
                "stage_synthesis_ms_mean": None,
                "stage_synthesis_ms_p50": None,
                "stage_synthesis_ms_p95": None,
            },
        ]
    ).to_csv(tmp_path / "analyzed_results.csv", index=False)

    write_run_md(
        run_dir=tmp_path,
        args={"dataset": "simpleqa", "limit": 500, "answer_source": "synth"},
        started_at=datetime(2026, 1, 1, 0, 0, 0),
        finished_at=datetime(2026, 1, 1, 0, 1, 0),
    )

    report = (tmp_path / "run.md").read_text()
    assert "## Latency by stage (ms)" in report
    expected_header = (
        "| provider | answer_source | "
        "plan mean | plan p50 | plan p95 | "
        "search mean | search p50 | search p95 | "
        "synthesis mean | synthesis p50 | synthesis p95 | "
        "server total p50 | provider p50 | client total p50 | n |"
    )
    assert expected_header in report
    # Sorted ascending by synthesis p50: exa (no synthesis data, NaN -> last)
    # comes after nimble_search_deep (6000ms).
    nimble_idx = report.index("| nimble_search_deep | synth |")
    exa_idx = report.index("| exa_search_auto | synth |")
    assert nimble_idx < exa_idx
    assert (
        "| nimble_search_deep | synth | 1600 | 1500 | 2200 | 5000 | 4800 | 8000 | "
        "6300 | 6000 | 9500 | 12400 | 12973 | 17500 | 500 |"
    ) in report
    assert ("| exa_search_auto | synth | — | — | — | — | — | — | — | — | — | — | 4500 | 9000 | 500 |") in report


@pytest.mark.unit
def test_run_md_never_renders_the_accuracy_breakdown_tables(tmp_path):
    """run.md never renders the by-answer-type or by-topic tables. The analyzer
    still writes their CSVs, so their presence must not bring the sections
    back."""
    pd.DataFrame(
        [
            {
                "provider": "nimble_search",
                "response_kind": "search_results",
                "answer_source": "synth",
                "accuracy_score": 0.75,
                "ndcg_at_10": 0.5,
                "recall_at_10_llm": 0.5,
                "provider_response_time_ms_p50": 100.0,
                "provider_response_time_ms_p95": 150.0,
            }
        ]
    ).to_csv(tmp_path / "analyzed_results.csv", index=False)
    pd.DataFrame(
        [
            {"provider": "nimble_search", "answer_type": "Person", "problem_count": 2, "accuracy_score": 0.5},
            {"provider": "nimble_search", "answer_type": "Place", "problem_count": 1, "accuracy_score": 1.0},
        ]
    ).to_csv(tmp_path / "analyzed_by_answer_type.csv", index=False)
    pd.DataFrame(
        [
            {"provider": "nimble_search", "topic": "Music", "problem_count": 3, "accuracy_score": 0.667},
            {"provider": "nimble_search", "topic": "Geography", "problem_count": 1, "accuracy_score": 1.0},
        ]
    ).to_csv(tmp_path / "analyzed_by_topic.csv", index=False)

    write_run_md(
        run_dir=tmp_path,
        args={"dataset": "simpleqa", "limit": 4, "answer_source": "synth"},
        started_at=datetime(2026, 1, 1, 0, 0, 0),
        finished_at=datetime(2026, 1, 1, 0, 1, 0),
    )

    report = (tmp_path / "run.md").read_text()
    assert "## Accuracy by answer type" not in report
    assert "## Accuracy by topic" not in report
    assert "| provider | Person | Place |" not in report
    assert "| provider | Music | Geography |" not in report


@pytest.mark.unit
def test_run_md_marks_baseline_row_with_circle_and_aligned_footnote(tmp_path):
    """When `significance.csv` is present, the matching headline row gets ◯ next
    to the provider name and the footnote legend names both markers."""
    pd.DataFrame(
        [
            {
                "provider": "tavily_search_basic",
                "response_kind": "search_results",
                "answer_source": "synth",
                "accuracy_score": 0.80,
                "ndcg_at_10": 0.65,
                "recall_at_10_llm": 0.72,
                "provider_response_time_ms_p50": 700.0,
                "provider_response_time_ms_p95": 1500.0,
            },
            {
                "provider": "parallel_search_basic",
                "response_kind": "search_results",
                "answer_source": "synth",
                "accuracy_score": 0.84,
                "ndcg_at_10": 0.78,
                "recall_at_10_llm": 0.85,
                "provider_response_time_ms_p50": 900.0,
                "provider_response_time_ms_p95": 1700.0,
            },
        ]
    ).to_csv(tmp_path / "analyzed_results.csv", index=False)
    pd.DataFrame(
        [
            {
                "metric": "ndcg_at_10",
                "baseline": "tavily_search_basic/search_results/synth",
                "system": "parallel_search_basic/search_results/synth",
                "baseline_mean": 0.65,
                "system_mean": 0.78,
                "mean_delta": 0.13,
                "p_value": 1e-6,
                "p_bonferroni": 3e-6,
                "significant": True,
                "n_queries": 25,
                "alpha": 0.05,
            },
        ]
    ).to_csv(tmp_path / "significance.csv", index=False)

    write_run_md(
        run_dir=tmp_path,
        args={"dataset": "simpleqa", "limit": 2, "answer_source": "synth"},
        started_at=datetime(2026, 1, 1, 0, 0, 0),
        finished_at=datetime(2026, 1, 1, 0, 1, 0),
    )

    report = (tmp_path / "run.md").read_text()
    # Baseline row carries the ◯ marker right after the provider name.
    assert "| tavily_search_basic ◯ |" in report
    # Non-baseline rows stay unmarked.
    assert "| parallel_search_basic |" in report
    # The NDCG@10 column is gone, so its significance row has nothing to star.
    assert "0.780" not in report
    # Footnote names both markers and the baseline key.
    assert "◯ significance baseline: `tavily_search_basic/search_results/synth`" in report
    assert "★ significantly better than the baseline" in report
    assert "Bonferroni-corrected p < 0.05" in report


@pytest.mark.unit
def test_run_md_marks_accuracy_score_significant(tmp_path):
    """The Markdown headline table stars accuracy when the significance CSV
    emits a ``metric=accuracy_score`` row that beats the baseline.

    This pins the Markdown ``run.md`` path specifically. The HTML/MDX
    renderer implements the same contract in separate code with its own
    templates, so a change there cannot be assumed to hold here.
    """
    pd.DataFrame(
        [
            {
                "provider": "tavily_search_basic",
                "response_kind": "search_results",
                "answer_source": "synth",
                "accuracy_score": 0.72,
                "ndcg_at_10": 0.65,
                "recall_at_10_llm": 0.72,
                "provider_response_time_ms_p50": 700.0,
                "provider_response_time_ms_p95": 1500.0,
            },
            {
                "provider": "parallel_search_basic",
                "response_kind": "search_results",
                "answer_source": "synth",
                "accuracy_score": 0.84,
                "ndcg_at_10": 0.65,
                "recall_at_10_llm": 0.72,
                "provider_response_time_ms_p50": 900.0,
                "provider_response_time_ms_p95": 1700.0,
            },
        ]
    ).to_csv(tmp_path / "analyzed_results.csv", index=False)
    pd.DataFrame(
        [
            {
                "metric": "accuracy_score",
                "baseline": "tavily_search_basic/search_results/synth",
                "system": "parallel_search_basic/search_results/synth",
                "baseline_mean": 0.72,
                "system_mean": 0.84,
                "mean_delta": 0.12,
                "p_value": 0.001,
                "p_bonferroni": 0.003,
                "significant": True,
                "n_queries": 25,
                "alpha": 0.05,
            },
        ]
    ).to_csv(tmp_path / "significance.csv", index=False)

    write_run_md(
        run_dir=tmp_path,
        args={"dataset": "simpleqa", "limit": 2, "answer_source": "synth"},
        started_at=datetime(2026, 1, 1, 0, 0, 0),
        finished_at=datetime(2026, 1, 1, 0, 1, 0),
    )

    report = (tmp_path / "run.md").read_text()
    # The challenger's accuracy cell carries ★ adjacent to the percent (no space),
    # matching the existing NDCG/Recall marker placement in this template.
    assert "84.0%★" in report
    # Nimble (the baseline) never gets a star against itself.
    assert "72.0%★" not in report


@pytest.mark.unit
def test_run_md_omits_significance_sections(tmp_path):
    """`run.md` no longer renders the ``## Statistical significance`` and
    ``## Per-provider significance`` H2 sections. The headline ★/◯ markers
    and the inline baseline footnote remain (covered by the dedicated tests
    above) -- only the deeper per-metric and per-provider breakdown
    sections are gone."""
    pd.DataFrame(
        [
            {
                "provider": "tavily_search_basic",
                "response_kind": "search_results",
                "answer_source": "synth",
                "accuracy_score": 0.80,
                "ndcg_at_10": 0.65,
                "recall_at_10_llm": 0.72,
                "provider_response_time_ms_p50": 700.0,
                "provider_response_time_ms_p95": 1500.0,
            },
            {
                "provider": "parallel_search_basic",
                "response_kind": "search_results",
                "answer_source": "synth",
                "accuracy_score": 0.84,
                "ndcg_at_10": 0.78,
                "recall_at_10_llm": 0.85,
                "provider_response_time_ms_p50": 900.0,
                "provider_response_time_ms_p95": 1700.0,
            },
        ]
    ).to_csv(tmp_path / "analyzed_results.csv", index=False)
    pd.DataFrame(
        [
            {
                "metric": "ndcg_at_10",
                "baseline": "tavily_search_basic/search_results/synth",
                "system": "parallel_search_basic/search_results/synth",
                "baseline_mean": 0.65,
                "system_mean": 0.78,
                "mean_delta": 0.13,
                "p_value": 1e-6,
                "p_bonferroni": 3e-6,
                "significant": True,
                "n_queries": 25,
                "alpha": 0.05,
            },
            {
                "metric": "accuracy_score",
                "baseline": "tavily_search_basic/search_results/synth",
                "system": "parallel_search_basic/search_results/synth",
                "baseline_mean": 0.80,
                "system_mean": 0.84,
                "mean_delta": 0.04,
                "p_value": 0.0008,
                "p_bonferroni": 0.0024,
                "significant": True,
                "n_queries": 25,
                "alpha": 0.05,
            },
        ]
    ).to_csv(tmp_path / "significance.csv", index=False)

    write_run_md(
        run_dir=tmp_path,
        args={"dataset": "simpleqa", "limit": 2, "answer_source": "synth"},
        started_at=datetime(2026, 1, 1, 0, 0, 0),
        finished_at=datetime(2026, 1, 1, 0, 1, 0),
    )

    report = (tmp_path / "run.md").read_text()
    assert "## Statistical significance" not in report
    assert "## Per-provider significance" not in report
    # The headline-table ★/◯ markers and footnote still render so a reader
    # can still see which lane is the baseline and which beat it.
    assert "◯ significance baseline: `tavily_search_basic/search_results/synth`" in report
    assert "84.0%★" in report


@pytest.mark.unit
def test_run_md_omits_circle_when_significance_csv_absent(tmp_path):
    """Backward compat: ◯ and the new footnote only appear when significance.csv exists."""
    pd.DataFrame(
        [
            {
                "provider": "tavily_search_basic",
                "response_kind": "search_results",
                "answer_source": "synth",
                "accuracy_score": 0.80,
                "ndcg_at_10": 0.65,
                "recall_at_10_llm": 0.72,
                "provider_response_time_ms_p50": 700.0,
                "provider_response_time_ms_p95": 1500.0,
            }
        ]
    ).to_csv(tmp_path / "analyzed_results.csv", index=False)

    write_run_md(
        run_dir=tmp_path,
        args={"dataset": "simpleqa", "limit": 1, "answer_source": "synth"},
        started_at=datetime(2026, 1, 1, 0, 0, 0),
        finished_at=datetime(2026, 1, 1, 0, 1, 0),
    )

    report = (tmp_path / "run.md").read_text()
    assert "◯" not in report
    assert "significance baseline" not in report


@pytest.mark.unit
def test_run_md_suppresses_breakdown_tables_when_metadata_absent(tmp_path):
    pd.DataFrame(
        [
            {
                "provider": "nimble_search",
                "response_kind": "search_results",
                "answer_source": "synth",
                "accuracy_score": 1.0,
                "ndcg_at_10": 1.0,
                "recall_at_10_llm": 1.0,
                "provider_response_time_ms_p50": 100.0,
                "provider_response_time_ms_p95": 150.0,
            }
        ]
    ).to_csv(tmp_path / "analyzed_results.csv", index=False)

    write_run_md(
        run_dir=tmp_path,
        args={"dataset": "simpleqa", "limit": 1, "answer_source": "synth"},
        started_at=datetime(2026, 1, 1, 0, 0, 0),
        finished_at=datetime(2026, 1, 1, 0, 1, 0),
    )

    report = (tmp_path / "run.md").read_text()
    assert "## Accuracy by answer type" not in report
    assert "## Accuracy by topic" not in report


@pytest.mark.unit
def test_cli_has_no_mode_flag():
    parser = build_parser()
    args = parser.parse_args(["--dataset", "simpleqa", "--limit", "1"])

    assert isinstance(args, argparse.Namespace)
    assert not hasattr(args, "mode")


@pytest.mark.unit
def test_cli_defaults_to_the_prod_search_lane():
    parser = build_parser()
    args = parser.parse_args([])

    assert args.samplers == ["nimble_search"]
    # The answer-source axis is gone: every lane is graded off eval-side synthesis.
    assert not hasattr(args, "answer_source")


@pytest.mark.unit
def test_cli_accepts_all_apis_alias():
    parser = build_parser()
    args = parser.parse_args(["--samplers", "all_apis"])

    assert args.samplers == ["all_apis"]
    from nimble_benchmark.samplers import expand_sampler_names

    assert expand_sampler_names(args.samplers) == [
        "nimble_search",
        "exa_search_auto",
        "exa_search_fast",
        "parallel_search_basic",
        "parallel_search_turbo",
        "tavily_search_basic",
        "tavily_search_fast",
        "brave_search",
        "firecrawl_search",
    ]


@pytest.mark.unit
def test_cli_rejects_removed_answer_aliases():
    """A stale dispatch naming a deleted alias must fail at argument parsing,
    not silently after the operator has committed to a run."""
    parser = build_parser()

    for removed in ("remote_answer_apis", "all_answer_apis", "nimble_apis"):
        with pytest.raises(SystemExit):
            parser.parse_args(["--samplers", removed])


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cli_dry_run_exits_zero_with_all_credentials(monkeypatch, capsys):
    calls = {"preflight": 0, "build_samplers": 0, "run_benchmark": 0, "make_run_dir": 0}

    async def fake_preflight_or_die(**kwargs):
        calls["preflight"] += 1

    def fake_build_samplers(**kwargs):
        calls["build_samplers"] += 1
        return []

    async def fake_run_benchmark(**kwargs):
        calls["run_benchmark"] += 1

    def fake_make_run_dir(**kwargs):
        calls["make_run_dir"] += 1
        return Path("should-not-be-created")

    _set_competition_api_env(monkeypatch)
    monkeypatch.setattr("nimble_benchmark.cli.preflight_or_die", fake_preflight_or_die)
    monkeypatch.setattr("nimble_benchmark.cli.build_samplers", fake_build_samplers)
    monkeypatch.setattr("nimble_benchmark.cli.run_benchmark", fake_run_benchmark)
    monkeypatch.setattr("nimble_benchmark.cli.make_run_dir", fake_make_run_dir)

    args = build_parser().parse_args(["--samplers", "all_apis", "--dry-run"])

    await _amain(args, Settings(_env_file=None))

    assert calls == {"preflight": 0, "build_samplers": 0, "run_benchmark": 0, "make_run_dir": 0}
    assert "credential check OK" in capsys.readouterr().out


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cli_dry_run_all_apis_requires_nimble_api_key(monkeypatch):
    """``all_apis`` includes ``nimble_search`` (prod), so missing
    ``NIMBLE_API_KEY`` must fail credential preflight."""
    _set_competition_api_env(monkeypatch)
    monkeypatch.delenv("NIMBLE_API_KEY")

    args = build_parser().parse_args(["--samplers", "all_apis", "--dry-run"])

    with pytest.raises(RuntimeError) as exc_info:
        await _amain(args, Settings(_env_file=None))

    message = str(exc_info.value)
    assert "nimble_search: NIMBLE_API_KEY" in message


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cli_dry_run_exits_nonzero_with_missing_credentials(monkeypatch):
    _set_competition_api_env(monkeypatch)
    monkeypatch.delenv("EXA_API_KEY")

    args = build_parser().parse_args(["--samplers", "all_apis", "--dry-run"])

    with pytest.raises(RuntimeError) as exc_info:
        await _amain(args, Settings(_env_file=None))

    message = str(exc_info.value)
    assert "Missing credentials for selected samplers" in message
    assert "exa_search_auto: EXA_API_KEY" in message


@pytest.mark.unit
def test_cli_rejects_the_removed_answer_source_flag():
    """Hard cut: a stale command line carrying ``--answer-source`` must fail
    at parse time rather than being silently ignored."""
    with pytest.raises(SystemExit):
        build_parser().parse_args(["--samplers", "nimble_search", "--answer-source", "synth"])


@pytest.mark.unit
def test_cli_rejects_unknown_sampler(capsys):
    parser = build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["--samplers", "nimble_seach"])
    assert "Unknown sampler: nimble_seach" in capsys.readouterr().err


@pytest.mark.unit
def test_cli_rejects_non_positive_limit():
    parser = build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["--limit", "0"])


@pytest.mark.unit
def test_cli_rejects_non_positive_concurrency():
    parser = build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["--max-concurrent-tasks", "0"])


@pytest.mark.unit
def test_sampler_config_written_as_json(tmp_path):
    write_sampler_config(tmp_path, "nimble_search", {"limit": 1})
    data = json.loads((Path(tmp_path) / "sampler_config_nimble_search.json").read_text())

    assert data["limit"] == 1
    assert data["_schema_version"] == 1


@pytest.mark.unit
def test_cli_sampler_help_lists_aliases_and_samplers():
    help_text = build_parser().format_help()

    assert "all_apis" in help_text
    assert "exa_search_auto" in help_text
    assert "nimble_search" in help_text


@pytest.mark.unit
@pytest.mark.asyncio
async def test_amain_expands_sampler_alias_before_build_and_preflight(monkeypatch, tmp_path):
    calls = {}

    def fake_build_samplers(*, settings, sampler_names, **kwargs):
        calls["build_sampler_names"] = sampler_names
        calls["build_kwargs"] = kwargs
        return [SimpleNamespace(name=name) for name in sampler_names]

    async def fake_preflight_or_die(*, base_url, api_key, sampler_names, settings):
        calls["preflight_sampler_names"] = sampler_names
        calls["preflight_settings"] = settings

    async def fake_run_benchmark(**kwargs):
        calls["run_sampler_names"] = [sampler.name for sampler in kwargs["samplers"]]
        return []

    monkeypatch.setattr("nimble_benchmark.cli.build_samplers", fake_build_samplers)
    monkeypatch.setattr("nimble_benchmark.cli.preflight_or_die", fake_preflight_or_die)
    monkeypatch.setattr("nimble_benchmark.cli.run_benchmark", fake_run_benchmark)
    monkeypatch.setattr("nimble_benchmark.cli.aggregate_run", lambda **kwargs: None)
    monkeypatch.setattr("nimble_benchmark.cli.write_run_md", lambda **kwargs: None)
    monkeypatch.setattr("nimble_benchmark.cli.write_run_json", lambda **kwargs: None)
    monkeypatch.setattr("nimble_benchmark.cli.make_run_dir", lambda **kwargs: tmp_path)

    args = build_parser().parse_args(["--samplers", "all_apis"])
    settings = Settings(
        nimble_api_key="nimble-key",
        exa_api_key="exa-key",
        parallel_api_key="parallel-key",
        firecrawl_api_key="firecrawl-key",
        brave_search_api_key="brave-key",
        tavily_api_key="tavily-key",
        openai_api_key="openai-key",
    )

    await _amain(args, settings)

    expanded = [
        "nimble_search",
        "exa_search_auto",
        "exa_search_fast",
        "parallel_search_basic",
        "parallel_search_turbo",
        "tavily_search_basic",
        "tavily_search_fast",
        "brave_search",
        "firecrawl_search",
    ]
    assert calls["build_sampler_names"] == expanded
    assert calls["preflight_sampler_names"] == expanded
    assert calls["preflight_settings"] is settings
    assert calls["run_sampler_names"] == expanded


@pytest.mark.unit
@pytest.mark.asyncio
async def test_amain_run_dir_override_uses_given_run_dir_instead_of_minting_new(monkeypatch, tmp_path):
    """``--run-dir`` writes into the given directory verbatim: it must not call
    ``make_run_dir``, and must pass that same path into ``run_benchmark`` so
    every artifact lands where the operator asked for it.
    """
    existing_run_dir = tmp_path / "named_run"
    existing_run_dir.mkdir()
    seen: dict[str, object] = {}

    def fake_make_run_dir(**kwargs):
        raise AssertionError("make_run_dir must not run when --run-dir is supplied")

    def fake_build_samplers(*, settings, sampler_names, **kwargs):
        return [SimpleNamespace(name=name) for name in sampler_names]

    async def fake_preflight_or_die(**kwargs):
        return []

    async def fake_run_benchmark(**kwargs):
        seen["run_dir"] = kwargs["run_dir"]

    monkeypatch.setattr("nimble_benchmark.cli.make_run_dir", fake_make_run_dir)
    monkeypatch.setattr("nimble_benchmark.cli.build_samplers", fake_build_samplers)
    monkeypatch.setattr("nimble_benchmark.cli.preflight_or_die", fake_preflight_or_die)
    monkeypatch.setattr("nimble_benchmark.cli.run_benchmark", fake_run_benchmark)
    monkeypatch.setattr("nimble_benchmark.cli.aggregate_run", lambda **kwargs: None)
    monkeypatch.setattr("nimble_benchmark.cli.write_run_md", lambda **kwargs: None)
    monkeypatch.setattr("nimble_benchmark.cli.write_run_json", lambda **kwargs: None)

    args = build_parser().parse_args(
        [
            "--samplers",
            "nimble_search",
            "--run-dir",
            str(existing_run_dir),
        ]
    )
    settings = Settings(nimble_api_key="nimble-key", openai_api_key="openai-key", _env_file=None)

    await _amain(args, settings)

    assert seen["run_dir"] == existing_run_dir


@pytest.mark.unit
@pytest.mark.asyncio
async def test_amain_run_dir_creates_missing_dir(monkeypatch, tmp_path):
    """``--run-dir`` accepts a not-yet-existing path so the operator can
    name a fresh run target deterministically (handy for scripted reruns).
    """
    target = tmp_path / "deep" / "nested" / "run"
    seen: dict[str, object] = {}

    def fake_build_samplers(*, settings, sampler_names, **kwargs):
        return [SimpleNamespace(name=name) for name in sampler_names]

    async def fake_preflight_or_die(**kwargs):
        return []

    async def fake_run_benchmark(**kwargs):
        seen["run_dir"] = kwargs["run_dir"]

    monkeypatch.setattr("nimble_benchmark.cli.build_samplers", fake_build_samplers)
    monkeypatch.setattr("nimble_benchmark.cli.preflight_or_die", fake_preflight_or_die)
    monkeypatch.setattr("nimble_benchmark.cli.run_benchmark", fake_run_benchmark)
    monkeypatch.setattr("nimble_benchmark.cli.aggregate_run", lambda **kwargs: None)
    monkeypatch.setattr("nimble_benchmark.cli.write_run_md", lambda **kwargs: None)
    monkeypatch.setattr("nimble_benchmark.cli.write_run_json", lambda **kwargs: None)

    args = build_parser().parse_args(
        [
            "--samplers",
            "nimble_search",
            "--run-dir",
            str(target),
        ]
    )
    settings = Settings(nimble_api_key="nimble-key", openai_api_key="openai-key", _env_file=None)

    await _amain(args, settings)

    assert seen["run_dir"] == target
    assert target.exists() and target.is_dir()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_amain_preflight_reports_aggregated_missing_alias_credentials_before_build(monkeypatch):
    def fake_build_samplers(**kwargs):
        raise AssertionError("build_samplers should not run before credential preflight")

    monkeypatch.setattr("nimble_benchmark.cli.build_samplers", fake_build_samplers)

    args = build_parser().parse_args(["--samplers", "all_apis"])
    settings = Settings(nimble_api_key="nimble-key", openai_api_key="openai-key", _env_file=None)

    with pytest.raises(RuntimeError) as exc_info:
        await _amain(args, settings)

    message = str(exc_info.value)
    # Every third-party lane in the published set reports its own missing key,
    # aggregated into one message rather than failing on the first one.
    assert "exa_search_auto: EXA_API_KEY" in message
    assert "parallel_search_basic: PARALLEL_API_KEY" in message
    assert "brave_search: BRAVE_SEARCH_API_KEY" in message
    assert "firecrawl_search: FIRECRAWL_API_KEY" in message
    # The Nimble base URL ships as a code default and its key is set here, so
    # neither shows up as missing.
    assert "NIMBLE_BASE_URL" not in message
    assert "NIMBLE_API_KEY" not in message


@pytest.mark.unit
@pytest.mark.asyncio
async def test_amain_sampler_configs_include_provider_runtime_knobs(monkeypatch, tmp_path):
    captured = {}

    async def fake_preflight_or_die(**kwargs):
        return None

    async def fake_run_benchmark(**kwargs):
        captured["sampler_configs"] = kwargs["sampler_configs"]
        return []

    monkeypatch.setattr("nimble_benchmark.cli.preflight_or_die", fake_preflight_or_die)
    monkeypatch.setattr("nimble_benchmark.cli.run_benchmark", fake_run_benchmark)
    monkeypatch.setattr("nimble_benchmark.cli.aggregate_run", lambda **kwargs: None)
    monkeypatch.setattr("nimble_benchmark.cli.write_run_md", lambda **kwargs: None)
    monkeypatch.setattr("nimble_benchmark.cli.write_run_json", lambda **kwargs: None)
    monkeypatch.setattr("nimble_benchmark.cli.make_run_dir", lambda **kwargs: tmp_path)
    monkeypatch.setenv("EXA_TIMEOUT_S", "12")

    args = build_parser().parse_args(["--samplers", "all_apis", "--limit", "3"])
    settings = Settings(
        nimble_api_key="local-key",
        exa_api_key="exa-key",
        parallel_api_key="parallel-key",
        firecrawl_api_key="firecrawl-key",
        brave_search_api_key="brave-key",
        tavily_api_key="tavily-key",
        openai_api_key="openai-key",
        _env_file=None,
    )

    await _amain(args, settings)

    configs = captured["sampler_configs"]
    assert {
        "base_url": "https://api.exa.ai",
        "search_type": "auto",
        "num_results": 10,
        "timeout_s": 12.0,
    }.items() <= configs["exa_search_auto"].items()
    assert configs["exa_search_fast"]["search_type"] == "fast"
    assert {"mode": "basic", "max_results": 10}.items() <= configs["parallel_search_basic"].items()
    assert configs["parallel_search_turbo"]["mode"] == "turbo"
    assert configs["firecrawl_search"]["base_url"] == "https://api.firecrawl.dev"
    # ``search_depth`` is what separates the two Tavily lanes, so the recorded
    # config has to carry it.
    assert configs["tavily_search_basic"]["search_depth"] == "basic"
    assert configs["tavily_search_fast"]["search_depth"] == "fast"
    # The prod Nimble lane uses the code-default URL, so pinning it in the
    # recorded config would churn it for no signal.
    assert "base_url" not in configs["nimble_search"]
    # No lane opts into ``full_content``, so no recorded config carries the key.
    assert "full_content" not in configs["nimble_search"]
    # The nimble lane carries only retrieval-side knobs now; the answer-source
    # and debug-override keys are gone.
    assert "answer_source" not in configs["nimble_search"]
    assert "include_answer" not in configs["nimble_search"]
    assert "answer_model" not in configs["nimble_search"]
    assert "disable_livecrawl" not in configs["nimble_search"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_sampler_config_never_contains_api_key(monkeypatch, tmp_path):
    captured = {}

    async def fake_preflight_or_die(**kwargs):
        return None

    async def fake_run_benchmark(**kwargs):
        captured["sampler_configs"] = kwargs["sampler_configs"]
        return []

    monkeypatch.setattr("nimble_benchmark.cli.preflight_or_die", fake_preflight_or_die)
    monkeypatch.setattr("nimble_benchmark.cli.run_benchmark", fake_run_benchmark)
    monkeypatch.setattr("nimble_benchmark.cli.aggregate_run", lambda **kwargs: None)
    monkeypatch.setattr("nimble_benchmark.cli.write_run_md", lambda **kwargs: None)
    monkeypatch.setattr("nimble_benchmark.cli.write_run_json", lambda **kwargs: None)
    monkeypatch.setattr("nimble_benchmark.cli.make_run_dir", lambda **kwargs: tmp_path)

    args = build_parser().parse_args(["--samplers", "all_apis"])
    settings = Settings(
        nimble_api_key="nimble-secret-key",
        exa_api_key="exa-secret-key",
        parallel_api_key="parallel-secret-key",
        firecrawl_api_key="firecrawl-secret-key",
        brave_search_api_key="brave-secret-key",
        tavily_api_key="tavily-secret-key",
        openai_api_key="openai-secret-key",
        _env_file=None,
    )

    await _amain(args, settings)

    serialized_config_values = json.dumps(captured["sampler_configs"], sort_keys=True, default=str)
    for field_name in ["api_key", "nimble_api_key", "exa_api_key", "openai_api_key"]:
        assert field_name not in serialized_config_values
    for secret in _settings_secrets(settings):
        assert secret not in serialized_config_values


def _settings_secrets(settings: Settings) -> list[str]:
    secrets = []
    for field_name in type(settings).model_fields:
        value = getattr(settings, field_name)
        if isinstance(value, SecretStr):
            secrets.append(value.get_secret_value())
    return [secret for secret in secrets if secret]


def _set_competition_api_env(monkeypatch) -> None:
    monkeypatch.setenv("NIMBLE_API_KEY", "nimble-key")
    monkeypatch.setenv("EXA_API_KEY", "exa-key")
    monkeypatch.setenv("PARALLEL_API_KEY", "parallel-key")
    monkeypatch.setenv("FIRECRAWL_API_KEY", "firecrawl-key")
    monkeypatch.setenv("TAVILY_API_KEY", "tavily-key")
    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "brave-key")
    monkeypatch.setenv("OPENAI_API_KEY", "openai-key")


@pytest.mark.unit
def test_cli_rejects_a_non_openai_synthesis_model_at_parse_time():
    """Every graded answer is synthesized eval-side through the OpenAI client,
    so the Anthropic entries are gone from ``SYNTHESIS_MODEL_CHOICES`` and
    naming one fails before the run starts."""
    with pytest.raises(SystemExit):
        build_parser().parse_args(["--samplers", "nimble_search", "--synthesis-model", "claude-haiku-4-5"])


@pytest.mark.unit
@pytest.mark.asyncio
async def test_amain_rejects_a_non_openai_synthesis_model_from_the_environment():
    """``SYNTHESIS_MODEL`` is an unconstrained ``str`` on ``Settings``, so a
    value that never went through argparse must still fail fast rather than
    crashing mid-run against a live upstream."""
    args = build_parser().parse_args(["--samplers", "nimble_search"])
    args.synthesis_model = "claude-haiku-4-5"
    settings = Settings(nimble_api_key="prod-key", openai_api_key="openai-key", _env_file=None)

    with pytest.raises(RuntimeError, match=r"OpenAI"):
        await _amain(args, settings)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_amain_threads_an_openai_synthesis_model_through_to_the_runner(monkeypatch, tmp_path):
    captured = {}

    async def fake_preflight_or_die(**kwargs):
        return None

    async def fake_run_benchmark(**kwargs):
        captured["sampler_names"] = [sampler.name for sampler in kwargs["samplers"]]
        captured["synthesis_model"] = kwargs["synthesis_model"]
        return []

    monkeypatch.setattr("nimble_benchmark.cli.preflight_or_die", fake_preflight_or_die)
    monkeypatch.setattr("nimble_benchmark.cli.run_benchmark", fake_run_benchmark)
    monkeypatch.setattr("nimble_benchmark.cli.aggregate_run", lambda **kwargs: None)
    monkeypatch.setattr("nimble_benchmark.cli.write_run_md", lambda **kwargs: None)
    monkeypatch.setattr("nimble_benchmark.cli.write_run_json", lambda **kwargs: None)
    monkeypatch.setattr("nimble_benchmark.cli.make_run_dir", lambda **kwargs: tmp_path)

    args = build_parser().parse_args(["--samplers", "all_apis", "--synthesis-model", "gpt-4o-mini"])
    settings = Settings(
        nimble_api_key="nimble-key",
        exa_api_key="exa-key",
        parallel_api_key="parallel-key",
        firecrawl_api_key="firecrawl-key",
        brave_search_api_key="brave-key",
        tavily_api_key="tavily-key",
        openai_api_key="openai-key",
        _env_file=None,
    )

    await _amain(args, settings)

    assert captured["sampler_names"] == [
        "nimble_search",
        "exa_search_auto",
        "exa_search_fast",
        "parallel_search_basic",
        "parallel_search_turbo",
        "tavily_search_basic",
        "tavily_search_fast",
        "brave_search",
        "firecrawl_search",
    ]
    assert captured["synthesis_model"] == "gpt-4o-mini"


@pytest.mark.unit
def test_write_run_md_includes_synthesis_model_in_header(tmp_path: Path) -> None:
    """The run.md header is the only place a casual reader sees which LLM
    synthesized/answered the questions. Surface it so reviewers don't have to
    open run.json."""
    pd.DataFrame(
        [
            {
                "provider": "tavily_search_basic",
                "response_kind": "search_results",
                "answer_source": "synth",
                "accuracy_score": 0.5,
                "ndcg_at_10": 0.0,
                "recall_at_10_llm": 0.0,
                "provider_response_time_ms_p50": 100.0,
                "provider_response_time_ms_p95": 150.0,
                "citation_parse_error_count": 0,
            }
        ]
    ).to_csv(tmp_path / "analyzed_results.csv", index=False)

    write_run_md(
        run_dir=tmp_path,
        args={
            "dataset": "simpleqa",
            "limit": 1,
            "answer_source": "synth",
            "synthesis_model": "claude-haiku-4-5",
        },
        started_at=datetime(2026, 1, 1, 0, 0, 0),
        finished_at=datetime(2026, 1, 1, 0, 1, 0),
    )

    report = (tmp_path / "run.md").read_text()
    assert "**Synthesis model:** `claude-haiku-4-5`" in report


@pytest.mark.unit
def test_write_run_md_synthesis_model_falls_back_when_args_missing(tmp_path: Path) -> None:
    pd.DataFrame(
        [
            {
                "provider": "tavily_search_basic",
                "response_kind": "search_results",
                "answer_source": "synth",
                "accuracy_score": 0.5,
                "ndcg_at_10": 0.0,
                "recall_at_10_llm": 0.0,
                "provider_response_time_ms_p50": 100.0,
                "provider_response_time_ms_p95": 150.0,
                "citation_parse_error_count": 0,
            }
        ]
    ).to_csv(tmp_path / "analyzed_results.csv", index=False)

    write_run_md(
        run_dir=tmp_path,
        args={"dataset": "simpleqa", "limit": 1, "answer_source": "synth"},
        started_at=datetime(2026, 1, 1, 0, 0, 0),
        finished_at=datetime(2026, 1, 1, 0, 1, 0),
    )

    assert "**Synthesis model:** `—`" in (tmp_path / "run.md").read_text()


@pytest.mark.unit
def test_report_header_carries_synthesis_and_judge_models(tmp_path: Path) -> None:
    write_run_md(
        run_dir=tmp_path,
        args={
            "dataset": "simpleqa",
            "limit": 1,
            "synthesis_model": "gpt-4o",
            "judge_model": "gpt-4o-2024-08-06",
        },
        started_at=datetime(2026, 1, 1, 0, 0, 0),
        finished_at=datetime(2026, 1, 1, 0, 1, 0),
    )
    report = (tmp_path / "run.md").read_text()
    assert "**Synthesis model:** `gpt-4o`" in report
    assert "**Judge model:** `gpt-4o-2024-08-06`" in report


@pytest.mark.unit
def test_report_header_marks_judge_skipped(tmp_path: Path) -> None:
    write_run_md(
        run_dir=tmp_path,
        args={
            "dataset": "simpleqa",
            "limit": 1,
            "judge_model": "gpt-4o-2024-08-06",
            "skip_llm_judge": True,
        },
        started_at=datetime(2026, 1, 1, 0, 0, 0),
        finished_at=datetime(2026, 1, 1, 0, 1, 0),
    )
    assert "**Judge model:** `skipped`" in (tmp_path / "run.md").read_text()


@pytest.mark.unit
def test_write_run_md_renders_blank_accuracy_when_grader_skipped(tmp_path: Path) -> None:
    pd.DataFrame(
        [
            {
                "provider": "nimble_search",
                "response_kind": "search_results",
                "answer_source": "synth",
                "accuracy_score": float("nan"),
                "ndcg_at_10": 0.5,
                "recall_at_10_llm": 0.75,
                "provider_response_time_ms_p50": 100.0,
                "provider_response_time_ms_p95": 150.0,
                "citation_parse_error_count": 0,
            }
        ]
    ).to_csv(tmp_path / "analyzed_results.csv", index=False)

    write_run_md(
        run_dir=tmp_path,
        args={"dataset": "simpleqa", "limit": 1, "answer_source": "synth"},
        started_at=datetime(2026, 1, 1, 0, 0, 0),
        finished_at=datetime(2026, 1, 1, 0, 1, 0),
    )

    report = (tmp_path / "run.md").read_text()
    assert "accuracy (via synth)" not in report
    # The legacy fixture has no problem_count, so ``n`` renders as ``—``; a
    # NaN accuracy_score renders as ``—`` too rather than ``nan``.
    assert "| provider | n | accuracy | p50 ms | p95 ms |" in report
    assert "| nimble_search | — | — | 100 | 150 |" in report


@pytest.mark.unit
def test_write_run_md_includes_sampler_configuration_section(tmp_path: Path) -> None:
    """run.md must carry the same Sampler Configuration block as the leaderboard
    so a reviewer reading either surface sees the per-provider knobs without
    rummaging through `sampler_config_*.json` by hand."""
    pd.DataFrame(
        [
            {
                "provider": "brave_search",
                "response_kind": "search_results",
                "answer_source": "synth",
                "accuracy_score": 0.96,
                "ndcg_at_10": 0.4,
                "recall_at_10_llm": 0.9,
                "provider_response_time_ms_p50": 4000.0,
                "provider_response_time_ms_p95": 8000.0,
                "citation_parse_error_count": 0,
            }
        ]
    ).to_csv(tmp_path / "analyzed_results.csv", index=False)
    (tmp_path / "sampler_config_brave_search.json").write_text(
        json.dumps(
            {
                "count": 10,
                "extra_snippets": True,
                "operators": False,
                "timeout_s": 60,
            }
        ),
        encoding="utf-8",
    )

    write_run_md(
        run_dir=tmp_path,
        args={"dataset": "simpleqa", "limit": 1},
        started_at=datetime(2026, 1, 1, 0, 0, 0),
        finished_at=datetime(2026, 1, 1, 0, 1, 0),
    )
    report = (tmp_path / "run.md").read_text()

    assert "## Sampler Configuration" in report
    assert "count=10" in report
    assert "extra_snippets=true" in report
    assert "operators=false" in report
    # Hidden fields stay out of run.md as well.
    assert "timeout_s" not in report


# === Analyzer split-CSV regression tests (Section 3a / IRON RULE) =====================


def _write_raw_results_csv(path: Path, *, provider: str, response_kind: str, rows: list[dict]):
    """Small helper for the analyzer regression tests below: build a
    ``dataset_*_raw_results_<sampler>.csv`` with the columns ``aggregate_run``
    reads (provider, response_kind, answer_source_used, query, evaluation
    result, status, latency, plus any metric columns the test supplies)."""
    base_row = {
        "response_kind": response_kind,
        "answer_source_used": "synth",
        "request_status": "ok",
        "request_response_time_ms": 100.0,
    }
    enriched = [{**base_row, **row} for row in rows]
    pd.DataFrame(enriched).to_csv(path, index=False)


@pytest.mark.unit
def test_aggregate_run_writes_one_summary_holding_every_kind(tmp_path: Path) -> None:
    """One ``analyzed_results.csv`` holds every lane. The per-kind split files
    must not reappear -- external consumers pinned to them should fail loudly,
    not read a stale file."""
    _write_raw_results_csv(
        tmp_path / "dataset_simpleqa_raw_results_nimble_search.csv",
        provider="nimble_search",
        response_kind="search_results",
        rows=[{"provider": "nimble_search", "evaluation_result": "is_correct", "query": "q1"}],
    )
    # A synthetic answer-kind lane: no such lane ships today, and the point of
    # this test is that the analyzer partitions on the column rather than on a
    # roster it knows.
    _write_raw_results_csv(
        tmp_path / "dataset_simpleqa_raw_results_example_answer_lane.csv",
        provider="example_answer_lane",
        response_kind="answer_with_citations",
        rows=[{"provider": "example_answer_lane", "evaluation_result": "is_correct", "query": "q1"}],
    )

    aggregate_run(run_dir=tmp_path, dataset_name="simpleqa")

    summary_csv = tmp_path / "analyzed_results.csv"
    assert summary_csv.exists()
    for legacy in ("analyzed_answer_results.csv", "analyzed_search_results.csv"):
        assert not (tmp_path / legacy).exists(), f"split CSV {legacy} must not be written"

    summary_df = pd.read_csv(summary_csv)
    assert set(summary_df["provider"]) == {"nimble_search", "example_answer_lane"}
    assert set(summary_df["response_kind"]) == {"search_results", "answer_with_citations"}


@pytest.mark.unit
def test_aggregate_run_writes_a_header_only_csv_when_no_lanes_produced_rows(tmp_path: Path) -> None:
    """An empty run still writes the summary CSV so downstream readers don't
    have to branch on file existence."""
    aggregate_run(run_dir=tmp_path, dataset_name="simpleqa")

    summary_csv = tmp_path / "analyzed_results.csv"
    assert summary_csv.exists()
    # The file may be empty (no rows) or header-only depending on pandas
    # behavior; either way the loader pattern the consumer uses must return an
    # empty frame rather than throwing.
    body = summary_csv.read_text(encoding="utf-8")
    if body.strip():
        assert pd.read_csv(summary_csv).empty


@pytest.mark.unit
def test_aggregate_run_keeps_an_unfamiliar_response_kind_in_the_summary(tmp_path: Path) -> None:
    """There is no kind partition any more, so a sampler introducing a new
    ``response_kind`` value lands in the summary like any other lane instead
    of tripping a classifier."""
    _write_raw_results_csv(
        tmp_path / "dataset_simpleqa_raw_results_mystery_sampler.csv",
        provider="mystery_sampler",
        response_kind="tool_use_chain",
        rows=[
            {"provider": "mystery_sampler", "evaluation_result": "is_correct", "query": "q1"},
        ],
    )

    aggregate_run(run_dir=tmp_path, dataset_name="simpleqa")

    summary_df = pd.read_csv(tmp_path / "analyzed_results.csv")
    assert set(summary_df["provider"]) == {"mystery_sampler"}
    assert set(summary_df["response_kind"]) == {"tool_use_chain"}


@pytest.mark.unit
def test_aggregate_run_writes_one_significance_file_and_no_legacy(tmp_path: Path) -> None:
    """Significance lands in ``significance.csv``. The per-kind split files
    must not reappear, and the baseline resolves to the ``nimble_search``
    system."""
    for provider in ("nimble_search", "exa_search_auto", "brave_search"):
        _write_raw_results_csv(
            tmp_path / f"dataset_simpleqa_raw_results_{provider}.csv",
            provider=provider,
            response_kind="search_results",
            rows=[
                {
                    "provider": provider,
                    "evaluation_result": "is_correct",
                    "query": "q1",
                    "predicted_urls": '["https://a.com"]',
                    "recall_at_10_llm": 0.5,
                },
                {
                    "provider": provider,
                    "evaluation_result": "is_incorrect",
                    "query": "q2",
                    "predicted_urls": '["https://b.com"]',
                    "recall_at_10_llm": 0.3,
                },
            ],
        )

    aggregate_run(run_dir=tmp_path, dataset_name="simpleqa")

    significance_csv = tmp_path / "significance.csv"
    assert significance_csv.exists()
    for legacy in ("significance_answer.csv", "significance_search.csv"):
        assert not (tmp_path / legacy).exists(), f"split significance CSV {legacy} must not be written"

    significance_df = pd.read_csv(significance_csv)
    assert not significance_df.empty
    assert set(significance_df["baseline"]) == {"nimble_search/search_results/synth"}
    assert set(significance_df["system"]) == {
        "exa_search_auto/search_results/synth",
        "brave_search/search_results/synth",
    }


# === Reliability gate + failure-relative metrics =======================
#
# The analyzer must (a) compute retrieval/latency means over the
# successful subset only, (b) surface ``failure_count`` /
# ``failure_rate`` for every lane, (c) flag lanes whose failure rate
# exceeds the configured ceiling with ``excluded_reason``, and (d) drop
# excluded lanes from the per-kind significance file. Tests below pin
# each rung.


def _write_raw_rows_with_status(
    path: Path,
    *,
    provider: str,
    response_kind: str,
    rows_by_status: dict[str, list[dict]],
) -> None:
    """Write a raw-results CSV containing a mix of ``request_status`` rows.

    ``rows_by_status`` maps a status string (e.g. ``"ok"`` /
    ``"failed_after_retries"`` / ``"validation_reject"``) to a list of
    per-row dicts. Each dict may carry metric columns (``ndcg_at_10``
    etc.); the helper stamps ``provider``, ``response_kind``,
    ``answer_source_used``, ``request_status``, and a default latency.
    """
    answer_source = "api" if response_kind != "search_results" else "synth"
    enriched: list[dict] = []
    for status, rows in rows_by_status.items():
        for row in rows:
            enriched.append(
                {
                    "provider": provider,
                    "response_kind": response_kind,
                    "answer_source_used": answer_source,
                    "request_status": status,
                    "request_response_time_ms": 120.0,
                    **row,
                }
            )
    pd.DataFrame(enriched).to_csv(path, index=False)


@pytest.mark.unit
def test_aggregate_run_metrics_computed_over_successful_subset(tmp_path: Path) -> None:
    """When 4 of 100 rows failed, the per-lane mean of every retrieval
    metric must equal the mean over the 96 successful rows -- the failed
    rows' empty ``predicted_urls`` / zero-score columns must NOT drag the
    mean toward zero. Failure counts and rate ride along on the same
    summary row so the report can warn.
    """
    ok_rows = [
        {
            "query": f"q_ok_{i}",
            "evaluation_result": "is_correct",
            "predicted_urls": '["https://a.com"]',
            "recall_at_10_llm": 1.0,
        }
        for i in range(96)
    ]
    failed_rows = [
        {
            "query": f"q_fail_{i}",
            "evaluation_result": "validation_reject",
            "predicted_urls": "[]",
            "recall_at_10_llm": 0.0,
        }
        for i in range(4)
    ]
    _write_raw_rows_with_status(
        tmp_path / "dataset_simpleqa_raw_results_tavily_search_basic.csv",
        provider="tavily_search_basic",
        response_kind="search_results",
        rows_by_status={"ok": ok_rows, "failed_after_retries": failed_rows},
    )

    aggregate_run(run_dir=tmp_path, dataset_name="simpleqa")
    summary = pd.read_csv(tmp_path / "analyzed_results.csv")

    row = summary.iloc[0]
    # Mean is taken over the 96 successful rows where ndcg/recall = 1.0;
    # the 4 failed rows (with 0.0) must not be included.
    assert row["recall_at_10_llm"] == pytest.approx(1.0)
    assert int(row["problem_count"]) == 100
    assert int(row["successful_problem_count"]) == 96
    assert int(row["failure_count"]) == 4
    assert row["failure_rate"] == pytest.approx(0.04)
    # Healthy lane: failure rate 4% is well under the 95% ceiling, the
    # 96 successful problems clears the (library default zero) min gate.
    assert pd.isna(row["excluded_reason"]) or row["excluded_reason"] == ""


@pytest.mark.unit
def test_aggregate_run_excludes_lane_above_max_failure_rate(tmp_path: Path) -> None:
    """A lane whose failure rate is over the configured ceiling lands in
    the summary with ``excluded_reason == "excess_failure_rate"`` so the
    report can flag it. Metrics are still computed over the few
    survivors -- the gate is advisory at the analyzer layer; the report
    + significance layer drop the lane downstream.
    """
    ok_rows = [
        {
            "query": "q_survivor",
            "evaluation_result": "is_correct",
            "predicted_urls": '["https://a.com"]',
            "recall_at_10_llm": 0.8,
        }
    ]
    failed_rows = [
        {
            "query": f"q_fail_{i}",
            "evaluation_result": "validation_reject",
            "predicted_urls": "[]",
        }
        for i in range(99)
    ]
    _write_raw_rows_with_status(
        tmp_path / "dataset_simpleqa_raw_results_broken_lane.csv",
        provider="broken_lane",
        response_kind="search_results",
        rows_by_status={"ok": ok_rows, "failed_after_retries": failed_rows},
    )

    aggregate_run(
        run_dir=tmp_path,
        dataset_name="simpleqa",
        max_failure_rate=0.95,
    )
    summary = pd.read_csv(tmp_path / "analyzed_results.csv")

    row = summary.iloc[0]
    assert row["excluded_reason"] == "excess_failure_rate"
    assert int(row["failure_count"]) == 99
    assert row["failure_rate"] == pytest.approx(0.99)
    # The survivor's score is preserved; only the lane's report
    # treatment changes.


@pytest.mark.unit
def test_aggregate_run_excludes_lane_below_min_successful_problems(tmp_path: Path) -> None:
    """A lane that succeeded on fewer problems than the configured floor
    is flagged with ``below_min_successful_problems`` even when its
    failure rate is fine. Caller supplies the floor explicitly here
    because the library default is 0 (the CLI applies the strict
    project default)."""
    ok_rows = [
        {
            "query": f"q_ok_{i}",
            "evaluation_result": "is_correct",
            "predicted_urls": '["https://a.com"]',
            "recall_at_10_llm": 0.5,
        }
        for i in range(3)
    ]
    _write_raw_rows_with_status(
        tmp_path / "dataset_simpleqa_raw_results_tiny_lane.csv",
        provider="tiny_lane",
        response_kind="search_results",
        rows_by_status={"ok": ok_rows},
    )

    aggregate_run(
        run_dir=tmp_path,
        dataset_name="simpleqa",
        min_successful_problems=10,
    )
    summary = pd.read_csv(tmp_path / "analyzed_results.csv")

    row = summary.iloc[0]
    assert row["excluded_reason"] == "below_min_successful_problems"
    assert int(row["successful_problem_count"]) == 3
    assert int(row["failure_count"]) == 0
    # Metric mean is still over the 3 successful rows -- the floor only
    # changes labelling, not the math.


@pytest.mark.unit
def test_aggregate_run_failure_rate_gate_wins_when_both_trigger(tmp_path: Path) -> None:
    """A lane that's both too small AND mostly failing must report the
    more specific reason -- excess failures -- so the report doesn't
    blame the wrong axis.
    """
    ok_rows = [
        {
            "query": "q_only_survivor",
            "evaluation_result": "is_correct",
            "predicted_urls": '["https://a.com"]',
        }
    ]
    failed_rows = [
        {
            "query": f"q_fail_{i}",
            "evaluation_result": "validation_reject",
            "predicted_urls": "[]",
        }
        for i in range(99)
    ]
    _write_raw_rows_with_status(
        tmp_path / "dataset_simpleqa_raw_results_doubly_broken.csv",
        provider="doubly_broken",
        response_kind="search_results",
        rows_by_status={"ok": ok_rows, "failed_after_retries": failed_rows},
    )

    aggregate_run(
        run_dir=tmp_path,
        dataset_name="simpleqa",
        min_successful_problems=10,
        max_failure_rate=0.95,
    )
    summary = pd.read_csv(tmp_path / "analyzed_results.csv")

    row = summary.iloc[0]
    assert row["excluded_reason"] == "excess_failure_rate"


@pytest.mark.unit
def test_aggregate_run_drops_excluded_lane_from_significance(tmp_path: Path) -> None:
    """A lane the reliability gate flagged with ``excluded_reason`` must
    not appear as either the baseline or a challenger in the per-kind
    significance CSV. Otherwise a barely-surviving lane could be picked
    as the comparison anchor and skew the corrected p-values for every
    healthy lane in its kind.
    """
    healthy_rows = [
        {
            "query": f"q_{i}",
            "evaluation_result": "is_correct",
            "predicted_urls": '["https://a.com"]',
            "recall_at_10_llm": 0.7,
        }
        for i in range(20)
    ]
    for provider in ("tavily_search_basic", "exa_search_auto"):
        _write_raw_rows_with_status(
            tmp_path / f"dataset_simpleqa_raw_results_{provider}.csv",
            provider=provider,
            response_kind="search_results",
            rows_by_status={"ok": healthy_rows},
        )

    # A third "broken" lane that flunks the failure-rate gate.
    ok_rows = [
        {
            "query": "q_0",
            "evaluation_result": "is_correct",
            "predicted_urls": '["https://a.com"]',
            "recall_at_10_llm": 0.7,
        }
    ]
    failed_rows = [
        {
            "query": f"q_fail_{i}",
            "evaluation_result": "validation_reject",
            "predicted_urls": "[]",
        }
        for i in range(99)
    ]
    _write_raw_rows_with_status(
        tmp_path / "dataset_simpleqa_raw_results_broken_lane.csv",
        provider="broken_lane",
        response_kind="search_results",
        rows_by_status={"ok": ok_rows, "failed_after_retries": failed_rows},
    )

    aggregate_run(
        run_dir=tmp_path,
        dataset_name="simpleqa",
        max_failure_rate=0.95,
    )

    sig_path = tmp_path / "significance.csv"
    if sig_path.exists():
        sig_df = pd.read_csv(sig_path)
        for col in ("baseline", "system"):
            assert (
                not sig_df[col].astype(str).str.contains("broken_lane").any()
            ), f"excluded lane must not appear in significance {col}"


def _empty_answer_response(provider="firecrawl_search", kind="search_results"):
    """A status=ok response with nothing gradeable: no chunks for a /search
    lane to synthesize from, and no ``api_answer`` for an answer lane."""
    return ProviderResponse(
        provider=provider,
        query="q",
        response_kind=kind,
        chunks=[],
        api_answer=None,
        latency=ProviderLatency(100.0, 50.0),
        usage=ProviderUsage(),
        status="ok",
        raw={},
    )


class _NeverCalledGrader:
    def __init__(self):
        self.calls = 0

    async def grader(self, query, ground_truth, predicted):
        self.calls += 1
        return {"score_name": "is_correct"}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_ok_response_with_empty_answer_still_emits_a_row(monkeypatch):
    """A provider that returns HTTP 200 with no answer text must still produce
    a row. Emitting nothing silently dropped the query from the artifact: it
    left the denominator entirely instead of counting against the lane.
    Observed 2026-08-08 n=500: two lanes lost queries with no failure logged
    anywhere.
    """

    async def fake_judge_chunks(**kwargs):
        return []

    async def fake_synth(query, formatted, model):
        return ""

    monkeypatch.setattr("nimble_benchmark.runner.judge_chunks", fake_judge_chunks)
    monkeypatch.setattr("nimble_benchmark.runner.synth", fake_synth)
    dataset = _NeverCalledGrader()

    rows = await response_to_rows(
        response=_empty_answer_response(),
        dataset=dataset,
        ground_truth="gt",
        gold_urls=[],
        synthesis_model="gpt-4o",
        judge_model="gpt-4o",
        judge_prompt_variant="passage",
        judge_relevance_threshold=2,
    )

    assert len(rows) == 1, "an ok-but-answerless response must still yield a row"
    assert rows[0]["generated_answer"] == ""
    assert rows[0]["answer_source_used"] == "synth"
    # request_status stays ok -- the HTTP call genuinely succeeded, so this must
    # not inflate the lane's failure_rate.
    assert rows[0]["request_status"] == "ok"
    # Graded as not-attempted, which `_aggregate_accuracy` keeps in the
    # accuracy denominator and scores as a miss.
    assert rows[0]["evaluation_result"] == "is_not_attempted"
    assert dataset.calls == 0, "must not spend an OpenAI grader call on an empty answer"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_empty_answer_row_is_labelled_with_its_query(monkeypatch):
    """The placeholder row must carry the query it stands for, so the artifact
    still says which question went unanswered."""

    async def fake_judge_chunks(**kwargs):
        return []

    async def fake_synth(query, formatted, model):
        return ""

    monkeypatch.setattr("nimble_benchmark.runner.judge_chunks", fake_judge_chunks)
    monkeypatch.setattr("nimble_benchmark.runner.synth", fake_synth)

    rows = await response_to_rows(
        response=_empty_answer_response(provider="firecrawl_search"),
        dataset=_NeverCalledGrader(),
        ground_truth="gt",
        gold_urls=[],
        synthesis_model="gpt-4o",
        judge_model="gpt-4o",
        judge_prompt_variant="passage",
        judge_relevance_threshold=2,
    )

    assert [row["query"] for row in rows] == ["q"]
    assert {row["answer_source_used"] for row in rows} == {"synth"}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_failed_request_rows_stay_not_evaluated(monkeypatch):
    """Regression guard: the ok-but-empty path must not change how genuine
    request failures are recorded. Those stay ``not_evaluated`` -- the lane's
    failure_rate already accounts for them."""

    async def fake_judge_chunks(**kwargs):
        return []

    monkeypatch.setattr("nimble_benchmark.runner.judge_chunks", fake_judge_chunks)

    response = ProviderResponse(
        provider="firecrawl_search",
        query="q",
        response_kind="search_results",
        chunks=[],
        api_answer=None,
        latency=ProviderLatency(100.0, None),
        usage=ProviderUsage(),
        status="failed_after_retries",
        raw={"error": "boom"},
    )

    rows = await response_to_rows(
        response=response,
        dataset=_NeverCalledGrader(),
        ground_truth="gt",
        gold_urls=[],
        synthesis_model="gpt-4o",
        judge_model="gpt-4o",
        judge_prompt_variant="passage",
        judge_relevance_threshold=2,
    )

    assert len(rows) == 1
    assert rows[0]["evaluation_result"] == "not_evaluated"
    assert rows[0]["request_status"] == "failed_after_retries"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_no_answer_marker_never_leaks_into_csv_columns(monkeypatch):
    """The internal marker key on placeholder candidates must not become a
    CSV column -- the raw schema is a published contract."""

    async def fake_judge_chunks(**kwargs):
        return []

    async def fake_synth(query, formatted, model):
        return ""

    monkeypatch.setattr("nimble_benchmark.runner.judge_chunks", fake_judge_chunks)
    monkeypatch.setattr("nimble_benchmark.runner.synth", fake_synth)

    rows = await response_to_rows(
        response=_empty_answer_response(),
        dataset=_NeverCalledGrader(),
        ground_truth="gt",
        gold_urls=[],
        synthesis_model="gpt-4o",
        judge_model="gpt-4o",
        judge_prompt_variant="passage",
        judge_relevance_threshold=2,
    )

    assert not [key for key in rows[0] if key.startswith("_")]

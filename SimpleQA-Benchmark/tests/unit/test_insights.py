"""Tests for the per-run insights.md / errors.md writers.

The classifier cases below are **verbatim error strings observed in this
repo's own ``runs/`` history**, not invented examples. Several encode traps
that cost real debugging time, so they are pinned deliberately:

* Parallel's monthly quota message literally says "rate limit" -- classifying
  it as throttling sends an operator to tune ``*_RATE_PER_SECOND``, which can
  never fix a per-month budget.
* Gemini's free tier returns HTTP 429 with ``"limit": 0``. Treating the status
  code as the diagnosis implies "back off and retry", but there is no quota to
  wait for.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import pytest

from nimble_benchmark.insights import (
    AUTH,
    BILLING,
    CONFIG,
    QUOTA,
    RATE_LIMIT,
    TRANSIENT,
    UNKNOWN,
    classify_error,
    find_previous_run_dir,
    slices_comparable,
    write_errors_md,
    write_insights_md,
)

_NOW = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)
_LATER = datetime(2026, 8, 8, 12, 30, tzinfo=UTC)


# ----------------------------------------------------------------------
# Error classification
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # --- billing: Exa credit exhaustion (observed run_20260808_163720) ---
        ("402, message='Payment Required', url='https://api.exa.ai/search'", BILLING),
        ("You have exceeded your credits limit. Please top up to keep using Exa", BILLING),
        # --- quota: says "rate limit" but is a per-MONTH budget ---
        (
            '{"code":8,"message":"Product rate limit quota exceeded for key: Product { key: '
            "'/*/search@key-quota_search_pm', quota_key: 'quota_search_pm' }. Please contact "
            'support@parallel.ai for higher rate limits."}',
            QUOTA,
        ),
        # --- quota: HTTP 429, but free tier with limit 0 -- retrying never works ---
        (
            '{"error": {"code": 429, "message": "You exceeded your current quota, please check '
            'your plan and billing details.", "status": "RESOURCE_EXHAUSTED"}}',
            QUOTA,
        ),
        # --- genuine per-second throttling (Nimble + Brave) ---
        (
            '{"detail":"Rate limit exceeded for product \'answer\'. Limit is 3 request(s) per '
            'second. Retry after 1 second(s).","error_code":"rate_limit_exceeded"}',
            RATE_LIMIT,
        ),
        ("Error code: 429 - {'detail': 'Request rate limit exceeded for plan'}", RATE_LIMIT),
        # --- auth ---
        ("403, message='Forbidden', url='https://generativelanguage.googleapis.com/v1beta'", AUTH),
        ("401 Unauthorized: invalid api key", AUTH),
        # --- config: retired model alias ---
        ("404, message='Not Found', url='.../models/gemini-2.0-flash:generateContent'", CONFIG),
        # --- transient ---
        ("Connection timeout to host https://sdk.nimbleway.com/v2/search", TRANSIENT),
        ("transient HTTP 0", TRANSIENT),
        ("Server disconnected", TRANSIENT),
        ("upstream connect error or disconnect/reset before headers. reset reason: connection termination", TRANSIENT),
        ("Cannot connect to host api.exa.ai:443 ssl:default [Connection reset by peer]", TRANSIENT),
        ("Cannot connect to host sdk.nimbleway.com:443 ssl:default [nodename nor servname provided]", TRANSIENT),
        ("[Errno 32] Broken pipe", TRANSIENT),
        ("Request timed out.", TRANSIENT),
        ("Connection error.", TRANSIENT),
    ],
)
@pytest.mark.unit
def test_classify_error_on_real_signatures(text, expected):
    assert classify_error(text) is expected


@pytest.mark.unit
def test_quota_wins_over_rate_limit_for_period_budgets():
    """Regression guard for the trap: a message containing BOTH "rate limit"
    and a period-quota key must classify as quota, because throttling cannot
    fix a monthly budget."""
    text = "Product rate limit quota exceeded for key: quota_search_pm"
    assert classify_error(text) is QUOTA
    assert classify_error(text).blocking is True


@pytest.mark.unit
def test_unmatched_text_is_unclassified_not_forced():
    """An unrecognised failure must surface as `unknown` rather than being
    filed under a wrong (and wrongly actionable) heading."""
    assert classify_error("something nobody has seen before") is UNKNOWN
    assert classify_error("") is UNKNOWN
    assert classify_error(None) is UNKNOWN


# ----------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------


def _write_run(
    root: Path,
    name: str,
    *,
    rows: list[dict],
    args: dict,
    summary: list[dict] | None = None,
) -> Path:
    run_dir = root / name
    run_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(run_dir / f"dataset_simpleqa_raw_results_{rows[0]['provider']}.csv", index=False)
    summary_rows = (
        summary
        if summary is not None
        else [
            {
                "provider": rows[0]["provider"],
                "response_kind": "search_results",
                "answer_source": "synth",
                "problem_count": len(rows),
                "successful_problem_count": sum(1 for row in rows if row["request_status"] == "ok"),
                "failure_count": sum(1 for row in rows if row["request_status"] != "ok"),
                "failure_rate": sum(1 for row in rows if row["request_status"] != "ok") / len(rows),
                "excluded_reason": "",
                "accuracy_score": 0.9,
                "ndcg_at_10": 0.5,
                "recall_at_10_llm": 0.8,
                "provider_response_time_ms_p50": 1000,
                "provider_response_time_ms_p95": 2000,
            }
        ]
    )
    pd.DataFrame(summary_rows).to_csv(run_dir / "analyzed_results.csv", index=False)
    (run_dir / "run.json").write_text(json.dumps({"args": args, "started_at": _NOW.isoformat()}))
    return run_dir


def _summary_row(provider: str, *, accuracy: float) -> dict:
    """One analyzed_results.csv row, for tests that only care about accuracy."""
    return {
        "provider": provider,
        "response_kind": "search_results",
        "answer_source": "synth",
        "problem_count": 2,
        "successful_problem_count": 2,
        "failure_count": 0,
        "failure_rate": 0.0,
        "excluded_reason": "",
        "accuracy_score": accuracy,
        "ndcg_at_10": 0.5,
        "recall_at_10_llm": 0.8,
        "provider_response_time_ms_p50": 1000,
        "provider_response_time_ms_p95": 2000,
    }


def _ok_row(provider="nimble_search"):
    return {
        "provider": provider,
        "request_status": "ok",
        "request_error": "",
        "evaluation_result": "is_correct",
        "generated_answer": "an answer",
    }


def _failed_row(provider="exa_search_auto", error="402, message='Payment Required'"):
    return {
        "provider": provider,
        "request_status": "failed_after_retries",
        "request_error": error,
        "evaluation_result": "not_evaluated",
        "generated_answer": "",
    }


_ARGS = {"dataset": "simpleqa", "limit": 2, "random_state": 0}


# ----------------------------------------------------------------------
# errors.md
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_errors_md_is_empty_when_run_is_clean(tmp_path):
    """The contract the user asked for: no issues -> zero-byte file, so
    `test -s errors.md` is a one-shot health check."""
    run_dir = _write_run(tmp_path, "run_20260808_120000_x", rows=[_ok_row(), _ok_row()], args=_ARGS)

    path = write_errors_md(run_dir=run_dir, args=_ARGS)

    assert path.exists()
    assert path.stat().st_size == 0
    assert path.read_text() == ""


@pytest.mark.unit
def test_errors_md_reports_blocking_billing_failure(tmp_path):
    run_dir = _write_run(
        tmp_path,
        "run_20260808_120000_x",
        rows=[_failed_row(), _failed_row()],
        args=_ARGS,
    )

    text = write_errors_md(run_dir=run_dir, args=_ARGS).read_text()

    assert text, "a failing run must not produce an empty errors.md"
    assert "Action required" in text
    assert "Billing / credits exhausted" in text
    assert "Payment Required" in text
    # The remediation must be actionable, not just a restatement of the error.
    assert "Top up" in text


@pytest.mark.unit
def test_errors_md_marks_transient_failures_as_non_blocking(tmp_path):
    run_dir = _write_run(
        tmp_path,
        "run_20260808_120000_x",
        rows=[_ok_row("nimble_search"), _failed_row("nimble_search", "Server disconnected")],
        args=_ARGS,
    )

    text = write_errors_md(run_dir=run_dir, args=_ARGS).read_text()

    assert "No blocking problems" in text
    assert "Action required" not in text
    assert "Transient network / upstream" in text


@pytest.mark.unit
def test_errors_md_flags_empty_answer_rows_even_with_no_request_failures(tmp_path):
    """A provider returning HTTP 200 with no answer isn't a request failure,
    but it scores as an accuracy miss -- so it still belongs in the digest."""
    rows = [
        _ok_row("brave_search"),
        {
            "provider": "brave_search",
            "request_status": "ok",
            "request_error": "",
            "evaluation_result": "is_not_attempted",
            "generated_answer": "",
        },
    ]
    run_dir = _write_run(tmp_path, "run_20260808_120000_x", rows=rows, args=_ARGS)

    text = write_errors_md(run_dir=run_dir, args=_ARGS).read_text()

    assert "empty answer" in text
    assert "brave_search" in text


# ----------------------------------------------------------------------
# Previous-run selection
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_find_previous_run_takes_the_last_run_whatever_slice_it_sampled(tmp_path):
    """The diff is against the previous run, full stop. An n=5 smoke run that
    ran last is still the comparison a reader expects; the slice mismatch is
    reported as a caveat rather than sending the diff to an older run."""
    big_args = {"dataset": "simpleqa", "limit": 500, "random_state": 0}
    smoke_args = {"dataset": "simpleqa", "limit": 5, "random_state": 0}
    _write_run(tmp_path, "run_20260808_100000_big", rows=[_ok_row()], args=big_args)
    _write_run(tmp_path, "run_20260808_110000_smoke", rows=[_ok_row()], args=smoke_args)
    current = _write_run(tmp_path, "run_20260808_120000_big", rows=[_ok_row()], args=big_args)

    chosen = find_previous_run_dir(current)

    assert chosen is not None
    assert chosen.name == "run_20260808_110000_smoke"


@pytest.mark.unit
def test_find_previous_run_skips_a_run_with_no_analyzed_summaries(tmp_path):
    """A run that died before aggregation has nothing to diff against, so the
    search falls through to the last run that does."""
    _write_run(tmp_path, "run_20260808_100000_a", rows=[_ok_row()], args=_ARGS)
    (tmp_path / "run_20260808_110000_dead").mkdir()
    current = _write_run(tmp_path, "run_20260808_120000_b", rows=[_ok_row()], args=_ARGS)

    chosen = find_previous_run_dir(current)

    assert chosen is not None and chosen.name == "run_20260808_100000_a"


@pytest.mark.unit
def test_find_previous_run_returns_none_for_first_run(tmp_path):
    current = _write_run(tmp_path, "run_20260808_120000_x", rows=[_ok_row()], args=_ARGS)
    assert find_previous_run_dir(current) is None


@pytest.mark.unit
def test_slices_comparable_detects_seed_change():
    comparable, reason = slices_comparable(
        {"dataset": "simpleqa", "limit": 500, "random_state": 1},
        {"dataset": "simpleqa", "limit": 500, "random_state": 0},
    )
    assert comparable is False
    assert "random_state" in reason


# ----------------------------------------------------------------------
# insights.md
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_insights_md_handles_first_ever_run(tmp_path):
    run_dir = _write_run(tmp_path, "run_20260808_120000_x", rows=[_ok_row()], args=_ARGS)

    text = write_insights_md(run_dir=run_dir, args=_ARGS, started_at=_NOW, finished_at=_LATER).read_text()

    assert "No earlier run found" in text
    assert "At a glance" in text


@pytest.mark.unit
def test_insights_md_reports_lane_roster_changes(tmp_path):
    """The regression that motivated this file: a lane silently disappearing
    (or appearing) between runs must be called out."""
    args = {"dataset": "simpleqa", "limit": 2, "random_state": 0}
    previous_summary = [
        {
            "provider": "nimble_search_deep",
            "response_kind": "search_results",
            "answer_source": "synth",
            "problem_count": 2,
            "successful_problem_count": 2,
            "failure_count": 0,
            "failure_rate": 0.0,
            "excluded_reason": "",
            "accuracy_score": 0.9,
            "ndcg_at_10": 0.5,
            "recall_at_10_llm": 0.8,
            "provider_response_time_ms_p50": 1000,
            "provider_response_time_ms_p95": 2000,
        }
    ]
    _write_run(
        tmp_path, "run_20260808_100000_a", rows=[_ok_row("nimble_search_deep")], args=args, summary=previous_summary
    )
    current = _write_run(tmp_path, "run_20260808_120000_b", rows=[_ok_row("brave_search")], args=args)

    text = write_insights_md(run_dir=current, args=args, started_at=_NOW, finished_at=_LATER).read_text()

    assert "Dropped:" in text
    assert "nimble_search_deep" in text


@pytest.mark.unit
def test_insights_md_still_diffs_across_different_slices_but_caveats_them(tmp_path):
    """A slice change must not blank the comparison: the deltas are still
    rendered, carrying a warning that they mix provider change with sample
    change."""
    smoke_args = {"dataset": "simpleqa", "limit": 5, "random_state": 0}
    big_args = {"dataset": "simpleqa", "limit": 500, "random_state": 0}
    previous_summary = [_summary_row("nimble_search", accuracy=0.50)]
    _write_run(tmp_path, "run_20260808_100000_a", rows=[_ok_row()], args=smoke_args, summary=previous_summary)
    current = _write_run(
        tmp_path,
        "run_20260808_120000_b",
        rows=[_ok_row()],
        args=big_args,
        summary=[_summary_row("nimble_search", accuracy=0.90)],
    )

    text = write_insights_md(
        run_dir=current,
        args=big_args,
        started_at=_NOW,
        finished_at=_LATER,
    ).read_text()

    assert "different question sets" in text
    assert "Metric movements" in text
    assert "accuracy_score" in text


@pytest.mark.unit
def test_insights_md_collapses_gate_exclusions_on_smoke_runs(tmp_path):
    """On an n=5 run every lane trips `below_min_successful_problems`; listing
    each one buries the real problems, so it must collapse to one line."""
    args = {"dataset": "simpleqa", "limit": 5, "random_state": 0, "min_successful_problems": 30}
    summary = [
        {
            "provider": f"provider_{index}",
            "response_kind": "search_results",
            "answer_source": "synth",
            "problem_count": 5,
            "successful_problem_count": 5,
            "failure_count": 0,
            "failure_rate": 0.0,
            "excluded_reason": "below_min_successful_problems",
            "accuracy_score": 0.9,
            "ndcg_at_10": 0.5,
            "recall_at_10_llm": 0.8,
            "provider_response_time_ms_p50": 1000,
            "provider_response_time_ms_p95": 2000,
        }
        for index in range(6)
    ]
    run_dir = _write_run(tmp_path, "run_20260808_120000_x", rows=[_ok_row()], args=args, summary=summary)

    text = write_insights_md(run_dir=run_dir, args=args, started_at=_NOW, finished_at=_LATER).read_text()

    assert "Expected at this run size" in text
    assert text.count("below_min_successful_problems") <= 1


@pytest.mark.unit
def test_errors_md_stays_empty_for_a_healthy_smoke_run(tmp_path):
    """A small but perfectly healthy run must still produce an EMPTY errors.md.

    Every lane trips `below_min_successful_problems` at n=3 simply because the
    run is small. Counting that as an issue would make `errors.md` non-empty
    for every smoke run, turning the `test -s errors.md` health check into a
    permanent false alarm.
    """
    args = {"dataset": "simpleqa", "limit": 3, "random_state": 0, "min_successful_problems": 10}
    summary = [
        {
            "provider": "nimble_search",
            "response_kind": "search_results",
            "answer_source": "synth",
            "problem_count": 3,
            "successful_problem_count": 3,
            "failure_count": 0,
            "failure_rate": 0.0,
            "excluded_reason": "below_min_successful_problems",
            "accuracy_score": 1.0,
            "ndcg_at_10": 0.5,
            "recall_at_10_llm": 0.8,
            "provider_response_time_ms_p50": 1000,
            "provider_response_time_ms_p95": 2000,
        }
    ]
    run_dir = _write_run(
        tmp_path,
        "run_20260808_120000_x",
        rows=[_ok_row("nimble_search")] * 3,
        args=args,
        summary=summary,
    )

    assert write_errors_md(run_dir=run_dir, args=args).stat().st_size == 0


@pytest.mark.unit
def test_errors_md_is_written_for_a_real_gate_exclusion(tmp_path):
    """The structural-exclusion exemption must be narrow: a lane excluded for
    excess failures is a genuine issue even on a small run."""
    args = {"dataset": "simpleqa", "limit": 3, "random_state": 0, "min_successful_problems": 10}
    summary = [
        {
            "provider": "exa_search_auto",
            "response_kind": "search_results",
            "answer_source": "synth",
            "problem_count": 3,
            "successful_problem_count": 0,
            "failure_count": 3,
            "failure_rate": 1.0,
            "excluded_reason": "excess_failure_rate",
            "accuracy_score": 0.0,
            "ndcg_at_10": 0.0,
            "recall_at_10_llm": 0.0,
            "provider_response_time_ms_p50": 1000,
            "provider_response_time_ms_p95": 2000,
        }
    ]
    run_dir = _write_run(
        tmp_path, "run_20260808_120000_x", rows=[_failed_row("exa_search_auto")], args=args, summary=summary
    )

    text = write_errors_md(run_dir=run_dir, args=args).read_text()

    assert text
    assert "excess_failure_rate" in text


# ----------------------------------------------------------------------
# Lane keying (provider + answer_source).
#
# ``answer_source_used`` is the constant ``synth`` on current runs, but the
# lane-keyed grouping is retained so a run artifact written before that
# collapse still reads back with its api/synth lanes distinguished. These
# tests pin that back-compat path.
# ----------------------------------------------------------------------


def _row(provider, source, status="ok", error="", answer="an answer", verdict="is_correct"):
    return {
        "provider": provider,
        "answer_source_used": source,
        "request_status": status,
        "request_error": error,
        "evaluation_result": verdict,
        "generated_answer": answer,
    }


@pytest.mark.unit
def test_collect_failures_separates_api_from_synth_lanes(tmp_path):
    """An older artifact where one provider produced two lanes: grouping by
    provider alone would hide which source was broken."""
    from nimble_benchmark.insights import collect_failures

    rows = [
        _row("nimble_search", "api", status="failed_after_retries", error="Server disconnected", answer=""),
        _row("nimble_search", "synth"),
        _row("nimble_search", "api"),
        _row("nimble_search", "synth"),
    ]
    run_dir = _write_run(tmp_path, "run_20260808_120000_x", rows=rows, args=_ARGS)

    lanes = {lane.lane_key: lane for lane in collect_failures(run_dir)}

    assert set(lanes) == {"nimble_search/api"}, "only the api lane failed"
    assert lanes["nimble_search/api"].failed_rows == 1
    assert lanes["nimble_search/api"].total_rows == 2, "denominator must be the lane, not the provider"


@pytest.mark.unit
def test_errors_md_labels_the_failing_answer_source(tmp_path):
    rows = [
        _row("nimble_search", "api", status="failed_after_retries", error="Server disconnected", answer=""),
        _row("nimble_search", "synth"),
    ]
    run_dir = _write_run(tmp_path, "run_20260808_120000_x", rows=rows, args=_ARGS)

    text = write_errors_md(run_dir=run_dir, args=_ARGS).read_text()

    assert "nimble_search/api" in text
    assert "`nimble_search/synth`" not in text, "a healthy lane must not appear as failing"


@pytest.mark.unit
def test_empty_answer_counts_are_keyed_per_lane(tmp_path):
    """Counts are keyed by lane, not provider, so they line up with every
    other lane-keyed table in the digest."""
    from nimble_benchmark.insights import collect_empty_answer_rows

    rows = [
        _row("nimble_search", "api", answer="", verdict="is_not_attempted"),
        _row("nimble_search", "synth", answer="a synthesized answer"),
    ]
    run_dir = _write_run(tmp_path, "run_20260808_120000_x", rows=rows, args=_ARGS)

    counts = collect_empty_answer_rows(run_dir)

    assert counts == {"nimble_search/api": 1}


@pytest.mark.unit
def test_collect_failures_tolerates_csvs_without_answer_source_column(tmp_path):
    """Older run artifacts predate `answer_source_used`; they must still be
    readable, falling back to a provider-only lane key."""
    from nimble_benchmark.insights import collect_failures

    run_dir = tmp_path / "run_20260808_120000_x"
    run_dir.mkdir()
    pd.DataFrame(
        [
            {
                "provider": "exa_search_auto",
                "request_status": "failed_after_retries",
                "request_error": "402 Payment Required",
            },
        ]
    ).to_csv(run_dir / "dataset_simpleqa_raw_results_exa_search_auto.csv", index=False)

    lanes = collect_failures(run_dir)

    assert len(lanes) == 1
    assert lanes[0].lane_key == "exa_search_auto"
    assert lanes[0].answer_source is None

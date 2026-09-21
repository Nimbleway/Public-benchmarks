"""Human and machine-readable report writers.

Reads the run summary CSV (``analyzed_results.csv``) and the significance CSV
(``significance.csv``) and writes ``run.md`` + ``run.json``.

``run.md``'s ``## Headline`` section is a single table holding every lane in
the run, ranked on Accuracy, with only provider / n / accuracy / latency / cost
rendered. Cost is
an estimate rather than a measurement: the lane's request count times a static
per-query vendor list price from :mod:`nimble_benchmark.price_list`.
Everything
dropped from the table (``response_kind``, ``answer_source``, ``ndcg@10``,
``recall@10 LLM``, ``fail rate``) is still in ``analyzed_results.csv`` and
``run.json``. The Caveat section spells out the consequence: without
``answer_source`` on the page, an Accuracy column that mixed model-authored
answers (``api``) with eval-side syntheses (``synth``) would not say so. Every
shipped lane is ``synth`` today, so the column is uniformly sourced.
"""

from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path

import pandas as pd

from nimble_benchmark.metrics.latency import KNOWN_SERVER_TIMING_STAGES
from nimble_benchmark.price_list import (
    PRICE_LIST_AS_OF,
    format_cost,
    search_depth_from_configs,
)
from nimble_benchmark.sampler_config import read_sampler_configs, render_sampler_config_table

SUMMARY_FILENAME = "analyzed_results.csv"
SIGNIFICANCE_FILENAME = "significance.csv"


def write_run_md(*, run_dir: Path, args: dict, started_at: datetime, finished_at: datetime) -> None:
    aggregate = _load_summary(run_dir / SUMMARY_FILENAME)
    significance = _load_significance(run_dir / SIGNIFICANCE_FILENAME)
    baseline = _resolve_baseline_key(significance)
    synthesis_model = args.get("synthesis_model") or "—"
    # ``skipped`` rather than the configured id when --skip-llm-judge was set,
    # so the header never names a judge that never ran.
    judge_model = "skipped" if args.get("skip_llm_judge") else (args.get("judge_model") or "—")
    preflight_skipped = args.get("preflight_skipped_samplers") or []

    lines: list[str] = [
        f"# Nimble Search Evals - {run_dir.name}\n\n",
        f"**Run type:** benchmark  **Dataset:** {args['dataset']}  **Limit:** {args['limit']}  ",
        f"**Synthesis model:** `{synthesis_model}`  ",
        f"**Judge model:** `{judge_model}`\n\n",
    ]
    if preflight_skipped:
        # Surface lenient-lane exclusions at the top of run.md so the
        # dispatcher notices a missing row in the leaderboard (e.g. the
        # lane's upstream was flaky). The skipped sampler is
        # also recorded in run.json under ``args.preflight_skipped_samplers``
        # for machine-readable downstream consumers.
        skipped_inline = ", ".join(f"`{name}`" for name in preflight_skipped)
        lines.append(
            f"> [!WARNING]\n"
            f"> **Excluded by preflight:** {skipped_inline} -- upstream returned "
            f"a transient 5xx / connection error during preflight, so the lane "
            f"was dropped from this run. Re-dispatch the workflow once the "
            f"upstream recovers to include it.\n\n"
        )

    # Reliability gate: lanes the analyzer dropped from the ranking
    # because they failed too often (>``--max-failure-rate``) or returned
    # too few successful rows (<``--min-successful-problems``). Rendered
    # ABOVE the headline so a reader can't miss why the table is missing
    # a familiar provider. The lane is still present in the analyzed CSVs
    # with its (success-subset) metrics if a deeper drill-down is needed.
    _append_excluded_lanes_section(lines=lines, aggregate=aggregate)

    # Headline section: one table for every lane in the run.
    if not aggregate.empty:
        lines.append("## Headline\n\n")
        _append_headline_table(
            lines=lines,
            aggregate=aggregate,
            significance=significance,
            baseline_key=baseline,
            sampler_configs=read_sampler_configs(run_dir),
        )
        if baseline is not None:
            lines.append(
                f"\n*◯ significance baseline: `{baseline}`. "
                "★ significantly better than the baseline on this metric "
                "(Bonferroni-corrected p < 0.05, paired Student's t-test).*\n\n"
            )

    _append_latency_breakdown_section(lines=lines, aggregate=aggregate)

    _append_wrong_answer_samples(lines=lines, run_dir=run_dir)

    sampler_config_table = render_sampler_config_table(run_dir)
    lines.append("\n## Sampler Configuration\n\n")
    lines.append(
        "Per-provider knobs read from `sampler_config_<name>.json`. "
        "`(default)` means the provider's own server picked the value.\n\n"
    )
    lines.append(sampler_config_table)
    lines.append("\n\n## Caveat\n\n")
    lines.append(
        "**Accuracy measures retrieval, not a provider's own answer product.** "
        "Every lane in this table is a `/search` lane (`answer_source=synth`): it "
        "is graded off an answer the runner synthesizes eval-side from that lane's "
        "top chunks using `--synthesis-model`, so the column reports the retrieval "
        "quality reaching one fixed synthesizer. Should an answer lane "
        "(`answer_source=api`, graded on the model's own answer) ever share this "
        "table, an `api` row beating a `synth` row is not a clean like-for-like "
        "win -- read `answer_source` in `analyzed_results.csv` to tell them apart. "
        "The retrieval metrics that *are* measured identically across every lane "
        "(NDCG@10, Recall@10) are in `analyzed_results.csv`.\n"
    )
    lines.append(
        "\n**Accuracy counts a non-answer as a miss.** The denominator is every row "
        "whose request succeeded, so a lane that returns 200 with nothing usable in it "
        "-- a refusal, or zero results to synthesize from -- is graded "
        "`is_not_attempted` and scores 0 for that question rather than dropping out of "
        "the denominator. Only rows whose request never landed (`not_evaluated`) are "
        "excluded; those are counted by `failure_rate`. Upstream simple-evals instead "
        "reports not-attempted as a separate third bucket and divides by attempts only, "
        "so **this column is not directly comparable to published SimpleQA accuracy "
        "figures** even though the grader prompt is upstream-verbatim. The per-row "
        "`evaluation_result` labels are unchanged, so the upstream split can be "
        "recomputed from the raw CSVs.\n"
    )
    lines.append(f"\nStarted: {started_at.isoformat()}  Finished: {finished_at.isoformat()}\n")
    (run_dir / "run.md").write_text("".join(lines))


def _append_headline_table(
    *,
    lines: list[str],
    aggregate: pd.DataFrame,
    significance: pd.DataFrame,
    baseline_key: str | None,
    sampler_configs: dict[str, dict] | None = None,
) -> None:
    """The run's single headline table, ranked on accuracy.

    Deliberately narrow: provider, n, accuracy, latency, cost. ``response_kind``
    / ``answer_source`` / ``ndcg@10`` / ``recall@10 LLM`` / ``fail rate`` are
    all still in ``analyzed_results.csv`` and ``run.json``, just not rendered
    here.

    Three consequences worth knowing when reading the table. Accuracy is not
    self-describing without ``answer_source``: ``synth`` rows are graded off the
    eval-side synthesized answer, ``api`` rows off the model's own (see the
    Caveat section). A partially-failed lane is only visible via the
    ``n`` column's ``ok/total`` form, not an explicit rate. And ``cost`` is an
    estimate rather than a measurement -- the lane's request count times a
    static per-query list price from :mod:`nimble_benchmark.price_list`, so
    it scales with ``--limit`` and ``—`` means the lane has no per-query list
    price (see the cost note below the table).

    ``sampler_configs`` is only consulted to price the depth-configurable
    ``nimble_search`` lane at the depth it actually ran at.
    """
    significance_lookup = _build_significance_lookup(significance)
    include_parse_errors = _has_parse_errors(aggregate)
    header = "| provider | n | accuracy | "
    separator = "|---|---:|---:|"
    if include_parse_errors:
        header += "parse errors | "
        separator += "---:|"
    header += "p50 ms | p95 ms | cost |\n"
    separator += "---:|---:|---:|\n"
    lines.append(header)
    lines.append(separator)
    for _, row in aggregate.sort_values("accuracy_score", ascending=False, na_position="last").iterrows():
        system_key = _row_system_key(row)
        is_baseline = baseline_key is not None and system_key == baseline_key
        acc_marker = _star_for(significance_lookup, "accuracy_score", system_key)
        provider_cell = _provider_cell(row, is_baseline=is_baseline)
        line = (
            f"| {provider_cell} | {_format_n_cell(row)} | "
            f"{_format_accuracy(row.get('accuracy_score'), marker=acc_marker)} | "
        )
        if include_parse_errors:
            parse_error_count = row.get("citation_parse_error_count") or 0
            line += f"{parse_error_count:.0f} | "
        provider_id = str(row.get("provider") or "")
        cost_cell = format_cost(
            provider_id,
            row.get("problem_count"),
            search_depth=search_depth_from_configs(provider_id, sampler_configs),
        )
        line += (
            f"{_format_latency_cell(row.get('provider_response_time_ms_p50'))} | "
            f"{_format_latency_cell(row.get('provider_response_time_ms_p95'))} | "
            f"{cost_cell} |\n"
        )
        lines.append(line)
    lines.append(
        f"\n*cost: this run's spend at list price -- the lane's request count (`n`'s total) times the "
        f"vendor's published per-query rate for that product tier (price snapshot of {PRICE_LIST_AS_OF}). "
        f"It scales with the run size, so it compares down the column but not across runs of different "
        f"`n`; quote the per-query rate in `price_list.py` for that. Retry attempts are not counted and "
        f"failed requests are, and the figure excludes extra-result, page-summary, and volume-discount "
        f"pricing -- an estimate, not an invoice. `—` means the lane has no per-query list price (the "
        f"native-LLM answer lane is billed on tokens; its per-row token counts are in "
        f"`analyzed_results.csv`).*\n"
    )


WRONG_ANSWER_SAMPLES_PER_LANE = 5
_SAMPLE_CELL_MAX_CHARS = 120


def _append_wrong_answer_samples(*, lines: list[str], run_dir: Path) -> None:
    """Append up to N graded-incorrect examples per (provider, answer_source).

    Reads the per-provider raw CSVs rather than the summaries because the
    summaries do not carry per-row answers. The section exists to make failure
    modes inspectable from run.md alone -- e.g. telling apart "retrieved junk"
    from "retrieved fine but synthesized the wrong fact" -- without opening the
    CSVs. Only ``is_incorrect`` rows qualify; ``is_not_attempted`` is a refusal,
    not a wrong answer, and mixing them would make providers with cautious
    synthesis look like they hallucinate.
    """
    raw_files = sorted(run_dir.glob("*_raw_results_*.csv"))
    if not raw_files:
        return
    sections: list[str] = []
    for path in raw_files:
        try:
            df = pd.read_csv(path)
        except Exception:
            continue
        required = {"provider", "answer_source_used", "evaluation_result", "query", "ground_truth", "generated_answer"}
        if not required.issubset(df.columns):
            continue
        wrong = df[df["evaluation_result"] == "is_incorrect"]
        for (provider, source), group in wrong.groupby(["provider", "answer_source_used"], sort=True):
            sample = group.head(WRONG_ANSWER_SAMPLES_PER_LANE)
            sections.append(f"\n### {provider} ({source}) — {len(group)} incorrect\n\n")
            sections.append("| Question | Expected | Got |\n| --- | --- | --- |\n")
            for _, row in sample.iterrows():
                cells = (
                    _sample_cell(row["query"]),
                    _sample_cell(row["ground_truth"]),
                    _sample_cell(row["generated_answer"]),
                )
                sections.append("| " + " | ".join(cells) + " |\n")
    if not sections:
        return
    lines.append("\n## Wrong-answer samples\n\n")
    lines.append(
        f"Up to {WRONG_ANSWER_SAMPLES_PER_LANE} graded-incorrect rows per lane "
        "(first by row order, not cherry-picked). Refusals (`is_not_attempted`) "
        "are excluded — they lower accuracy but are not wrong answers.\n"
    )
    lines.extend(sections)


def _sample_cell(value) -> str:
    text = "" if value is None or (isinstance(value, float) and math.isnan(value)) else str(value)
    text = " ".join(text.split())  # collapse newlines/whitespace that would break the MD table
    if len(text) > _SAMPLE_CELL_MAX_CHARS:
        text = text[: _SAMPLE_CELL_MAX_CHARS - 1] + "…"
    return text.replace("|", "\\|")


def _append_excluded_lanes_section(*, lines: list[str], aggregate: pd.DataFrame) -> None:
    """Render a warning section listing lanes the reliability gate dropped.

    The gate runs in :func:`nimble_benchmark.analyzer.aggregate_run` and
    stamps ``excluded_reason`` on every row. Rows are still emitted in the
    analyzed CSV (with metrics computed over the survivor subset) so a
    reader can drill into the raw artifact if they want; here we just surface
    the headline diagnostic so the missing-from-leaderboard signal isn't
    silent.
    """
    if aggregate.empty or "excluded_reason" not in aggregate.columns:
        return
    excluded = aggregate[aggregate["excluded_reason"].notna() & (aggregate["excluded_reason"].astype(str) != "")]
    if excluded.empty:
        return
    lines.append("> [!WARNING]\n")
    lines.append(
        "> **Excluded by reliability gate** -- the lane(s) below are present "
        "in the analyzed CSV with their headline metrics computed over the "
        "successful subset, but they were dropped from the leaderboard "
        "ranking and from the significance baseline pool because they "
        "failed too often or returned too few successful rows. Investigate "
        "the failure mode before treating their metric numbers as comparable "
        "to fully-populated lanes.\n"
    )
    lines.append("> \n")
    lines.append("> | provider | response_kind | answer_source | total | successful | fail rate | reason |\n")
    lines.append("> |---|---|---|---:|---:|---:|---|\n")
    for _, row in excluded.sort_values(["failure_rate", "provider"], ascending=[False, True]).iterrows():
        lines.append(
            "> | `{provider}` | {kind} | {source} | {total} | {ok} | {rate} | `{reason}` |\n".format(
                provider=row.get("provider") or "",
                kind=row.get("response_kind") or "",
                source=row.get("answer_source") or "",
                total=_format_problem_count(row.get("problem_count")),
                ok=_format_problem_count(row.get("successful_problem_count")),
                rate=_format_failure_rate(row.get("failure_rate")),
                reason=row.get("excluded_reason") or "",
            )
        )
    lines.append("\n")


def _provider_cell(row, *, is_baseline: bool) -> str:
    """Render the provider cell, suffixing ``◯`` when this is the baseline
    and ``⚠`` when the lane was excluded by the reliability gate.

    The warning marker mirrors the dedicated "Excluded by reliability gate"
    section above the table so a reader scanning the row left-to-right has
    a quick visual cue that the metric numbers in this row are based on a
    truncated sample.
    """
    reason = row.get("excluded_reason") if hasattr(row, "get") else None
    marker = ""
    if is_baseline:
        marker += " ◯"
    if reason and not (isinstance(reason, float) and math.isnan(reason)) and str(reason).strip():
        marker += " ⚠"
    return f"{row['provider']}{marker}"


def _format_failure_rate(value) -> str:
    rate = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    if pd.isna(rate):
        return "—"
    return f"{rate * 100:.1f}%"


def _format_n_cell(row) -> str:
    """``successful / total`` count cell.

    Falls back to just ``total`` when the new reliability columns are
    absent (i.e. an older run was re-rendered through this code path).
    """
    total = pd.to_numeric(pd.Series([row.get("problem_count")]), errors="coerce").iloc[0]
    successful = pd.to_numeric(pd.Series([row.get("successful_problem_count")]), errors="coerce").iloc[0]
    if pd.isna(total):
        return "—"
    total_str = f"{int(total)}"
    if pd.isna(successful):
        return total_str
    return f"{int(successful)}/{total_str}"


def _append_latency_breakdown_section(*, lines: list[str], aggregate: pd.DataFrame) -> None:
    """Render a per-stage latency attribution table.

    Pulls ``stage_<name>_ms_{mean,p50,p95}`` columns the analyzer rolled up
    from the sampler's ``Server-Timing`` response header; their sum is
    approximately ``internal_response_time_ms`` (server-side total).
    Third-party APIs do not emit ``Server-Timing`` so their stage cells render
    as ``—``. The section is omitted entirely when no row in the aggregate has
    any stage data populated.
    """
    if aggregate.empty:
        return
    combined = aggregate
    stage_columns = [
        f"stage_{stage}_ms_{stat}" for stage in KNOWN_SERVER_TIMING_STAGES for stat in ("mean", "p50", "p95")
    ]
    populated_stage_columns = [column for column in stage_columns if column in combined]
    if not populated_stage_columns:
        return
    populated = combined[populated_stage_columns].apply(pd.to_numeric, errors="coerce")
    if not populated.notna().any().any():
        return

    lines.append("\n## Latency by stage (ms)\n\n")
    lines.append(
        "Per-stage attribution parsed from each sampler's `Server-Timing` "
        "response header: `plan` (query planning), `search` (SERP / livecrawl "
        "content fetch), and `synthesis`; their sum approximately equals "
        "`server total` (`internal_response_time_ms`). Third-party APIs do not "
        "emit `Server-Timing`, so their stage cells are `—`. `client total` is "
        "the eval harness's wall-clock measurement. Lower is better.\n\n"
    )
    header_cells = ["provider", "answer_source"]
    align_cells = ["---", "---"]
    for stage in KNOWN_SERVER_TIMING_STAGES:
        header_cells.extend([f"{stage} mean", f"{stage} p50", f"{stage} p95"])
        align_cells.extend(["---:"] * 3)
    # Three nested totals, innermost first: what the server attributed to
    # itself, the round trip we measured around it, and the harness wall clock
    # (round trip + our limiter queue wait + retry backoff). On a throttled
    # lane the last two diverge by seconds, and only the middle one is the
    # provider's latency.
    header_cells.extend(["server total p50", "provider p50", "client total p50", "n"])
    align_cells.extend(["---:", "---:", "---:", "---:"])
    lines.append("| " + " | ".join(header_cells) + " |\n")
    lines.append("|" + "|".join(align_cells) + "|\n")

    sort_column = "stage_synthesis_ms_p50" if "stage_synthesis_ms_p50" in combined else "provider_response_time_ms_p50"
    sorted_frame = combined.sort_values(sort_column, ascending=True, na_position="last")
    for _, row in sorted_frame.iterrows():
        cells = [str(row.get("provider") or ""), str(row.get("answer_source") or "")]
        for stage in KNOWN_SERVER_TIMING_STAGES:
            for stat in ("mean", "p50", "p95"):
                cells.append(_format_latency_cell(row.get(f"stage_{stage}_ms_{stat}")))
        cells.append(_format_latency_cell(row.get("internal_response_time_ms_p50")))
        cells.append(_format_latency_cell(row.get("provider_response_time_ms_p50")))
        cells.append(_format_latency_cell(row.get("request_response_time_ms_p50")))
        cells.append(_format_problem_count(row.get("problem_count")))
        lines.append("| " + " | ".join(cells) + " |\n")
    lines.append("\n")


def _format_latency_cell(value) -> str:
    numeric = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    if pd.isna(numeric):
        return "—"
    return f"{numeric:.0f}"


def _format_problem_count(value) -> str:
    numeric = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    if pd.isna(numeric):
        return "—"
    return f"{int(numeric)}"


def _has_parse_errors(aggregate: pd.DataFrame) -> bool:
    if "citation_parse_error_count" not in aggregate:
        return False
    return pd.to_numeric(aggregate["citation_parse_error_count"], errors="coerce").fillna(0).sum() > 0


def _format_accuracy(value, *, marker: str = "") -> str:
    accuracy = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    if pd.isna(accuracy):
        return "—"
    return f"{accuracy * 100:.1f}%{marker}"


def write_run_json(*, run_dir: Path, args: dict, started_at: datetime, finished_at: datetime) -> None:
    aggregate = _load_summary(run_dir / SUMMARY_FILENAME)
    significance = _load_significance(run_dir / SIGNIFICANCE_FILENAME)
    payload = {
        "run_dir": run_dir.name,
        "args": args,
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "duration_seconds": (finished_at - started_at).total_seconds(),
        "rows": aggregate.to_dict(orient="records"),
        "significance_baseline": _resolve_baseline_key(significance),
        "significance": significance.to_dict(orient="records"),
    }
    (run_dir / "run.json").write_text(json.dumps(payload, indent=2, default=str))


FAILURES_FILENAME = "failures.json"
_FAILURE_CHUNK_DESC_MAX_CHARS = 400

# ``failure_kind`` values, coarsest split first:
#   request_failure  -- the provider call itself died (after retries).
#   refusal          -- provider/synth answered "I don't know" (is_not_attempted).
#   wrong_answer     -- an answer was given and graded incorrect.
#
# ``retrieval_diagnosis`` values (refusal/wrong_answer rows only):
#   empty_retrieval               -- no chunks came back at all.
#   gold_in_top2_generation_miss  -- gold URL ranked 1-2; retrieval did its job,
#                                    the answer step still missed. Synth/answer bug.
#   gold_found_ranked_low         -- gold URL present but deeper in the list.
#                                    Ranking problem; hurts NDCG, may starve synth.
#   gold_absent_relevant_content  -- gold URL missing but UMBRELA graded some
#                                    chunks relevant; answered from alternates.
#   bad_retrieval                 -- gold absent AND judged relevance ~zero
#                                    (e.g. lite's dictionary.com/instagram rows).
#   no_metrics                    -- URL/LLM metrics unavailable for the row.


def _diagnose_retrieval(row: pd.Series, chunks: list[dict]) -> str:
    if not chunks:
        return "empty_retrieval"
    hit = pd.to_numeric(pd.Series([row.get("hit_at_10")]), errors="coerce").iloc[0]
    mrr = pd.to_numeric(pd.Series([row.get("mrr")]), errors="coerce").iloc[0]
    llm_recall = pd.to_numeric(pd.Series([row.get("recall_at_10_llm")]), errors="coerce").iloc[0]
    if pd.isna(hit):
        return "no_metrics"
    if hit >= 1:
        if not pd.isna(mrr) and mrr >= 0.5:
            return "gold_in_top2_generation_miss"
        return "gold_found_ranked_low"
    if not pd.isna(llm_recall) and llm_recall >= 0.3:
        return "gold_absent_relevant_content"
    return "bad_retrieval"


def write_failures_json(*, run_dir: Path) -> None:
    """Write every failed row across all raw CSVs to ``failures.json``.

    A "failure" is a request error, a refusal, or a graded-incorrect answer.
    Each entry carries the full retrieval context (URLs, titles, truncated
    passages, per-chunk UMBRELA grades) plus a machine-assigned
    ``failure_kind`` + ``retrieval_diagnosis`` so root-cause buckets can be
    counted with one ``jq`` and re-classified offline without rerunning
    anything. ``is_not_evaluated`` rows with an ok request are skip-grader
    artifacts, not failures, and are excluded.
    """
    failures: list[dict] = []
    for path in sorted(run_dir.glob("*_raw_results_*.csv")):
        try:
            df = pd.read_csv(path)
        except Exception:
            continue
        if "evaluation_result" not in df.columns:
            continue
        for _, row in df.iterrows():
            request_failed = str(row.get("request_status", "ok")) != "ok"
            verdict = str(row.get("evaluation_result", ""))
            if not request_failed and verdict not in {"is_incorrect", "is_not_attempted"}:
                continue
            try:
                chunks = json.loads(row["chunks_json"]) if pd.notna(row.get("chunks_json")) else []
            except (TypeError, ValueError):
                chunks = []
            try:
                grades = json.loads(row["llm_grades_json"]) if pd.notna(row.get("llm_grades_json")) else []
            except (TypeError, ValueError):
                grades = []
            if request_failed:
                kind, diagnosis = "request_failure", None
            else:
                kind = "refusal" if verdict == "is_not_attempted" else "wrong_answer"
                diagnosis = _diagnose_retrieval(row, chunks)
            failures.append(
                {
                    "provider": row.get("provider"),
                    "answer_source": row.get("answer_source_used"),
                    "failure_kind": kind,
                    "retrieval_diagnosis": diagnosis,
                    "query": row.get("query"),
                    "ground_truth": row.get("ground_truth"),
                    "generated_answer": _none_if_nan(row.get("generated_answer")),
                    "evaluation_result": verdict or None,
                    "request_error": _truncate(_none_if_nan(row.get("request_error")), 1000),
                    "metrics": {
                        key: _float_or_none(row.get(key))
                        for key in ("mrr", "ndcg_at_10", "hit_at_10", "recall_at_10", "recall_at_10_llm")
                    },
                    "llm_grades": grades,
                    "chunks": [
                        {
                            "position": chunk.get("position"),
                            "url": chunk.get("url"),
                            "title": chunk.get("title"),
                            "description": _truncate(chunk.get("description"), _FAILURE_CHUNK_DESC_MAX_CHARS),
                        }
                        for chunk in chunks
                    ],
                    # The round trip, not the harness wall clock: for a failed
                    # row what you want to know is how long the upstream took
                    # before it failed, not how long the row queued first.
                    "latency_ms": _float_or_none(row.get("provider_response_time_ms")),
                    "answer_type": _none_if_nan(row.get("answer_type")),
                    "topic": _none_if_nan(row.get("topic")),
                }
            )

    counts: dict[str, dict] = {}
    for entry in failures:
        lane = f"{entry['provider']}/{entry['answer_source']}"
        lane_counts = counts.setdefault(lane, {})
        key = entry["failure_kind"] + (f":{entry['retrieval_diagnosis']}" if entry["retrieval_diagnosis"] else "")
        lane_counts[key] = lane_counts.get(key, 0) + 1

    payload = {
        "schema_version": 1,
        "run_dir": run_dir.name,
        "total_failures": len(failures),
        "counts_by_lane": dict(sorted(counts.items())),
        "failures": failures,
    }
    (run_dir / FAILURES_FILENAME).write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str))


def _none_if_nan(value):
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    return value


def _truncate(value, max_chars: int):
    if value is None:
        return None
    text = str(value)
    return text if len(text) <= max_chars else text[: max_chars - 1] + "…"


def _float_or_none(value):
    numeric = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    return None if pd.isna(numeric) else float(numeric)


def _load_summary(path: Path) -> pd.DataFrame:
    """Read the summary CSV. Returns an empty frame when absent or empty."""
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_csv(path)
    return df if not df.empty else pd.DataFrame()


def _load_significance(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_csv(path)
    return df if not df.empty else pd.DataFrame()


def _build_significance_lookup(significance: pd.DataFrame) -> dict[tuple[str, str], dict]:
    """Index significance rows by (metric, system_key) for O(1) headline marking."""
    if significance.empty:
        return {}
    required = {"metric", "system", "significant"}
    if not required.issubset(significance.columns):
        return {}
    return {(str(row["metric"]), str(row["system"])): row.to_dict() for _, row in significance.iterrows()}


def _resolve_baseline_key(significance: pd.DataFrame) -> str | None:
    if significance.empty or "baseline" not in significance.columns:
        return None
    baselines = significance["baseline"].dropna().astype(str).unique()
    return baselines[0] if len(baselines) else None


def _row_system_key(row) -> str:
    """Build the slash-joined key that `metrics.significance` emits."""
    return "/".join(
        "" if value is None or (isinstance(value, float) and math.isnan(value)) else str(value)
        for value in (row["provider"], row.get("response_kind"), row.get("answer_source"))
    )


def _star_for(lookup: dict[tuple[str, str], dict], metric: str, system_key: str) -> str:
    entry = lookup.get((metric, system_key))
    if not entry:
        return ""
    return "★" if bool(entry.get("significant")) else ""

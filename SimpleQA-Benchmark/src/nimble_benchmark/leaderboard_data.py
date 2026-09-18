"""Data-preparation layer for the leaderboard.

Pure: reads run-directory artifacts (``analyzed_results.csv``, ``run.json``,
``sampler_config_*.json``) and returns typed dataclasses with no formatting
applied. All HTML / Markdown / MDX rendering lives in
:mod:`nimble_benchmark.leaderboard_render`.

The dataclasses are JSON-round-trippable via :func:`save_snapshot` /
:func:`load_snapshot` so the preview tool can re-render templates against a
cached snapshot in milliseconds without re-reading the source CSVs.

Schema version 6 (this revision):
  * The answer/search kind split is gone. ``headline_rows`` +
    ``baseline_system`` replace ``answer_rows`` / ``search_rows`` /
    ``answer_baseline_system`` / ``search_baseline_system``: every lane,
    including the native-LLM answer lanes, renders in one table against one
    significance baseline.
  * ``per_answer_type`` / ``per_topic`` are absent: the accuracy-by-answer-type
    and accuracy-by-topic breakdowns are not rendered on the board. The
    underlying ``analyzed_by_*.csv`` artifacts are still written by the
    analyzer and still listed under Source Artifacts.

Schema version 3:
  * ``HeadlineRow`` gained ``accuracy_score_significant`` and
    ``SystemSignificance`` matching ``accuracy_score_*`` fields so the accuracy
    column can carry a ★ marker like NDCG/Recall already do.
"""

from __future__ import annotations

import csv
import json
import os
import shlex
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Schema version bumps when the on-disk snapshot shape changes incompatibly.
# v1: legacy headline_rows + single baseline.
# v2: answer_rows + search_rows + per-table baselines.
# v3: accuracy_score_significant on HeadlineRow + accuracy_score_* on
#     SystemSignificance.
# v4: answer_rows / answer_baseline_system removed (search-only harness).
# v5: answer_rows / answer_baseline_system restored.
# v6: kind split collapsed to headline_rows + baseline_system; breakdown
#     sections (per_answer_type / per_topic) dropped (this revision).
# 7: the headline latency field moved from ``request_response_time_ms_p50``
# (harness wall clock, which carried our limiter queue wait) to
# ``provider_response_time_ms_p50`` (the upstream round trip). A v6 snapshot's
# latency is not comparable to a v7 one, so it must fail to load rather than
# render silently.
SNAPSHOT_SCHEMA_VERSION = 7

REQUIRED_HEADLINE_COLUMNS = {
    "provider",
    "response_kind",
    "answer_source",
    "accuracy_score",
    # NDCG@10 uses the URL-binary judge: a citation is relevant iff its URL
    # appears in the dataset's gold reference set. Fully reproducible (no LLM
    # in the loop) and rewards exact source matches.
    "ndcg_at_10",
    # Recall@10 uses the UMBRELA LLM-judged signal: relevance is decided by an
    # LLM on the citation passage itself, so we don't penalize a provider that
    # cites a different-but-equally-good source than the gold URL.
    "recall_at_10_llm",
    "provider_response_time_ms_p50",
    "problem_count",
}

SUMMARY_CSV_FILENAME = "analyzed_results.csv"
SIGNIFICANCE_FILENAME = "significance.csv"


@dataclass(frozen=True)
class SystemSignificance:
    """Significance result for a single `(provider, response_kind, answer_source)` system.

    Carries the per-metric `significant`/p-value/delta tuple so the leaderboard
    can expand a `<details>` block under each provider row with full per-system
    detail without re-reading the significance CSVs.

    ``accuracy_score_*`` mirrors the NDCG/Recall fields so the accuracy column
    can stamp the same ★ marker. Default-free here (the dataclass is frozen and explicit) but defaults to
    ``False`` / ``None`` in :func:`_significance_by_system` when a system has
    no ``accuracy_score`` row in the significance CSV.
    """

    system: str
    response_kind: str
    answer_source: str
    ndcg_at_10_significant: bool
    ndcg_at_10_p_bonferroni: float | None
    ndcg_at_10_delta: float | None
    recall_at_10_llm_significant: bool
    recall_at_10_llm_p_bonferroni: float | None
    recall_at_10_llm_delta: float | None
    accuracy_score_significant: bool = False
    accuracy_score_p_bonferroni: float | None = None
    accuracy_score_delta: float | None = None


@dataclass(frozen=True)
class HeadlineRow:
    """One ``(provider, response_kind, answer_source)`` system's metrics.

    No weighted averaging: every row in this dataclass corresponds to exactly
    one row in ``analyzed_results.csv``.

    Reliability fields (``successful_problem_count`` / ``failure_count`` /
    ``failure_rate`` / ``excluded_reason``) come from the analyzer's
    reliability gate (see :mod:`nimble_benchmark.analyzer`). They are
    additive and default to ``None`` / ``0`` so a snapshot written by an
    older analyzer load-trips correctly through :meth:`from_dict` without
    a schema-version bump.
    """

    provider_id: str
    response_kind: str
    answer_source: str
    accuracy_score: float | None
    ndcg_at_10: float | None
    recall_at_10_llm: float | None
    usage_input_tokens_mean: float | None
    usage_output_tokens_mean: float | None
    provider_response_time_ms_p50: float | None
    problem_count: float
    is_baseline: bool = False
    ndcg_at_10_significant: bool = False
    recall_at_10_llm_significant: bool = False
    # Lets the accuracy column carry ★ the same way NDCG/Recall do. Populated
    # from the ``accuracy_score`` metric row in the significance CSV by
    # :func:`_system_rows_from_csv`.
    accuracy_score_significant: bool = False
    system_significance: SystemSignificance | None = None
    # Reliability gate axis. ``successful_problem_count`` is the number of
    # ok-status rows the metrics were averaged over (always <=
    # ``problem_count``); ``failure_rate`` is the 0..1 fraction the lane
    # failed. ``excluded_reason`` is non-empty when the analyzer dropped the
    # lane from the leaderboard ranking (either too few successes or too
    # many failures). The renderer uses these to surface a ⚠ marker on
    # excluded lanes and an optional fail-rate column in the table.
    successful_problem_count: float | None = None
    failure_count: float | None = None
    failure_rate: float | None = None
    excluded_reason: str | None = None


@dataclass(frozen=True)
class SourceArtifact:
    """One row in the leaderboard's "Source Artifacts" table.

    ``path`` is always the run-local filesystem path so the table stays
    useful when the leaderboard is generated locally. ``url`` is optional
    and only populated in CI runs (driven by the
    ``LEADERBOARD_WORKFLOW_RUN_URL`` env var) -- when present the renderer
    turns the path into a clickable link that drops the viewer at the
    workflow-run's Artifacts panel where the underlying file is
    downloadable.
    """

    name: str
    path: str
    url: str | None = None


@dataclass(frozen=True)
class LeaderboardData:
    """Everything the renderer needs. Serializable via :func:`save_snapshot`."""

    run_name: str
    run_dir: str
    generated_at: str
    synthesis_model: str
    total_rows: int
    headline_rows: list[HeadlineRow]
    sampler_configs: dict[str, dict[str, Any]]
    reproduce_command: str
    leaderboard_command: str
    source_artifacts: list[SourceArtifact] = field(default_factory=list)
    baseline_system: str | None = None
    significance_alpha: float | None = None
    schema_version: int = SNAPSHOT_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LeaderboardData:
        stored_version = int(data.get("schema_version", 0))
        if stored_version != SNAPSHOT_SCHEMA_VERSION:
            raise RuntimeError(
                f"snapshot schema version {stored_version} is not supported "
                f"(expected {SNAPSHOT_SCHEMA_VERSION}). Re-run "
                "`python -m nimble_benchmark.leaderboard_preview snapshot "
                "<run_dir>` against a fresh run to regenerate the fixture."
            )
        return cls(
            run_name=data["run_name"],
            run_dir=data["run_dir"],
            generated_at=data["generated_at"],
            synthesis_model=data["synthesis_model"],
            total_rows=data["total_rows"],
            headline_rows=[_headline_row_from_dict(row) for row in data["headline_rows"]],
            sampler_configs=data["sampler_configs"],
            reproduce_command=data["reproduce_command"],
            leaderboard_command=data["leaderboard_command"],
            source_artifacts=[SourceArtifact(**a) for a in data.get("source_artifacts", [])],
            baseline_system=data.get("baseline_system"),
            significance_alpha=data.get("significance_alpha"),
            schema_version=stored_version,
        )


def _headline_row_from_dict(data: dict[str, Any]) -> HeadlineRow:
    """Reconstruct a HeadlineRow from a snapshot dict."""
    significance_payload = data.get("system_significance")
    return HeadlineRow(
        provider_id=data["provider_id"],
        response_kind=data.get("response_kind", ""),
        answer_source=data.get("answer_source", ""),
        accuracy_score=data.get("accuracy_score"),
        ndcg_at_10=data.get("ndcg_at_10"),
        recall_at_10_llm=data.get("recall_at_10_llm"),
        usage_input_tokens_mean=data.get("usage_input_tokens_mean"),
        usage_output_tokens_mean=data.get("usage_output_tokens_mean"),
        provider_response_time_ms_p50=data.get("provider_response_time_ms_p50"),
        problem_count=data.get("problem_count", 0),
        is_baseline=bool(data.get("is_baseline", False)),
        ndcg_at_10_significant=bool(data.get("ndcg_at_10_significant", False)),
        recall_at_10_llm_significant=bool(data.get("recall_at_10_llm_significant", False)),
        accuracy_score_significant=bool(data.get("accuracy_score_significant", False)),
        system_significance=SystemSignificance(**significance_payload) if significance_payload else None,
        successful_problem_count=data.get("successful_problem_count"),
        failure_count=data.get("failure_count"),
        failure_rate=data.get("failure_rate"),
        excluded_reason=data.get("excluded_reason"),
    )


def prepare_leaderboard_data(
    run_dir: Path,
    *,
    generated_at: str | None = None,
) -> LeaderboardData:
    """Read every artifact in ``run_dir`` and return the structured payload.

    ``generated_at`` is injectable so callers (notably the preview tool and
    tests) can pin the timestamp instead of always taking ``datetime.now``.
    """
    run_dir = Path(run_dir)
    csv_rows = _read_summary_csv(run_dir / SUMMARY_CSV_FILENAME)
    if csv_rows is None:
        raise RuntimeError(f"Missing {SUMMARY_CSV_FILENAME} in {run_dir}; run nimble-eval/aggregate first.")

    run_payload = _read_run_json(run_dir / "run.json")

    significance_rows, baseline, alpha = _read_significance(run_dir / SIGNIFICANCE_FILENAME)

    headline_rows = _system_rows_from_csv(
        csv_rows,
        significance_rows=significance_rows,
        baseline_system=baseline,
    )

    total_rows = sum(int(_to_float(row.problem_count) or 0) for row in headline_rows)

    return LeaderboardData(
        run_name=run_dir.name,
        run_dir=str(run_dir),
        generated_at=generated_at or datetime.now(UTC).isoformat(timespec="seconds"),
        synthesis_model=_synthesis_model_from_run(run_payload),
        total_rows=total_rows,
        headline_rows=headline_rows,
        sampler_configs=_read_sampler_configs(run_dir),
        reproduce_command=_build_reproduce_command(run_payload),
        leaderboard_command=f"python -m nimble_benchmark.leaderboard {shlex.quote(str(run_dir))}",
        source_artifacts=_build_source_artifacts(run_dir),
        baseline_system=baseline,
        significance_alpha=alpha,
    )


def save_snapshot(data: LeaderboardData, path: Path) -> Path:
    """Write a JSON snapshot for the preview tool to consume."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def load_snapshot(path: Path) -> LeaderboardData:
    return LeaderboardData.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


# --- internals -------------------------------------------------------------


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as fp:
        return list(csv.DictReader(fp))


def _read_summary_csv(path: Path) -> list[dict[str, str]] | None:
    """Read the run summary CSV. Returns ``None`` when the file is absent so
    the caller can raise a targeted "run the analyzer first" error.

    Asserts column completeness against :data:`REQUIRED_HEADLINE_COLUMNS`
    when the file exists. Header-only empty files are valid (zero rows).
    """
    if not path.exists():
        return None
    with path.open(encoding="utf-8", newline="") as fp:
        reader = csv.DictReader(fp)
        fieldnames = set(reader.fieldnames or [])
        if fieldnames:
            missing_columns = sorted(REQUIRED_HEADLINE_COLUMNS - fieldnames)
            if missing_columns:
                raise RuntimeError(f"{path.name} is missing required columns: " + ", ".join(missing_columns))
        return list(reader)


def _read_run_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _read_sampler_configs(run_dir: Path) -> dict[str, dict[str, Any]]:
    """Lift the implementation from :mod:`sampler_config` but keep the call
    local here so the data layer doesn't depend on the renderer or the older
    plain-Markdown helper.
    """
    configs: dict[str, dict[str, Any]] = {}
    for path in sorted(Path(run_dir).glob("sampler_config_*.json")):
        sampler_name = path.stem.removeprefix("sampler_config_")
        try:
            configs[sampler_name] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
    return configs


def _synthesis_model_from_run(run_payload: dict[str, Any]) -> str:
    """Pull ``synthesis_model`` from the run's args, falling back gracefully for
    older runs that didn't capture it."""
    args = run_payload.get("args") if isinstance(run_payload, dict) else None
    if isinstance(args, dict):
        value = args.get("synthesis_model")
        if isinstance(value, str) and value.strip():
            return value
    return "—"


def _system_rows_from_csv(
    rows: list[dict[str, str]],
    *,
    significance_rows: list[dict[str, str]] | None = None,
    baseline_system: str | None = None,
) -> list[HeadlineRow]:
    """Build one ``HeadlineRow`` per CSV row (no aggregation).

    Significance markers (★) and the ``is_baseline`` flag are decorated from
    the significance CSV. Each row's ``system_significance`` carries
    the full per-metric details for the renderer's per-system breakdown
    section.
    """
    significance_by_system = _significance_by_system(significance_rows or [])

    out: list[HeadlineRow] = []
    for row in rows:
        provider = row.get("provider", "")
        response_kind = row.get("response_kind", "") or ""
        answer_source = row.get("answer_source", "") or ""
        system_key = f"{provider}/{response_kind}/{answer_source}"
        significance = significance_by_system.get(system_key)
        is_baseline = baseline_system is not None and system_key == baseline_system
        excluded_reason_raw = (row.get("excluded_reason") or "").strip()
        excluded_reason = excluded_reason_raw or None
        out.append(
            HeadlineRow(
                provider_id=provider,
                response_kind=response_kind,
                answer_source=answer_source,
                accuracy_score=_to_float(row.get("accuracy_score")),
                ndcg_at_10=_to_float(row.get("ndcg_at_10")),
                recall_at_10_llm=_to_float(row.get("recall_at_10_llm")),
                usage_input_tokens_mean=_to_float(row.get("usage_input_tokens_mean")),
                usage_output_tokens_mean=_to_float(row.get("usage_output_tokens_mean")),
                provider_response_time_ms_p50=_to_float(row.get("provider_response_time_ms_p50")),
                problem_count=_to_float(row.get("problem_count")) or 0,
                is_baseline=is_baseline,
                ndcg_at_10_significant=significance.ndcg_at_10_significant if significance else False,
                recall_at_10_llm_significant=significance.recall_at_10_llm_significant if significance else False,
                accuracy_score_significant=significance.accuracy_score_significant if significance else False,
                system_significance=significance,
                successful_problem_count=_to_float(row.get("successful_problem_count")),
                failure_count=_to_float(row.get("failure_count")),
                failure_rate=_to_float(row.get("failure_rate")),
                excluded_reason=excluded_reason,
            )
        )
    return out


def _read_significance(path: Path) -> tuple[list[dict[str, str]], str | None, float | None]:
    """Read the significance CSV if it exists.

    Returns (rows, baseline_system, alpha). When the file is absent (fewer
    than two comparable lanes), returns ``([], None, None)``.
    """
    if not path.exists():
        return [], None, None
    rows = _read_csv(path)
    if not rows:
        return [], None, None
    baselines = {row.get("baseline") for row in rows if row.get("baseline")}
    baseline_system = next(iter(baselines)) if len(baselines) == 1 else None
    alpha_values = {_to_float(row.get("alpha")) for row in rows if row.get("alpha")}
    alpha = next(iter(alpha_values)) if len(alpha_values) == 1 else None
    return rows, baseline_system, alpha


def _significance_by_system(
    significance_rows: list[dict[str, str]],
) -> dict[str, SystemSignificance]:
    """Pivot per-(metric, system) rows into one :class:`SystemSignificance`
    per system, keyed by the full ``provider/response_kind/answer_source``
    system string for direct lookup at row-decoration time.
    """
    by_system: dict[str, dict[str, dict[str, str]]] = defaultdict(dict)
    for row in significance_rows:
        system = row.get("system")
        metric = row.get("metric")
        if not system or not metric:
            continue
        by_system[system][metric] = row

    out: dict[str, SystemSignificance] = {}
    for system_key, metric_rows in by_system.items():
        parts = system_key.split("/")
        response_kind = parts[1] if len(parts) > 1 else ""
        answer_source = parts[2] if len(parts) > 2 else ""
        ndcg_row = metric_rows.get("ndcg_at_10", {})
        recall_row = metric_rows.get("recall_at_10_llm", {})
        accuracy_row = metric_rows.get("accuracy_score", {})
        out[system_key] = SystemSignificance(
            system=system_key,
            response_kind=response_kind,
            answer_source=answer_source,
            ndcg_at_10_significant=_truthy(ndcg_row.get("significant")),
            ndcg_at_10_p_bonferroni=_to_float(ndcg_row.get("p_bonferroni")),
            ndcg_at_10_delta=_to_float(ndcg_row.get("mean_delta")),
            recall_at_10_llm_significant=_truthy(recall_row.get("significant")),
            recall_at_10_llm_p_bonferroni=_to_float(recall_row.get("p_bonferroni")),
            recall_at_10_llm_delta=_to_float(recall_row.get("mean_delta")),
            accuracy_score_significant=_truthy(accuracy_row.get("significant")),
            accuracy_score_p_bonferroni=_to_float(accuracy_row.get("p_bonferroni")),
            accuracy_score_delta=_to_float(accuracy_row.get("mean_delta")),
        )
    return out


def _truthy(value: Any) -> bool:
    """CSV booleans round-trip as the strings "True"/"False"; normalize."""
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"true", "1", "yes"}


def _build_reproduce_command(run_payload: dict[str, Any]) -> str:
    args = run_payload.get("args")
    if not isinstance(args, dict):
        return ""

    command = ["uv", "run", "nimble-eval"]
    _append_arg(command, "--dataset", args.get("dataset"))
    _append_arg(command, "--limit", args.get("limit"))
    _append_arg(command, "--random-state", args.get("random_state"))
    samplers = args.get("samplers") or args.get("expanded_samplers")
    if isinstance(samplers, list) and samplers:
        command.append("--samplers")
        command.extend(str(sampler) for sampler in samplers)
    _append_arg(command, "--max-concurrent-tasks", args.get("max_concurrent_tasks"))
    _append_arg(command, "--synthesis-model", args.get("synthesis_model"))
    _append_arg(command, "--grader-model", args.get("grader_model"))
    _append_arg(command, "--judge-model", args.get("judge_model"))
    _append_arg(command, "--judge-prompt-variant", args.get("judge_prompt_variant"))
    _append_arg(command, "--judge-relevance-threshold", args.get("judge_relevance_threshold"))
    _append_arg(command, "--results-dir", args.get("results_dir"))
    if args.get("skip_llm_judge"):
        command.append("--skip-llm-judge")
    if args.get("skip_grader"):
        command.append("--skip-grader")

    return " ".join(shlex.quote(part) for part in command)


def _append_arg(command: list[str], flag: str, value: Any) -> None:
    if value is None:
        return
    command.extend([flag, str(value)])


def _build_source_artifacts(run_dir: Path) -> list[SourceArtifact]:
    """Build the "Source Artifacts" rows for the leaderboard.

    In CI we set ``LEADERBOARD_WORKFLOW_RUN_URL`` to
    ``<server>/<repo>/actions/runs/<run_id>``; that URL is stable from the
    moment the job starts (no chicken-and-egg with artifact uploads) and
    drops the viewer at the Artifacts panel where every uploaded artifact
    is one click away. When the env var is unset (local generation) we
    fall back to plain filesystem paths.

    The answer-type / topic CSVs are still listed even though the board no
    longer renders those breakdowns: the analyzer keeps writing them, and
    they're the artifact anyone re-slicing accuracy by category reaches for.
    """
    workflow_run_url = os.environ.get("LEADERBOARD_WORKFLOW_RUN_URL") or None
    return [
        SourceArtifact(name="Run directory", path=str(run_dir), url=workflow_run_url),
        SourceArtifact(name="Headline CSV", path=str(run_dir / SUMMARY_CSV_FILENAME), url=workflow_run_url),
        SourceArtifact(name="Significance CSV", path=str(run_dir / SIGNIFICANCE_FILENAME), url=workflow_run_url),
        SourceArtifact(
            name="Answer-type CSV",
            path=str(run_dir / "analyzed_by_answer_type.csv"),
            url=workflow_run_url,
        ),
        SourceArtifact(name="Topic CSV", path=str(run_dir / "analyzed_by_topic.csv"), url=workflow_run_url),
    ]


def _to_float(value: float | str | None) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


__all__ = [
    "HeadlineRow",
    "LeaderboardData",
    "REQUIRED_HEADLINE_COLUMNS",
    "SIGNIFICANCE_FILENAME",
    "SNAPSHOT_SCHEMA_VERSION",
    "SUMMARY_CSV_FILENAME",
    "SourceArtifact",
    "SystemSignificance",
    "load_snapshot",
    "prepare_leaderboard_data",
    "save_snapshot",
]

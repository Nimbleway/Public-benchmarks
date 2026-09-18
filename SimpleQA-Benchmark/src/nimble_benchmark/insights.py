"""Per-run ``insights.md`` and ``errors.md`` writers.

Two artifacts, written into the run directory next to ``run.md``:

``insights.md``
    A narrative digest of what this run actually shows: headline movements
    against the previous run -- whatever slice it sampled, with a caveat when
    the slices differ -- lane roster changes, reliability and coverage gaps,
    latency outliers, and cost. Written for a human (or agent)
    who wants the "so what" without opening four CSVs.

``errors.md``
    A triage digest of everything that failed, grouped by *cause class* with
    concrete remediation. Deliberately **empty (zero bytes) when the run had
    no issues**, so ``test -s errors.md`` is a one-shot health check and an
    agent reading it next run gets signal rather than boilerplate.

Why a separate module rather than more of ``report.py``: ``report.py`` renders
*this* run's measurements. These two files interpret them -- they compare
across runs, classify causes, and make recommendations. Keeping interpretation
separate keeps ``run.md`` a stable, mechanical artifact.

On statistics: cross-run deltas here are **heuristic materiality thresholds,
not significance tests**. Two runs over the same seeded SimpleQA slice are
paired data, but the summary CSVs carry only per-lane means (no per-query
values), so a proper paired test is not computable from them. The rigorous
comparison already exists for *within*-run lane-vs-baseline in
``significance.csv``; ``insights.md`` surfaces that verbatim and is explicit
about which claims are tested and which are eyeballed.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Final

import pandas as pd

from nimble_benchmark.report import SIGNIFICANCE_FILENAME, SUMMARY_FILENAME

logger = logging.getLogger(__name__)

INSIGHTS_FILENAME: Final[str] = "insights.md"
ERRORS_FILENAME: Final[str] = "errors.md"

# Heuristic "worth a second look" thresholds for cross-run deltas. NOT
# significance tests -- see the module docstring. Calibrated so a normal
# run-to-run wobble at n=500 stays quiet: the binomial standard error of an
# accuracy near 0.9 at n=500 is ~1.3pp, so a 2.5pp move is roughly 2 SE.
NOTABLE_ACCURACY_DELTA: Final[float] = 0.025
NOTABLE_METRIC_DELTA: Final[float] = 0.02
# Latency is far noisier run-to-run (upstream load, time of day), so it needs a
# much bigger relative move before it means anything.
NOTABLE_LATENCY_RATIO: Final[float] = 1.5

# A lane whose p95 exceeds this multiple of its own p50 is spending its tail
# somewhere pathological (timeouts, retries) rather than just being slow.
# 3.5 was picked against the 2026-08-08 14-lane run, where healthy lanes sat
# between 1.1x and 2.9x and only one timing-out lane (3.5x,
# p95 113 s) crossed it.
TAIL_RATIO_ALERT: Final[float] = 3.5

# A lane whose p50 exceeds this multiple of the median lane's p50 is an
# outlier on wall-clock cost, which should be justified by a quality win.
SLOW_LANE_RATIO: Final[float] = 2.5


# ----------------------------------------------------------------------
# Error classification
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class ErrorClass:
    """One cause class, with the remediation an operator/agent should apply."""

    key: str
    title: str
    blocking: bool
    remediation: str


# Ordered most-specific first: ``classify_error`` returns the first match, so a
# 402 must be tested before any generic 4xx rule.
AUTH = ErrorClass(
    key="auth",
    title="Authentication / authorization",
    blocking=True,
    remediation=(
        "The API key is missing, invalid, expired, or lacks access to this endpoint. "
        "Rotate or re-issue the key and update `.env`. Retrying will not help."
    ),
)
BILLING = ErrorClass(
    key="billing",
    title="Billing / credits exhausted",
    blocking=True,
    remediation=(
        "The account is out of credits or past a spending cap. Top up with the provider, "
        "or drop this lane from the run (`--samplers`) until it is funded. "
        "Retrying will not help and every row will fail."
    ),
)
QUOTA = ErrorClass(
    key="quota",
    title="Plan quota exhausted (period-scoped)",
    blocking=True,
    remediation=(
        "A per-month/per-period allowance is used up. Client-side throttling cannot fix this — "
        "the budget resets on the provider's schedule. Raise the plan limit or exclude the lane. "
        "Re-running the same size benchmark will exhaust it again."
    ),
)
RATE_LIMIT = ErrorClass(
    key="rate_limit",
    title="Rate limit (per-second/minute)",
    blocking=False,
    remediation=(
        "Requests exceeded the provider's throughput ceiling. Lower the lane's "
        "`*_RATE_PER_SECOND` env var (see `samplers/_rate_limit.py`) or `--max-concurrent-tasks`. "
        "The runner already caps workers to the configured rate, so persistent 429s mean the "
        "configured rate is above the real ceiling."
    ),
)
TRANSIENT = ErrorClass(
    key="transient",
    title="Transient network / upstream",
    blocking=False,
    remediation=(
        "Timeouts, connection resets, and 5xx that survived the retry budget. A handful per "
        "thousand rows is normal. "
        "A concentrated burst in one lane points at that upstream being unhealthy or too slow "
        "for the configured timeout."
    ),
)
VALIDATION = ErrorClass(
    key="validation",
    title="Request rejected as invalid",
    blocking=True,
    remediation=(
        "The provider rejected the request shape (400/422). This is a harness bug or a breaking "
        "upstream API change, not a flake — inspect the sampler's payload builder."
    ),
)
CONFIG = ErrorClass(
    key="config",
    title="Misconfiguration",
    blocking=True,
    remediation=(
        "The endpoint or model name does not exist (404). Usually a retired model alias — e.g. a "
        "`-preview` pin that was withdrawn. Update the model setting in `.env` / `config.py`. "
        "Every row in the lane will fail until it is corrected."
    ),
)
UNKNOWN = ErrorClass(
    key="unknown",
    title="Unclassified",
    blocking=False,
    remediation=(
        "No classification rule matched. Read the raw text below and, if it recurs, add a rule to "
        "`insights._CLASSIFIER_RULES` so the next run triages it automatically."
    ),
)

# Render order in errors.md: blocking classes first, most-actionable at the top.
ERROR_CLASS_ORDER: Final[tuple[ErrorClass, ...]] = (
    AUTH,
    BILLING,
    QUOTA,
    CONFIG,
    VALIDATION,
    RATE_LIMIT,
    TRANSIENT,
    UNKNOWN,
)

# (compiled pattern, class). FIRST MATCH WINS, so order matters.
#
# Every pattern below is grounded in an error string actually observed in this
# repo's ``runs/`` history -- not invented. The ordering encodes two traps that
# real provider messages set:
#
# 1. Providers overload the word "limit" across billing, period-quota, and
#    per-second throttling. "Product rate limit quota exceeded" (Parallel) is a
#    MONTHLY quota that says "rate limit"; throttling it harder does nothing.
#    Quota therefore has to be tested before the generic rate-limit rule.
# 2. An HTTP status code alone is not a diagnosis. Free tiers return 429 with
#    ``"limit": 0`` (observed on the since-removed Gemini lane) -- there is no
#    quota to wait for, so treating it as retryable throttling (as the bare 429
#    would) sends an operator chasing a fix that cannot exist. The zero-quota
#    wording is matched before 429.
_CLASSIFIER_RULES: Final[tuple[tuple[re.Pattern[str], ErrorClass], ...]] = (
    # --- auth: 401/403 and credential wording ---
    (
        re.compile(
            r"\b401\b|\b403\b|unauthorized|forbidden|permission denied|"
            r"invalid[ _-]?(api[ _-]?key|token)|authentication",
            re.I,
        ),
        AUTH,
    ),
    # --- billing: 402 / credit exhaustion (before quota, before rate) ---
    (
        re.compile(
            r"\b402\b|payment required|credits? limit|out of credits|top up|insufficient (funds|credit)",
            re.I,
        ),
        BILLING,
    ),
    # --- quota: period-scoped allowances that retrying can never clear.
    #     ``quota_search_pm`` is Parallel's per-month key; Google/OpenAI report
    #     RESOURCE_EXHAUSTED / "exceeded your current quota" under a 429.
    (
        re.compile(
            r"quota[_ ]?(exceeded|key)|quota_\w*_(pm|pd)|exceeded your current quota|"
            r"resource_exhausted|monthly (quota|limit)|usage limit|check your plan and billing",
            re.I,
        ),
        QUOTA,
    ),
    # --- config: nonexistent endpoint or retired model alias ---
    (re.compile(r"\b404\b|not found|does not exist|model_not_found", re.I), CONFIG),
    # --- rate limit: genuine per-second/minute throttling ---
    (re.compile(r"\b429\b|too many requests|rate limit|rate_limit_exceeded", re.I), RATE_LIMIT),
    # --- validation ---
    (re.compile(r"\b(400|422)\b|bad request|unprocessable|validation", re.I), VALIDATION),
    # --- transient: timeouts, resets, DNS, broken pipes, 5xx, and the harness's
    #     own ``transient HTTP 0`` marker (retry.py's fallback when the wrapped
    #     exception stringifies to empty -- several distinct client-side
    #     failures collapse into that one label, so it is intentionally last).
    (
        re.compile(
            r"transient http 0|timeout|timed out|cannot connect to host|"
            r"connection (error|reset|termination|refused|aborted)|reset by peer|"
            r"server disconnected|disconnect|broken pipe|nodename nor servname|"
            r"temporary failure in name resolution|\b5\d{2}\b|bad gateway|"
            r"service unavailable|gateway timeout|upstream connect error",
            re.I,
        ),
        TRANSIENT,
    ),
)


def classify_error(text: str | None) -> ErrorClass:
    """Map a raw provider error string to a cause class.

    Returns :data:`UNKNOWN` when nothing matches -- deliberately, so an
    unrecognised failure mode is visible in ``errors.md`` as "unclassified"
    rather than being silently filed under a wrong (and wrongly actionable)
    heading.
    """
    if not text:
        return UNKNOWN
    for pattern, error_class in _CLASSIFIER_RULES:
        if pattern.search(text):
            return error_class
    return UNKNOWN


# ----------------------------------------------------------------------
# Run data loading
# ----------------------------------------------------------------------


@dataclass
class LaneFailures:
    """Failure rollup for one (provider, answer_source) lane.

    Keyed by lane rather than by provider so an ``api`` failure (a native-LLM
    answer lane) is distinguishable from a ``synth`` one (a /search lane),
    matching the lane key used by ``insights.md``, the analyzed CSVs, and the
    leaderboard. ``answer_source`` is ``None`` only for run artifacts whose raw
    CSVs predate the ``answer_source_used`` column.
    """

    provider: str
    answer_source: str | None
    total_rows: int
    failed_rows: int
    # class key -> {error text -> count}
    by_class: dict[str, dict[str, int]] = field(default_factory=dict)

    @property
    def lane_key(self) -> str:
        return f"{self.provider}/{self.answer_source}" if self.answer_source else self.provider

    @property
    def failure_rate(self) -> float:
        return self.failed_rows / self.total_rows if self.total_rows else 0.0


def _read_raw_columns(path: Path, columns: list[str]) -> pd.DataFrame:
    """Read only the needed columns from a raw results CSV.

    Raw CSVs carry full page text in ``chunks_json`` and run to hundreds of MB
    on a 500-row run; ``usecols`` keeps this cheap. Missing columns (older run
    artifacts) degrade to an empty frame rather than raising.
    """
    try:
        available = set(pd.read_csv(path, nrows=0).columns)
    except Exception:
        logger.debug("insights: could not read header of %s", path, exc_info=True)
        return pd.DataFrame()
    usable = [column for column in columns if column in available]
    if not usable:
        return pd.DataFrame()
    try:
        return pd.read_csv(path, usecols=usable, low_memory=False)
    except Exception:
        logger.debug("insights: could not read %s", path, exc_info=True)
        return pd.DataFrame()


def collect_failures(run_dir: Path) -> list[LaneFailures]:
    """Group every non-ok row in the run's raw CSVs by lane and cause class.

    Grouped by ``(provider, answer_source_used)``, not by provider alone: the
    source is fixed per lane, so the pair identifies exactly one lane and
    keeps the key aligned with every other lane-keyed table in the digest.
    """
    lanes: list[LaneFailures] = []
    for path in sorted(run_dir.glob("*_raw_results_*.csv")):
        df = _read_raw_columns(path, ["provider", "answer_source_used", "request_status", "request_error"])
        if df.empty or "request_status" not in df.columns:
            continue
        fallback_provider = str(df["provider"].iloc[0]) if "provider" in df.columns and len(df) else path.stem
        has_source = "answer_source_used" in df.columns
        group_columns = ["provider"] + (["answer_source_used"] if has_source else [])
        if "provider" not in df.columns:
            continue

        for group_key, group in df.groupby(group_columns, dropna=False, sort=True):
            values = group_key if isinstance(group_key, tuple) else (group_key,)
            provider = str(values[0]) if pd.notna(values[0]) else fallback_provider
            source = str(values[1]) if has_source and len(values) > 1 and pd.notna(values[1]) else None
            bad = group[group["request_status"].astype(str) != "ok"]
            if bad.empty:
                continue
            lane = LaneFailures(
                provider=provider,
                answer_source=source,
                total_rows=len(group),
                failed_rows=len(bad),
            )
            for _, row in bad.iterrows():
                text = "" if pd.isna(row.get("request_error")) else str(row.get("request_error"))
                error_class = classify_error(text)
                bucket = lane.by_class.setdefault(error_class.key, {})
                key = text.strip() or f"(no error text; status={row.get('request_status')})"
                bucket[key] = bucket.get(key, 0) + 1
            lanes.append(lane)
    return lanes


def collect_empty_answer_rows(run_dir: Path) -> dict[str, int]:
    """Count rows where the request succeeded but the provider returned nothing.

    These are the placeholder rows ``runner._empty_answer_candidate`` writes
    for a ``status=ok`` response with no answer text. They are graded
    ``is_not_attempted`` and score as misses, so a lane quietly accumulating
    them is losing accuracy without anything appearing in ``failure_rate`` --
    worth surfacing even though nothing "failed".
    """
    counts: dict[str, int] = {}
    for path in sorted(run_dir.glob("*_raw_results_*.csv")):
        df = _read_raw_columns(
            path,
            ["provider", "answer_source_used", "request_status", "evaluation_result", "generated_answer"],
        )
        if df.empty or not {"request_status", "evaluation_result"}.issubset(df.columns):
            continue
        empty_answer = (
            df["generated_answer"].isna() | (df["generated_answer"].astype(str).str.strip() == "")
            if "generated_answer" in df.columns
            else True
        )
        mask = (
            (df["request_status"].astype(str) == "ok")
            & (df["evaluation_result"].astype(str) == "is_not_attempted")
            & empty_answer
        )
        matched = df[mask]
        if matched.empty:
            continue
        fallback = str(df["provider"].iloc[0]) if "provider" in df.columns else path.stem
        # Keyed per lane so the counts line up with every other lane-keyed
        # table in the digest.
        for _, row in matched.iterrows():
            provider = str(row.get("provider")) if pd.notna(row.get("provider")) else fallback
            source = row.get("answer_source_used")
            lane = f"{provider}/{source}" if pd.notna(source) and str(source).strip() else provider
            counts[lane] = counts.get(lane, 0) + 1
    return counts


def load_summaries(run_dir: Path) -> pd.DataFrame:
    """The run's summary CSV, or an empty frame when it's missing/unreadable."""
    return _read_optional_csv(run_dir / SUMMARY_FILENAME)


def load_significance(run_dir: Path) -> pd.DataFrame:
    return _read_optional_csv(run_dir / SIGNIFICANCE_FILENAME)


def _read_optional_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        df = pd.read_csv(path)
    except Exception:
        return pd.DataFrame()
    return df if not df.empty else pd.DataFrame()


def _lane_key(row) -> str:
    return f"{row.get('provider')}/{row.get('answer_source')}"


def find_previous_run_dir(run_dir: Path) -> Path | None:
    """The run to diff against -- the last one -- or ``None`` on a first run.

    Deliberately *not* filtered by question slice: the diff is against whatever
    ran last, whatever it sampled. A slice change makes the deltas noisier, not
    unreportable, so it is surfaced as a caveat on the comparison (see
    :func:`slices_comparable`) rather than used to skip runs.

    Prefers the most recent earlier run that actually has analyzed summaries,
    so a run that died before aggregation doesn't blank the section; falls back
    to the immediately-preceding run so roster changes are still reported.

    Ordered by directory name, which embeds a sortable ``run_<YYYYmmdd_HHMMSS>``
    timestamp -- more reliable than mtime, which later writes churn.
    """
    parent = run_dir.parent
    if not parent.is_dir():
        return None
    siblings = sorted(
        (p for p in parent.glob("run_*") if p.is_dir() and p.name < run_dir.name),
        key=lambda p: p.name,
        reverse=True,
    )
    if not siblings:
        return None
    for candidate in siblings:
        if not load_summaries(candidate).empty:
            return candidate
    return siblings[0]


def _load_run_args(run_dir: Path) -> dict:
    path = run_dir / "run.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text()).get("args", {}) or {}
    except Exception:
        return {}


def slices_comparable(current_args: dict, previous_args: dict) -> tuple[bool, str]:
    """Whether two runs scored the same question set.

    SimpleQA rows are drawn with ``random.Random(seed).sample`` so identical
    ``(dataset, limit, random_state)`` means identical questions -- the case
    where a metric delta reflects the provider rather than the sample.

    Advisory only: the comparison is always rendered. This decides how strongly
    to caveat it, not whether the reader gets one.
    """
    if not previous_args:
        return False, "previous run has no run.json, so its question slice is unknown"
    fields = ("dataset", "limit", "random_state")
    mismatches = [
        f"{field}: {previous_args.get(field)!r} -> {current_args.get(field)!r}"
        for field in fields
        if previous_args.get(field) != current_args.get(field)
    ]
    if mismatches:
        return False, "different question slice (" + "; ".join(mismatches) + ")"
    return True, "same dataset, limit and seed — identical question set"


def _fmt_pct(value, digits: int = 1) -> str:
    numeric = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    if pd.isna(numeric):
        return "—"
    return f"{numeric * 100:.{digits}f}%"


def _fmt_num(value, digits: int = 3) -> str:
    numeric = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    if pd.isna(numeric):
        return "—"
    return f"{numeric:.{digits}f}"


def _fmt_int(value) -> str:
    numeric = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    if pd.isna(numeric):
        return "—"
    return f"{int(numeric):,}"


def _fmt_delta(value, *, digits: int = 3, as_pct: bool = False) -> str:
    numeric = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    if pd.isna(numeric):
        return "—"
    scaled = numeric * 100 if as_pct else numeric
    suffix = "pp" if as_pct else ""
    return f"{scaled:+.{1 if as_pct else digits}f}{suffix}"


def _duration_text(started_at: datetime, finished_at: datetime) -> str:
    seconds = max(0.0, (finished_at - started_at).total_seconds())
    if seconds < 90:
        return f"{seconds:.0f}s"
    minutes = seconds / 60
    if minutes < 90:
        return f"{minutes:.1f} min"
    return f"{minutes / 60:.1f} h"


# ----------------------------------------------------------------------
# insights.md
# ----------------------------------------------------------------------


def write_insights_md(
    *,
    run_dir: Path,
    args: dict,
    started_at: datetime,
    finished_at: datetime,
    previous_run_dir: Path | None = None,
) -> Path:
    """Write ``insights.md`` -- the interpreted digest of this run.

    ``previous_run_dir`` defaults to the last run in this results directory,
    regardless of which question slice it sampled; pass it explicitly to diff
    against a specific baseline run (or pass a directory with no summaries to
    skip the comparison).
    """
    summaries = load_summaries(run_dir)
    previous = previous_run_dir if previous_run_dir is not None else find_previous_run_dir(run_dir)

    lines: list[str] = [
        f"# Insights — {run_dir.name}\n\n",
        "*Interpretation layer over `run.md`. Measurements live there; this file says what they mean. ",
        "Cross-run deltas are heuristic materiality thresholds, **not** significance tests — "
        "the tested comparison is lane-vs-baseline within this run (see Significance below).*\n\n",
    ]

    _append_at_a_glance(lines, summaries=summaries, args=args, started_at=started_at, finished_at=finished_at)
    _append_comparison(lines, run_dir=run_dir, summaries=summaries, args=args, previous_run_dir=previous)
    _append_reliability(lines, run_dir=run_dir, summaries=summaries, args=args)
    _append_latency(lines, summaries=summaries)
    _append_significance(lines, run_dir=run_dir)
    _append_usage(lines, summaries=summaries)

    path = run_dir / INSIGHTS_FILENAME
    path.write_text("".join(lines))
    return path


def _append_at_a_glance(
    lines: list[str], *, summaries: pd.DataFrame, args: dict, started_at: datetime, finished_at: datetime
) -> None:
    lines.append("## At a glance\n\n")
    lanes = len(summaries)
    providers = summaries["provider"].nunique() if not summaries.empty else 0
    graded_rows = int(pd.to_numeric(summaries.get("problem_count"), errors="coerce").fillna(0).sum()) if lanes else 0
    healthy = 0
    if lanes and "failure_count" in summaries.columns:
        healthy = int((pd.to_numeric(summaries["failure_count"], errors="coerce").fillna(0) == 0).sum())
    lines.append(f"- **{providers} providers / {lanes} scored lanes**, {args.get('limit', '—')} questions each\n")
    lines.append(f"- **{graded_rows:,} graded rows**\n")
    lines.append(f"- **Duration:** {_duration_text(started_at, finished_at)}\n")
    lines.append(
        f"- **Models:** grader `{args.get('grader_model', '—')}`, judge `{args.get('judge_model', '—')}`, "
        f"synthesis `{args.get('synthesis_model', '—')}`\n"
    )
    if lanes:
        lines.append(f"- **Fully clean lanes:** {healthy}/{lanes}\n")
    skipped = args.get("preflight_skipped_samplers") or []
    if skipped:
        lines.append(f"- **Skipped by preflight:** {', '.join(f'`{name}`' for name in skipped)}\n")
    lines.append("\n")


def _append_comparison(
    lines: list[str], *, run_dir: Path, summaries: pd.DataFrame, args: dict, previous_run_dir: Path | None
) -> None:
    lines.append("## What changed since the last run\n\n")
    if previous_run_dir is None:
        lines.append("No earlier run found in this results directory — nothing to compare against.\n\n")
        return

    previous_summaries = load_summaries(previous_run_dir)
    if previous_summaries.empty:
        lines.append(
            f"Previous run `{previous_run_dir.name}` has no analyzed summaries "
            "(it likely failed before aggregation), so no comparison is possible.\n\n"
        )
        return

    comparable, reason = slices_comparable(args, _load_run_args(previous_run_dir))
    lines.append(f"Compared with **`{previous_run_dir.name}`** — {reason}.\n\n")

    current_lanes = {_lane_key(row): row for _, row in summaries.iterrows()} if not summaries.empty else {}
    previous_lanes = {_lane_key(row): row for _, row in previous_summaries.iterrows()}

    # --- roster changes: the failure mode where a lane silently vanishes ---
    added = sorted(set(current_lanes) - set(previous_lanes))
    removed = sorted(set(previous_lanes) - set(current_lanes))
    if added or removed:
        lines.append("### Lane roster\n\n")
        for lane in added:
            lines.append(f"- **Added:** `{lane}` — not present in the previous run.\n")
        for lane in removed:
            lines.append(
                f"- **Dropped:** `{lane}` — scored last run, absent here. "
                "If unintentional, check the `--samplers` list and the lane's credentials.\n"
            )
        lines.append("\n")

    if not comparable:
        lines.append(
            "> [!WARNING]\n"
            "> **The two runs scored different question sets**, so every delta below mixes provider "
            "change with sample change and none of it is evidence on its own. Treat a movement here "
            "as a lead to re-test on a matched slice (same `--limit` and `--random-state`), not as a "
            "result. The tested comparison in this file is the within-run lane-vs-baseline "
            "significance table, which is unaffected.\n\n"
        )

    shared = sorted(set(current_lanes) & set(previous_lanes))
    if not shared:
        lines.append("No lanes in common, so there is nothing to diff.\n\n")
        return

    moves: list[tuple[str, str, float, float, float]] = []
    for lane in shared:
        current, previous = current_lanes[lane], previous_lanes[lane]
        for metric, threshold in (
            ("accuracy_score", NOTABLE_ACCURACY_DELTA),
            ("ndcg_at_10", NOTABLE_METRIC_DELTA),
            ("recall_at_10_llm", NOTABLE_METRIC_DELTA),
        ):
            now = pd.to_numeric(pd.Series([current.get(metric)]), errors="coerce").iloc[0]
            before = pd.to_numeric(pd.Series([previous.get(metric)]), errors="coerce").iloc[0]
            if pd.isna(now) or pd.isna(before):
                continue
            delta = float(now - before)
            if abs(delta) >= threshold:
                moves.append((lane, metric, float(before), float(now), delta))

    if not moves:
        lines.append(
            f"**No metric moved materially.** Every shared lane stayed within "
            f"{NOTABLE_ACCURACY_DELTA * 100:.1f}pp on accuracy and {NOTABLE_METRIC_DELTA:.2f} on "
            "NDCG@10 / Recall@10 — consistent with run-to-run noise rather than a real change"
            + ("" if comparable else ", and the two runs did not even share a question slice")
            + ".\n\n"
        )
        return

    lines.append("### Metric movements\n\n")
    lines.append(
        f"Lanes that moved by more than {NOTABLE_ACCURACY_DELTA * 100:.1f}pp (accuracy) or "
        f"{NOTABLE_METRIC_DELTA:.2f} (NDCG@10 / Recall@10). Everything else held steady."
        + (
            ""
            if comparable
            else " Both runs' numbers are honest measurements of their own slice; the *difference* "
            "is what carries the sample confound flagged above."
        )
        + "\n\n"
    )
    lines.append("| lane | metric | previous | current | delta |\n|---|---|---:|---:|---:|\n")
    for lane, metric, before, now, delta in sorted(moves, key=lambda m: -abs(m[4])):
        as_pct = metric == "accuracy_score"
        fmt = _fmt_pct if as_pct else _fmt_num
        arrow = "🟢" if delta > 0 else "🔴"
        lines.append(
            f"| `{lane}` | {metric} | {fmt(before)} | {fmt(now)} | " f"{arrow} {_fmt_delta(delta, as_pct=as_pct)} |\n"
        )
    lines.append("\n")


def excluded_lanes_by_reason(summaries: pd.DataFrame) -> dict[str, list[str]]:
    """Lanes the analyzer's reliability gate dropped, grouped by reason."""
    if summaries.empty or "excluded_reason" not in summaries.columns:
        return {}
    grouped: dict[str, list[str]] = {}
    for _, row in summaries.iterrows():
        reason = str(row.get("excluded_reason") or "").strip()
        if reason and reason.lower() != "nan":
            grouped.setdefault(reason, []).append(_lane_key(row))
    return {reason: sorted(lanes) for reason, lanes in grouped.items()}


def _run_is_below_ranking_gate(args: dict) -> bool:
    """True when the run is too small to clear ``--min-successful-problems``.

    On a smoke run (``--limit 5``) *every* lane trips
    ``below_min_successful_problems`` no matter how healthy it is. Listing all
    of them as warnings buries the one or two real problems, so this case is
    collapsed into a single explanatory line instead.
    """
    limit = pd.to_numeric(pd.Series([args.get("limit")]), errors="coerce").iloc[0]
    floor = pd.to_numeric(pd.Series([args.get("min_successful_problems")]), errors="coerce").iloc[0]
    if pd.isna(limit) or pd.isna(floor):
        return False
    return float(limit) < float(floor)


def _is_structural_exclusion(reason: str, args: dict) -> bool:
    """True when this exclusion is a property of the run size, not a lane fault.

    On a deliberately small run (``--limit 5``) every lane trips
    ``below_min_successful_problems`` no matter how healthy it is. That is an
    expected consequence of the requested size, so it must not count as an
    "issue" -- otherwise every smoke run would raise a false alarm and
    ``errors.md`` would never be empty.
    """
    return reason == "below_min_successful_problems" and _run_is_below_ranking_gate(args)


def has_actionable_exclusions(summaries: pd.DataFrame, args: dict) -> bool:
    """Whether any reliability-gate exclusion reflects a real problem."""
    return any(not _is_structural_exclusion(reason, args) for reason in excluded_lanes_by_reason(summaries))


def summarize_exclusions(summaries: pd.DataFrame, args: dict) -> list[str]:
    """Bullet lines describing reliability-gate exclusions, grouped by reason.

    One line per reason rather than per lane: a 14-lane run that trips the same
    gate everywhere should read as one fact, not fourteen.
    """
    grouped = excluded_lanes_by_reason(summaries)
    if not grouped:
        return []
    out: list[str] = []
    for reason, lanes in sorted(grouped.items()):
        if _is_structural_exclusion(reason, args):
            lane_word = "lane" if len(lanes) == 1 else "lanes"
            out.append(
                f"- {len(lanes)} {lane_word} fall below the leaderboard ranking gate because this run "
                f"scored only {args.get('limit')} question(s), under `--min-successful-problems "
                f"{args.get('min_successful_problems')}`. **Expected at this run size** — not a lane "
                "problem, but it does mean nothing here is rankable.\n"
            )
            continue
        listed = ", ".join(f"`{lane}`" for lane in lanes[:8])
        overflow = f" (+{len(lanes) - 8} more)" if len(lanes) > 8 else ""
        out.append(
            f"- ⚠ **{len(lanes)} lane(s) excluded from the ranking** (`{reason}`): {listed}{overflow}. "
            "Their metrics come from a truncated sample and are not comparable to full lanes.\n"
        )
    return out


def _append_reliability(lines: list[str], *, run_dir: Path, summaries: pd.DataFrame, args: dict) -> None:
    lines.append("## Reliability & coverage\n\n")
    notes: list[str] = []

    if not summaries.empty and "failure_rate" in summaries.columns:
        unhealthy = summaries[pd.to_numeric(summaries["failure_rate"], errors="coerce").fillna(0) > 0]
        if unhealthy.empty:
            notes.append("- Every lane completed with a **0% request failure rate**.\n")
        else:
            notes.append("- Lanes with request failures (see `errors.md` for causes and fixes):\n")
            for _, row in unhealthy.sort_values("failure_rate", ascending=False).iterrows():
                notes.append(
                    f"  - `{_lane_key(row)}` — {_fmt_pct(row.get('failure_rate'))} "
                    f"({_fmt_int(row.get('failure_count'))} of {_fmt_int(row.get('problem_count'))} rows)\n"
                )

    notes.extend(summarize_exclusions(summaries, args))

    # Coverage: a lane scoring fewer questions than --limit is being graded on a
    # different (smaller) sample than its peers, which quietly biases any
    # head-to-head comparison.
    limit = pd.to_numeric(pd.Series([args.get("limit")]), errors="coerce").iloc[0]
    if not summaries.empty and not pd.isna(limit) and "problem_count" in summaries.columns:
        short = summaries[pd.to_numeric(summaries["problem_count"], errors="coerce").fillna(0) < float(limit)]
        if not short.empty:
            gaps = ", ".join(
                f"`{_lane_key(row)}` ({_fmt_int(row.get('problem_count'))}/{int(limit)})" for _, row in short.iterrows()
            )
            notes.append(
                f"- **Incomplete coverage** — these lanes scored fewer than {int(limit)} questions: "
                f"{gaps}. They are graded on a smaller sample than their peers; "
                "re-run the benchmark to score them on the full slice.\n"
            )

    empty_answers = collect_empty_answer_rows(run_dir)
    for provider, count in sorted(empty_answers.items(), key=lambda item: -item[1]):
        notes.append(
            f"- `{provider}` returned **{count} empty answer(s)** on successful requests. These are "
            "graded `is_not_attempted` and score as misses, so they cost this lane accuracy without "
            "showing up in `failure_rate`.\n"
        )

    lines.extend(notes or ["- Nothing to flag.\n"])
    lines.append("\n")


def _append_latency(lines: list[str], *, summaries: pd.DataFrame) -> None:
    if summaries.empty or "provider_response_time_ms_p50" not in summaries.columns:
        return
    p50 = pd.to_numeric(summaries["provider_response_time_ms_p50"], errors="coerce")
    if not p50.notna().any():
        return
    lines.append("## Latency\n\n")
    median_p50 = float(p50.median())
    fastest = summaries.loc[p50.idxmin()]
    slowest = summaries.loc[p50.idxmax()]
    lines.append(
        f"- Median lane p50 is **{median_p50:,.0f} ms**. Fastest: `{_lane_key(fastest)}` "
        f"({_fmt_int(fastest.get('provider_response_time_ms_p50'))} ms). Slowest: `{_lane_key(slowest)}` "
        f"({_fmt_int(slowest.get('provider_response_time_ms_p50'))} ms).\n"
    )
    # Outlier-slow lanes: a lane far slower than the typical lane is paying a
    # real wall-clock cost that should be justified by a retrieval-quality win.
    for _, row in summaries.iterrows():
        row_p50 = pd.to_numeric(pd.Series([row.get("provider_response_time_ms_p50")]), errors="coerce").iloc[0]
        if pd.isna(row_p50) or median_p50 <= 0:
            continue
        if row_p50 / median_p50 >= SLOW_LANE_RATIO:
            lines.append(
                f"- ⚠ `{_lane_key(row)}` is **{row_p50 / median_p50:.1f}x slower than the median lane** "
                f"({row_p50:,.0f} ms vs {median_p50:,.0f} ms). Worth checking that its retrieval "
                "quality justifies the wall-clock cost.\n"
            )
    if "provider_response_time_ms_p95" in summaries.columns:
        for _, row in summaries.iterrows():
            row_p50 = pd.to_numeric(pd.Series([row.get("provider_response_time_ms_p50")]), errors="coerce").iloc[0]
            row_p95 = pd.to_numeric(pd.Series([row.get("provider_response_time_ms_p95")]), errors="coerce").iloc[0]
            if pd.isna(row_p50) or pd.isna(row_p95) or row_p50 <= 0:
                continue
            if row_p95 / row_p50 >= TAIL_RATIO_ALERT:
                lines.append(
                    f"- ⚠ `{_lane_key(row)}` has a **heavy tail**: p95 {row_p95:,.0f} ms is "
                    f"{row_p95 / row_p50:.1f}x its p50 ({row_p50:,.0f} ms). That shape usually means "
                    "requests are hitting timeouts or retries rather than merely being slow.\n"
                )
    lines.append("\n")


def _append_significance(lines: list[str], *, run_dir: Path) -> None:
    significance = load_significance(run_dir)
    if significance.empty or "significant" not in significance.columns:
        return
    winners = significance[significance["significant"].astype(str).str.lower().isin({"true", "1"})]
    lines.append("## Significance (tested, within this run)\n\n")
    if winners.empty:
        lines.append(
            "No lane beat the baseline at Bonferroni-corrected p < 0.05. Differences in the "
            "headline table are not statistically separable on this sample.\n\n"
        )
        return
    lines.append(
        "Lanes that beat the run's baseline on a paired Student's t-test "
        "(Bonferroni-corrected p < 0.05). This is the one comparison in this file that is "
        "a real hypothesis test.\n\n"
    )
    lines.append("| system | metric | baseline mean | system mean | delta | p (corrected) |\n")
    lines.append("|---|---|---:|---:|---:|---:|\n")
    for _, row in winners.sort_values("mean_delta", ascending=False).iterrows():
        lines.append(
            f"| `{row.get('system')}` | {row.get('metric')} | {_fmt_num(row.get('baseline_mean'))} | "
            f"{_fmt_num(row.get('system_mean'))} | {_fmt_delta(row.get('mean_delta'))} | "
            f"{_fmt_num(row.get('p_bonferroni'), digits=4)} |\n"
        )
    lines.append("\n")


def _append_usage(lines: list[str], *, summaries: pd.DataFrame) -> None:
    if summaries.empty or "usage_input_tokens_sum" not in summaries.columns:
        return
    input_tokens = pd.to_numeric(summaries["usage_input_tokens_sum"], errors="coerce").fillna(0).sum()
    output_tokens = pd.to_numeric(summaries.get("usage_output_tokens_sum"), errors="coerce").fillna(0).sum()
    if input_tokens <= 0 and output_tokens <= 0:
        return
    lines.append("## Provider token usage\n\n")
    lines.append(
        f"- **{int(input_tokens):,} input** / **{int(output_tokens):,} output** tokens reported by "
        "providers that expose usage. Lanes that report nothing are omitted, so this is a floor, "
        "not the full bill — and it excludes the harness's own OpenAI grading/judging/synthesis spend.\n\n"
    )


# ----------------------------------------------------------------------
# errors.md
# ----------------------------------------------------------------------

_MAX_SAMPLES_PER_CLASS: Final[int] = 3
_ERROR_TEXT_MAX_CHARS: Final[int] = 300


def write_errors_md(*, run_dir: Path, args: dict | None = None) -> Path:
    """Write ``errors.md`` -- the triage digest, or an empty file if clean.

    The file is **zero bytes when the run had no problems**, which makes
    ``test -s errors.md`` a one-line health check and means an agent reading it
    at the start of the next run sees signal or nothing at all.

    "Problems" is broader than request failures: a lane excluded by the
    reliability gate, a lane that scored fewer questions than requested, or a
    provider quietly returning empty answers all change how the numbers should
    be read, so they are reported here too.
    """
    lanes = collect_failures(run_dir)
    summaries = load_summaries(run_dir)
    empty_answers = collect_empty_answer_rows(run_dir)

    exclusion_notes = summarize_exclusions(summaries, args or {})
    short_rows: list[tuple[pd.Series, int]] = []
    if not summaries.empty:
        limit = pd.to_numeric(pd.Series([(args or {}).get("limit")]), errors="coerce").iloc[0]
        if not pd.isna(limit) and "problem_count" in summaries.columns:
            for _, row in summaries.iterrows():
                count = pd.to_numeric(pd.Series([row.get("problem_count")]), errors="coerce").iloc[0]
                if not pd.isna(count) and count < float(limit):
                    short_rows.append((row, int(limit) - int(count)))

    # Emptiness is decided on *actionable* problems only. A structural
    # exclusion (run too small to rank) is context, not an issue -- counting it
    # would make errors.md non-empty for every healthy smoke run and turn the
    # `test -s errors.md` health check into a permanent false alarm.
    path = run_dir / ERRORS_FILENAME
    if not lanes and not has_actionable_exclusions(summaries, args or {}) and not short_rows and not empty_answers:
        # Clean run: empty file, by design.
        path.write_text("")
        return path

    # Roll failures up by cause class across all lanes. Keyed by lane, so two
    # answer sources of the same provider stay distinguishable.
    by_class: dict[str, dict[str, dict[str, int]]] = {}
    for lane in lanes:
        for class_key, errors in lane.by_class.items():
            lane_bucket = by_class.setdefault(class_key, {})
            lane_bucket[lane.lane_key] = dict(errors)

    blocking_classes = [
        error_class for error_class in ERROR_CLASS_ORDER if error_class.key in by_class and error_class.blocking
    ]

    lines: list[str] = [
        f"# Errors — {run_dir.name}\n\n",
        "*Written for whoever (human or agent) picks this up next. Each section says what broke, "
        "whether it blocks, and what to do. An empty version of this file means the run was clean.*\n\n",
    ]

    total_failed = sum(lane.failed_rows for lane in lanes)
    if blocking_classes:
        titles = ", ".join(f"**{error_class.title}**" for error_class in blocking_classes)
        lines.append(
            f"> [!CAUTION]\n> **Action required before the next run.** Blocking problems found: {titles}. "
            "These will not clear on their own — re-running without fixing them reproduces the same failures.\n\n"
        )
    elif total_failed:
        lines.append(
            "> [!NOTE]\n> No blocking problems. The failures below are transient — re-running "
            "the same command should clear them.\n\n"
        )

    if lanes:
        lines.append("## Failure summary by lane\n\n")
        lines.append("| lane | failed / total | rate | cause classes |\n|---|---:|---:|---|\n")
        for lane in sorted(lanes, key=lambda item: -item.failure_rate):
            classes = ", ".join(sorted(lane.by_class)) or "—"
            lines.append(
                f"| `{lane.lane_key}` | {lane.failed_rows} / {lane.total_rows} | "
                f"{lane.failure_rate * 100:.1f}% | {classes} |\n"
            )
        lines.append("\n")

    for error_class in ERROR_CLASS_ORDER:
        if error_class.key not in by_class:
            continue
        marker = "🚫 " if error_class.blocking else ""
        lines.append(f"## {marker}{error_class.title}\n\n")
        lines.append(f"**What to do:** {error_class.remediation}\n\n")
        for lane_key, errors in sorted(by_class[error_class.key].items()):
            count = sum(errors.values())
            lines.append(f"- **`{lane_key}`** — {count} row(s):\n")
            for text, occurrences in sorted(errors.items(), key=lambda item: -item[1])[:_MAX_SAMPLES_PER_CLASS]:
                snippet = " ".join(str(text).split())
                if len(snippet) > _ERROR_TEXT_MAX_CHARS:
                    snippet = snippet[: _ERROR_TEXT_MAX_CHARS - 1] + "…"
                lines.append(f"  - `{occurrences}x` — {snippet}\n")
            extra = len(errors) - _MAX_SAMPLES_PER_CLASS
            if extra > 0:
                lines.append(f"  - …and {extra} more distinct message(s); see the raw CSVs.\n")
        lines.append("\n")

    if exclusion_notes or short_rows or empty_answers:
        lines.append("## Data-quality warnings\n\n")
        lines.append("Nothing errored here, but these change how the numbers should be read.\n\n")
        lines.extend(exclusion_notes)
        for row, missing in short_rows:
            lines.append(
                f"- **`{_lane_key(row)}` is short {missing} question(s)** "
                f"({_fmt_int(row.get('problem_count'))} scored). Re-run to score it on the full slice.\n"
            )
        for provider, count in sorted(empty_answers.items(), key=lambda item: -item[1]):
            lines.append(
                f"- **`{provider}` returned {count} empty answer(s)** on successful requests "
                "(graded `is_not_attempted`, scored as misses). If this is more than a handful, the "
                "lane's accuracy is being set by empty responses rather than by wrong answers.\n"
            )
        lines.append("\n")

    lines.append("## Reading the raw data\n\n")
    lines.append(
        "- Per-row detail: `dataset_*_raw_results_<provider>.csv` — columns `request_status`, `request_error`.\n"
        "- Structured failures incl. retrieval context: `failures.json`.\n"
        "- Full DEBUG trace for this run: `eval.log`.\n\n"
    )
    lines.append(
        "> **Note on `request_status`:** the harness writes `failed_after_retries` for *every* "
        "failure, including ones it never retried (a 402 or 404 is not retryable). Treat the "
        'status as "this row failed" only — the cause classification above comes from the '
        "error text, which is the reliable signal.\n"
    )

    path.write_text("".join(lines))
    return path

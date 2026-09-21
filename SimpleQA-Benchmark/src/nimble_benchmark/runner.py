"""Single benchmark runner."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
from datetime import datetime
from pathlib import Path
from typing import Final

import pandas as pd
from tqdm.asyncio import tqdm_asyncio

from nimble_benchmark.datasets import DATASETS, parse_gold_urls_from_metadata
from nimble_benchmark.judging.umbrela import judge_chunks
from nimble_benchmark.metrics.latency import KNOWN_SERVER_TIMING_STAGES
from nimble_benchmark.metrics.llm_retrieval import score_grades
from nimble_benchmark.metrics.url_retrieval import score_urls
from nimble_benchmark.samplers import answer_source_for_provider
from nimble_benchmark.synthesis.synthesizer import synth

logger = logging.getLogger(__name__)

SAMPLER_CONFIG_SCHEMA_VERSION = 1

# Process-wide OpenAI concurrency defaults, shared across ALL samplers in a
# run. These used to be per-sampler (judge = max_concurrent_tasks * 5 per
# sampler), which meant a 10-sampler dispatch could stack ~2000 concurrent
# judge calls onto one 100-connection httpx pool -- the queued requests
# surfaced as APITimeoutError / APIConnectionError storms. Global budgets keep
# the total OpenAI fan-out constant no matter how many samplers run at once;
# their sum stays below the pool ceiling in ``synthesis.llm``.
DEFAULT_JUDGE_CONCURRENCY: Final[int] = 64
DEFAULT_GRADER_CONCURRENCY: Final[int] = 32
DEFAULT_SYNTH_CONCURRENCY: Final[int] = 16

# Worker cap for rate-limited samplers: a lane throttled to R requests/second
# gains nothing from more than R * (typical request latency) in-flight workers
# -- the extras just camp on the token bucket. 10 s is a generous p90 latency
# estimate across the search lanes, so the cap errs toward keeping the token
# bucket saturated rather than starving a slow upstream.
_SAMPLER_RATE_HEADROOM_S: Final[float] = 10.0


def effective_sampler_concurrency(sampler, requested: int) -> int:
    """Clamp ``requested`` worker count to what the sampler's rate limit can use.

    Samplers that self-throttle expose ``rate_per_second`` (see
    ``samplers/_rate_limit.py``); unthrottled samplers keep the requested
    concurrency unchanged.
    """
    rate = getattr(sampler, "rate_per_second", None)
    if not rate or rate <= 0:
        return requested
    cap = max(1, math.ceil(rate * _SAMPLER_RATE_HEADROOM_S))
    if cap >= requested:
        return requested
    logger.info(
        "Capping %s worker count %d -> %d (rate limit %.2f req/s; extra workers would only queue on the limiter)",
        sampler.name,
        requested,
        cap,
        rate,
    )
    return cap


# APFS, HFS+, and ext4 all cap a SINGLE path component at 255 bytes. The run
# directory is one component whose name embedded every lane verbatim, so the
# roster grew the name linearly: a 15-lane ``all_apis`` dispatch produced a
# 258-byte name and died with ``OSError: [Errno 63] File name too long`` before
# writing a single row. The name is therefore bounded here rather than left to
# the filesystem to reject. 240 leaves headroom for the ``.tmp``/``.partial``
# suffixes a future writer might append to a sibling path.
MAX_RUN_DIR_NAME_BYTES: Final[int] = 240

# How many leading characters of the roster digest go into an elided name.
# Collisions only have to be avoided among runs sharing one timestamp *second*,
# which the timestamp already makes near-impossible; the digest exists so two
# different rosters that share a leading-lane prefix stay visually distinct.
_ROSTER_DIGEST_CHARS: Final[int] = 8


def _roster_segment(samplers: list[str], budget: int) -> str:
    """Render the sampler roster for a run-directory name within ``budget`` bytes.

    Short rosters are joined verbatim, exactly as before, so existing run names
    are unchanged. A roster that would overflow keeps as many leading lane names
    as fit and replaces the tail with ``<n>more-<digest>``. Nothing parses the
    roster back out of the directory name -- ``run.json`` records the full
    ``samplers`` and ``expanded_samplers`` lists -- so the elision loses nothing
    a consumer depends on.
    """
    joined = "+".join(samplers)
    if len(joined.encode()) <= budget:
        return joined

    digest = hashlib.sha256(joined.encode()).hexdigest()[:_ROSTER_DIGEST_CHARS]
    # Reserve room for the WIDEST possible marker up front (the count can only
    # shrink as lanes are kept), so appending a lane can never push the finished
    # segment back over budget.
    reserved = len(f"+{len(samplers)}more-{digest}".encode())
    kept: list[str] = []
    for name in samplers:
        if len("+".join([*kept, name]).encode()) + reserved > budget:
            break
        kept.append(name)
    return "+".join([*kept, f"{len(samplers) - len(kept)}more-{digest}"])


def _digest_run_name(*, ts: str, dataset: str, samplers: list[str], limit: int) -> str:
    """Minimal always-fitting run name, used when the readable one cannot fit.

    ``_roster_segment`` can only shrink the roster; it cannot help when the
    *fixed* parts of the name already consume the whole budget -- ``--limit``
    accepts any positive int, so a caller can hand us a 300-digit ``_n<limit>``
    suffix, and a dataset name is likewise unbounded. Everything variable is
    collapsed into one digest here, keeping only the sortable timestamp prefix
    that ``insights`` relies on for previous-run ordering. Bounded at roughly 46
    bytes regardless of its inputs.
    """
    digest = hashlib.sha256(f"{dataset}|{'+'.join(samplers)}|{limit}".encode()).hexdigest()[:16]
    return f"run_{ts}_benchmark_{digest}"


def make_run_dir(
    *, results_root: str, dataset: str, samplers: list[str], limit: int, timestamp: datetime | None = None
) -> Path:
    ts = (timestamp or datetime.now()).strftime("%Y%m%d_%H%M%S")
    # The ``run_<YYYYmmdd_HHMMSS>`` prefix is load-bearing: ``insights`` finds the
    # previous run by sorting sibling names lexicographically, so the timestamp
    # must stay immediately after ``run_`` and ahead of any elision.
    prefix = f"run_{ts}_benchmark_{dataset}_"
    suffix = f"_n{limit}"
    budget = MAX_RUN_DIR_NAME_BYTES - len(prefix.encode()) - len(suffix.encode())
    name = f"{prefix}{_roster_segment(samplers, budget)}{suffix}"
    # Single authority on the cap. ``budget`` goes non-positive once ``dataset``
    # and ``limit`` alone fill the name, and at that point no roster segment --
    # not even the bare elision marker -- can fit, so trimming lanes cannot
    # rescue it. Verify the finished name instead of trusting the arithmetic,
    # and fall back to the digest form rather than handing the filesystem a
    # component it will reject with errno 63.
    if len(name.encode()) > MAX_RUN_DIR_NAME_BYTES:
        name = _digest_run_name(ts=ts, dataset=dataset, samplers=samplers, limit=limit)
        # Log the SIZES, not the values: the inputs that trigger this are by
        # definition huge, and echoing a 400-digit limit back into the log
        # helps nobody.
        logger.warning(
            "Run directory name would exceed %d bytes (dataset %d chars, limit %d digits, %d lanes); "
            "using the digest form %r instead. The full roster is still recorded in run.json.",
            MAX_RUN_DIR_NAME_BYTES,
            len(dataset),
            len(str(limit)),
            len(samplers),
            name,
        )
    path = Path(results_root) / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def _sampler_config_payload(config: dict) -> dict:
    return {**config, "_schema_version": SAMPLER_CONFIG_SCHEMA_VERSION}


def _optional_row_value(value) -> str | None:
    if not isinstance(value, str) or not bool(pd.notna(value)):
        return None
    value = value.strip()
    return value or None


def write_sampler_config(run_dir: Path, sampler_name: str, config: dict) -> None:
    """Record the knobs this lane ran under, next to its raw CSV.

    Read back by the report/leaderboard (``sampler_config.py``) and by the
    pricing path, which resolves ``nimble_search``'s tier from the run's
    ``search_depth``. It is a provenance artifact only -- nothing consumes it
    as control flow.
    """
    payload = _sampler_config_payload(config)
    (run_dir / f"sampler_config_{sampler_name}.json").write_text(json.dumps(payload, indent=2, default=str))


# Bound on the per-row error column so a verbose upstream HTML/JSON error
# body can't push a single row past ``csv`` reader limits or balloon the
# total artifact size when most rows are failing. 1 KB is enough to
# carry the relevant status code, message, and a snippet of the response
# body for diagnosis; the original full body is still in the sampler's
# in-process state if a deeper drill-down is needed.
_REQUEST_ERROR_MAX_CHARS: Final[int] = 1024


def _extract_request_error(status: str, raw: dict | None) -> str:
    """Return a short, CSV-safe error string for non-ok request rows.

    On ``status == "ok"`` returns the empty string so the column reads as
    "no error" without bloating the artifact. On any failure status, pulls
    the ``error`` key the sampler base stashes in ``raw`` (or the JSON dump
    of ``raw`` as a last resort) and truncates to keep CSV rows bounded.
    """
    if status == "ok":
        return ""
    if not raw:
        return ""
    err = raw.get("error") if isinstance(raw, dict) else None
    if err:
        text = str(err)
    else:
        try:
            text = json.dumps(raw, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            text = str(raw)
    return text[:_REQUEST_ERROR_MAX_CHARS]


async def synthesize_answer(
    *,
    response,
    synthesis_model: str,
    synth_sem: asyncio.Semaphore | None = None,
) -> str:
    """Synthesize the graded answer eval-side from the lane's ranked chunks.

    Used by the ``/search`` lanes, which have no server-side answer to grade.
    The native-LLM answer lanes bypass this entirely and are graded on
    ``response.api_answer``.
    """
    formatted = [f"[{chunk.title}]({chunk.url})\n {chunk.description}" for chunk in response.chunks]
    # Synthesis calls carry the biggest prompts of the eval's three OpenAI
    # call sites, so they run under their own (small) budget. Previously
    # this fan-out was completely unbounded.
    if synth_sem is not None:
        async with synth_sem:
            return await synth(response.query, formatted, synthesis_model)
    return await synth(response.query, formatted, synthesis_model)


# Marker key on a candidate dict signalling "no answer text exists". Consumed
# by ``response_to_rows`` to pick the grade and skip the grader call; stripped
# before the CSV row is built, so it never becomes a column.
_NO_ANSWER_GRADE = "_no_answer_grade"


def _empty_answer_candidate(source: str, *, attempted: bool) -> dict[str, str]:
    """A placeholder row with no answer text.

    ``attempted`` distinguishes the two ways a row can end up answerless:

    * ``True`` -- the request succeeded but produced nothing gradeable (a
      /search lane with no chunks to synthesize from, or an answer lane that
      returned empty text). Graded ``is_not_attempted``, matching how SimpleQA
      classifies a model that declines to answer. This row IS in the accuracy
      denominator (``constants.ACCURACY_DENOMINATOR_RESULTS``) and scores as a
      miss: the call reached the provider and came back 200, so a question the
      lane was served and could not answer counts against it exactly like a
      wrong answer.
    * ``False`` -- the request itself failed. Left ``not_evaluated``; the
      lane's ``failure_rate`` already accounts for it.
    """
    grade = "is_not_attempted" if attempted else "not_evaluated"
    return {"answer_source": source, "answer": "", _NO_ANSWER_GRADE: grade}


async def response_to_rows(
    *,
    response,
    dataset,
    ground_truth: str,
    gold_urls: list[str],
    synthesis_model: str,
    judge_model: str,
    judge_prompt_variant: str,
    judge_relevance_threshold: int,
    answer_type: str | None = None,
    topic: str | None = None,
    skip_llm_judge: bool = False,
    skip_grader: bool = False,
    judge_sem: asyncio.Semaphore | None = None,
    grader_sem: asyncio.Semaphore | None = None,
    synth_sem: asyncio.Semaphore | None = None,
) -> list[dict]:
    chunks = response.chunks_as_dicts()
    predicted_urls = [chunk["url"] for chunk in chunks]
    url_scores = score_urls(predicted=predicted_urls, gold=gold_urls)
    if skip_llm_judge:
        # Skip per-chunk UMBRELA judging entirely. `score_grades([])` returns
        # the zero defaults so the CSV columns still exist (just empty).
        grades: list[int] = []
    else:
        # Per-chunk grades fan out concurrently inside ``judge_chunks`` so a
        # 10-chunk row finishes in one OpenAI round-trip instead of ten. The
        # ``judge_sem`` (when provided) caps the *cross-row* total fan-out
        # against OpenAI's per-minute limits.
        grades = await judge_chunks(
            query=response.query,
            chunks=chunks,
            model=judge_model,
            variant=judge_prompt_variant,
            semaphore=judge_sem,
        )
    llm_scores = score_grades(grades, recall_threshold=judge_relevance_threshold)
    # Fixed per lane, not chosen per run: an answer lane is graded on the text
    # it returned, a /search lane on an answer synthesized from its chunks.
    source = answer_source_for_provider(response.provider)
    if response.status == "ok":
        if source == "api":
            answer = response.api_answer or ""
        else:
            answer = await synthesize_answer(
                response=response,
                synthesis_model=synthesis_model,
                synth_sem=synth_sem,
            )
        if answer and answer.strip():
            candidates = [{"answer_source": source, "answer": answer}]
        else:
            # HTTP 200, but there is nothing to grade -- a /search lane that
            # returned zero chunks, or an answer lane that returned empty text.
            # Emitting no row at all silently dropped the query from the
            # artifact: it vanished from the denominator instead of counting as
            # unanswered. Observed on the 2026-08-08 n=500 run, where two lanes
            # lost queries with no failure logged anywhere. Emit a placeholder
            # row instead.
            logger.warning(
                "No gradeable answer (provider=%s source=%s query=%r chunks=%d); recording as not attempted",
                response.provider,
                source,
                response.query,
                len(response.chunks),
            )
            candidates = [_empty_answer_candidate(source, attempted=True)]
    else:
        candidates = [_empty_answer_candidate(source, attempted=False)]

    rows = []
    for candidate in candidates:
        grade = {"score_name": "not_evaluated"}
        # Placeholder rows carry their grade with them: there is no answer text
        # to grade, so calling the grader would burn an OpenAI round-trip on an
        # empty string to get back the "C" we already know it deserves.
        no_answer_grade = candidate.get(_NO_ANSWER_GRADE)
        # ``skip_grader`` short-circuits the SimpleQA A/B/C accuracy grader
        # (judging/grader.py). The grader does not contribute to the
        # leaderboard's primary retrieval metrics (URL NDCG@10 + LLM recall@10);
        # turning it off cuts ~1-2 OpenAI calls per row x N samplers.
        if no_answer_grade is not None:
            grade = {"score_name": no_answer_grade}
        elif response.status == "ok" and not skip_grader:
            if grader_sem is not None:
                async with grader_sem:
                    grade = await dataset.grader(response.query, ground_truth, candidate["answer"])
            else:
                grade = await dataset.grader(response.query, ground_truth, candidate["answer"])
        # Per-stage latency columns from the sampler's Server-Timing header.
        # KNOWN_SERVER_TIMING_STAGES is the eval-wide contract: every raw row
        # carries one ``stage_<name>_ms`` column per known stage so the
        # analyzer can roll them up into the summary CSV uniformly across
        # samplers (third-party answer APIs that don't emit Server-Timing
        # leave these as ``None``).
        stage_timings = response.latency.stage_timings_ms or {}
        stage_columns = {f"stage_{stage}_ms": stage_timings.get(stage) for stage in KNOWN_SERVER_TIMING_STAGES}
        # When ``status != "ok"`` the sampler base captured the underlying
        # exception in ``response.raw["error"]`` (see ``samplers/base.py``),
        # but the raw dict was previously dropped on the floor before the
        # CSV write. That made the 2026-05-27 n=500 SimpleQA third-party lane
        # failures undiagnosable from the run artifact alone. Surface a
        # bounded slice on every row -- failure rows get the error string,
        # ok rows get a short marker -- so the next run is self-debugging.
        row = {
            "provider": response.provider,
            "response_kind": response.response_kind,
            "query": response.query,
            "ground_truth": ground_truth,
            "generated_answer": candidate["answer"],
            "answer_source_used": candidate["answer_source"],
            "evaluation_result": grade["score_name"],
            "predicted_urls": json.dumps(predicted_urls),
            "chunks_json": json.dumps(chunks, ensure_ascii=False),
            "llm_grades_json": json.dumps(grades),
            "request_status": response.status,
            "request_error": _extract_request_error(response.status, response.raw),
            "request_response_time_ms": response.latency.request_response_time_ms,
            "provider_response_time_ms": response.latency.provider_response_time_ms,
            "internal_response_time_ms": response.latency.internal_response_time_ms,
            **stage_columns,
            "usage_input_tokens": response.usage.input_tokens,
            "usage_output_tokens": response.usage.output_tokens,
            "answer_type": _optional_row_value(answer_type),
            "topic": _optional_row_value(topic),
            **url_scores,
            **llm_scores,
        }
        rows.append(row)
    return rows


async def run_one_sampler(
    *,
    sampler,
    dataset_name: str,
    run_dir: Path,
    synthesis_model: str,
    judge_model: str,
    judge_prompt_variant: str,
    judge_relevance_threshold: int,
    limit: int | None,
    random_state: int | None,
    max_concurrent_tasks: int,
    sampler_config: dict,
    skip_llm_judge: bool = False,
    skip_grader: bool = False,
    judge_concurrency: int | None = None,
    grader_concurrency: int | None = None,
    judge_sem: asyncio.Semaphore | None = None,
    grader_sem: asyncio.Semaphore | None = None,
    synth_sem: asyncio.Semaphore | None = None,
) -> Path:
    dataset = DATASETS[dataset_name]
    df = dataset.load(limit=limit, random_state=random_state)
    csv_path = run_dir / f"dataset_{dataset_name}_raw_results_{sampler.name}.csv"
    write_sampler_config(run_dir, sampler.name, sampler_config)

    # Independent budgets — sampler.sample hits the upstream provider API
    # (slow, latency-bound), while judge_chunks / grader / synth hit OpenAI
    # (fast, rate-limit-bound). Sharing one semaphore for all of them meant a
    # slot was held ~25-35 s per row even though ~25 s of that was OpenAI
    # work that could have used a much wider lane.
    #
    # ``run_benchmark`` passes shared judge/grader/synth semaphores so the
    # OpenAI budgets hold across ALL samplers in a run; the local fallbacks
    # below only fire when this function is driven directly (tests, ad-hoc
    # single-lane scripts). The sampler semaphore is per-lane by design and
    # clamped to what the lane's rate limit can actually keep busy.
    sampler_sem = asyncio.Semaphore(effective_sampler_concurrency(sampler, max_concurrent_tasks))
    judge_sem = judge_sem or asyncio.Semaphore(judge_concurrency or DEFAULT_JUDGE_CONCURRENCY)
    grader_sem = grader_sem or asyncio.Semaphore(grader_concurrency or DEFAULT_GRADER_CONCURRENCY)
    synth_sem = synth_sem or asyncio.Semaphore(DEFAULT_SYNTH_CONCURRENCY)
    failed_queries: list[str] = []

    async def one(row) -> list[dict]:
        query = str(row["problem"])
        try:
            # Hold ``sampler_sem`` only across the slow upstream round-trip.
            # Judging + grading run under their own budgets so a single row
            # no longer monopolises a sampler slot for its OpenAI tail.
            async with sampler_sem:
                logger.debug("Sampling (sampler=%s query=%r)", sampler.name, query)
                response = await sampler.sample(query)
            if response.status != "ok":
                logger.warning(
                    "Sampler request failed (sampler=%s query=%r status=%s): %s",
                    sampler.name,
                    query,
                    response.status,
                    _extract_request_error(response.status, response.raw),
                )
            return await response_to_rows(
                response=response,
                dataset=dataset,
                ground_truth=str(row["answer"]),
                gold_urls=parse_gold_urls_from_metadata(row.get("metadata", "")),
                synthesis_model=synthesis_model,
                judge_model=judge_model,
                judge_prompt_variant=judge_prompt_variant,
                judge_relevance_threshold=judge_relevance_threshold,
                answer_type=row.get("answer_type"),
                topic=row.get("topic"),
                skip_llm_judge=skip_llm_judge,
                skip_grader=skip_grader,
                judge_sem=judge_sem,
                grader_sem=grader_sem,
                synth_sem=synth_sem,
            )
        except Exception:
            # Don't let one failing row (transient API blip, malformed
            # SERP, etc.) cancel in-flight tasks. The row is left unwritten
            # and the run fails after all in-flight work has flushed, so a
            # partial CSV is not mistaken for a complete benchmark.
            logger.exception("Row failed for sampler=%s query=%r", sampler.name, query)
            failed_queries.append(query)
            return []

    tasks = [one(row) for _, row in df.iterrows()]
    # ``w``: a run owns its CSV outright. Appending was only ever there to let a
    # later invocation top up a partial file; without that flow, appending into
    # a reused run dir would just duplicate rows.
    write_header = True
    with csv_path.open("w", encoding="utf-8") as fp:
        for future in tqdm_asyncio.as_completed(tasks, desc=sampler.name):
            rows = await future
            if rows:
                pd.DataFrame(rows).to_csv(fp, header=write_header, index=False)
                write_header = False
                fp.flush()
    if failed_queries:
        raise RuntimeError(
            f"{sampler.name} failed {len(failed_queries)} row(s). "
            f"Partial results were written to {csv_path}; re-run the benchmark to score this lane in full. "
            f"First failures: {failed_queries[:5]}"
        )
    return csv_path


async def run_benchmark(
    *,
    samplers: list,
    dataset_name: str,
    run_dir: Path,
    synthesis_model: str,
    judge_model: str,
    judge_prompt_variant: str,
    judge_relevance_threshold: int,
    limit: int | None,
    random_state: int | None,
    max_concurrent_tasks: int,
    sampler_configs: dict[str, dict],
    skip_llm_judge: bool = False,
    skip_grader: bool = False,
    judge_concurrency: int | None = None,
    grader_concurrency: int | None = None,
    synth_concurrency: int | None = None,
) -> list[Path]:
    # One judge/grader/synth budget for the WHOLE run, not per sampler --
    # the OpenAI fan-out must not scale with the number of samplers.
    judge_sem = asyncio.Semaphore(judge_concurrency or DEFAULT_JUDGE_CONCURRENCY)
    grader_sem = asyncio.Semaphore(grader_concurrency or DEFAULT_GRADER_CONCURRENCY)
    synth_sem = asyncio.Semaphore(synth_concurrency or DEFAULT_SYNTH_CONCURRENCY)

    # ``return_exceptions=True`` so one sampler's failure doesn't cancel the
    # others mid-flight (a bare gather re-raises immediately, and the
    # still-running siblings then die noisily during asyncio.run() shutdown).
    # Every sampler finishes -- writing whatever rows it could -- before the
    # combined failure is raised.
    results = await asyncio.gather(
        *[
            run_one_sampler(
                sampler=sampler,
                dataset_name=dataset_name,
                run_dir=run_dir,
                synthesis_model=synthesis_model,
                judge_model=judge_model,
                judge_prompt_variant=judge_prompt_variant,
                judge_relevance_threshold=judge_relevance_threshold,
                limit=limit,
                random_state=random_state,
                max_concurrent_tasks=max_concurrent_tasks,
                sampler_config=sampler_configs[sampler.name],
                skip_llm_judge=skip_llm_judge,
                skip_grader=skip_grader,
                judge_sem=judge_sem,
                grader_sem=grader_sem,
                synth_sem=synth_sem,
            )
            for sampler in samplers
        ],
        return_exceptions=True,
    )

    failures = [
        (sampler.name, result)
        for sampler, result in zip(samplers, results, strict=True)
        if isinstance(result, BaseException)
    ]
    if failures:
        for name, exc in failures:
            logger.error("Sampler %s failed", name, exc_info=exc)
        failed_names = ", ".join(name for name, _ in failures)
        raise RuntimeError(
            f"{len(failures)}/{len(samplers)} sampler(s) failed: {failed_names}. "
            "Completed samplers' CSVs are intact; re-run the benchmark to score the failed lane(s)."
        ) from failures[0][1]
    return [result for result in results if isinstance(result, Path)]

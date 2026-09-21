"""CLI entrypoint."""

from __future__ import annotations

import argparse
import asyncio
import logging
from datetime import UTC, datetime
from pathlib import Path

from nimble_benchmark.analyzer import (
    DEFAULT_MAX_FAILURE_RATE,
    DEFAULT_MIN_SUCCESSFUL_PROBLEMS,
    aggregate_run,
)
from nimble_benchmark.config import (
    SYNTHESIS_MODEL_CHOICES,
    Settings,
    configure_openai_environment,
    require_credentials_for_samplers,
    require_openai_for_benchmark,
    synthesis_model_supports_eval_side_synthesis,
)
from nimble_benchmark.datasets.base import DEFAULT_RANDOM_STATE
from nimble_benchmark.insights import write_errors_md, write_insights_md
from nimble_benchmark.judging.grader import set_grader_model
from nimble_benchmark.logging_setup import DEFAULT_CONSOLE_LOG_LEVEL, attach_run_log, setup_logging
from nimble_benchmark.preflight import preflight_or_die
from nimble_benchmark.report import write_failures_json, write_run_json, write_run_md
from nimble_benchmark.runner import (
    DEFAULT_GRADER_CONCURRENCY,
    DEFAULT_JUDGE_CONCURRENCY,
    DEFAULT_SYNTH_CONCURRENCY,
    make_run_dir,
    run_benchmark,
)
from nimble_benchmark.samplers import (
    ALIASES,
    EXCLUDED_SAMPLERS,
    PROVIDER_CAPABILITIES,
    build_samplers,
    expand_sampler_names,
)
from nimble_benchmark.samplers.base import BaseSampler

NIMBLE_SAMPLERS: frozenset[str] = frozenset({"nimble_search"})
# Only a Nimble lane pointed at its own base URL would record ``base_url`` in
# its recorded config: the default lanes all share one code-default URL, so
# pinning it would add noise for no signal. Sourced from ``samplers`` so the
# exclusion set is defined in exactly one place (it is empty today).
NIMBLE_EXCLUDED_SAMPLERS: frozenset[str] = EXCLUDED_SAMPLERS


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be > 0")
    return parsed


def relevance_threshold(value: str) -> int:
    parsed = int(value)
    if not 0 <= parsed <= 3:
        raise argparse.ArgumentTypeError("must be between 0 and 3")
    return parsed


def non_negative_int(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be >= 0")
    return parsed


def unit_fraction(value: str) -> float:
    """Parse a 0.0-1.0 fraction used for the failure-rate gate.

    Accepts the inclusive range so ``--max-failure-rate 1.0`` disables the
    gate entirely (every lane is treated as healthy) and
    ``--max-failure-rate 0.0`` excludes any lane with even one failure.
    """
    parsed = float(value)
    if not 0.0 <= parsed <= 1.0:
        raise argparse.ArgumentTypeError("must be between 0.0 and 1.0")
    return parsed


def sampler_name(value: str) -> str:
    try:
        expand_sampler_names([value])
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc
    return value


def _sampler_help() -> str:
    aliases = ", ".join(sorted(ALIASES))
    sampler_names = ", ".join(sorted(PROVIDER_CAPABILITIES))
    return (
        "Sampler names or aliases. /search lanes are graded on an answer "
        "synthesized eval-side from their ranked results; the native-LLM "
        "answer lanes are graded on the answer they return. "
        f"Aliases: {aliases}. Samplers: {sampler_names}."
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="nimble-eval")
    parser.add_argument("--dataset", default="simpleqa", choices=["simpleqa"])
    parser.add_argument("--limit", type=positive_int, default=500)
    parser.add_argument(
        "--random-state",
        type=int,
        default=DEFAULT_RANDOM_STATE,
        help=(
            "Seed for SimpleQA row sampling. Default 0 matches the canonical "
            "implementation in openai/simple-evals (`random.Random(0).sample"
            "(examples, num_examples)`), so a `--limit N` slice picks the same "
            "rows every benchmark reports against. Pass any other int to draw "
            "a different reproducible subset."
        ),
    )
    parser.add_argument(
        "--samplers",
        nargs="+",
        default=["nimble_search"],
        type=sampler_name,
        metavar="SAMPLER",
        help=_sampler_help(),
    )
    parser.add_argument(
        "--max-concurrent-tasks",
        type=positive_int,
        default=40,
        help=(
            "Maximum concurrent calls to the sampler under test (Nimble or "
            "third-party /search APIs). Latency-bound — bump until the upstream "
            "starts contention-flat-lining (p95 climbs without a throughput gain) "
            "or you hit its rate limit. Tune downward when running against a "
            "rate-limited third-party API. Note that a lane which self-throttles "
            "clamps this to what its rate limit can keep busy."
        ),
    )
    parser.add_argument(
        "--judge-concurrency",
        type=positive_int,
        default=None,
        help=(
            "Maximum concurrent OpenAI calls for UMBRELA per-chunk relevance "
            "judging, shared across ALL samplers in the run (default "
            f"{DEFAULT_JUDGE_CONCURRENCY}). Bound separately so the "
            "rate-limit-bound judge fan-out doesn't share a budget with the "
            "latency-bound sampler. Ignored when --skip-llm-judge is set."
        ),
    )
    parser.add_argument(
        "--grader-concurrency",
        type=positive_int,
        default=None,
        help=(
            "Maximum concurrent OpenAI calls for the SimpleQA A/B/C grader, "
            f"shared across ALL samplers in the run (default {DEFAULT_GRADER_CONCURRENCY}). "
            "Bound separately so the grader's OpenAI calls run on a different "
            "budget than the slow sampler round-trip."
        ),
    )
    parser.add_argument(
        "--synth-concurrency",
        type=positive_int,
        default=None,
        help=(
            "Maximum concurrent OpenAI calls for eval-side answer synthesis, "
            f"shared across ALL samplers in the run (default {DEFAULT_SYNTH_CONCURRENCY}). "
            "Only the /search lanes synthesize."
        ),
    )
    parser.add_argument(
        "--log-level",
        default=DEFAULT_CONSOLE_LOG_LEVEL,
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help=(
            "Console log verbosity, default ERROR -- a lane failing every row "
            "(an expired API key, an exhausted quota) no longer prints one "
            "warning per question. Nothing is lost: the log FILE always captures "
            "DEBUG regardless of this flag (see --log-dir), and errors.md is "
            "built from the run's CSVs, not from log records. Pass INFO to get "
            "the progress commentary and the 'this run had issues' pointer back."
        ),
    )
    parser.add_argument(
        "--log-dir",
        default="logs",
        help=(
            "Directory for the per-invocation DEBUG log file. A copy of all "
            "records emitted after the run directory is created is also "
            "written to <run-dir>/eval.log so every run artifact carries its "
            "own log."
        ),
    )
    parser.add_argument(
        "--synthesis-model",
        default=SYNTHESIS_MODEL_CHOICES[0],
        choices=list(SYNTHESIS_MODEL_CHOICES),
        help=(
            "Model used to synthesize the graded answer from a /search lane's "
            "ranked results (OpenAI models only). Every `synth` row's accuracy "
            "is graded off this synthesis, so changing it moves all of them. "
            "Does not affect the native-LLM `api` rows, which are graded on the "
            "answer they return."
        ),
    )
    parser.add_argument("--grader-model", default="gpt-4o")
    parser.add_argument("--judge-model", default="gpt-4o-2024-08-06")
    parser.add_argument("--judge-prompt-variant", default="passage", choices=["passage", "url-only"])
    parser.add_argument("--judge-relevance-threshold", type=relevance_threshold, default=2)
    parser.add_argument(
        "--skip-llm-judge",
        action="store_true",
        help=(
            "Skip UMBRELA per-chunk LLM judging. URL retrieval metrics are still "
            "computed; LLM-judged recall/hit columns are emitted as zero."
        ),
    )
    parser.add_argument(
        "--skip-grader",
        action="store_true",
        help=(
            "Skip the SimpleQA A/B/C accuracy grader call. URL-binary and "
            "LLM-judged retrieval metrics (Recall/Hit) are unaffected; "
            "``evaluation_result`` rows are emitted as ``not_evaluated`` and "
            "``accuracy_score`` aggregates as NaN. Use when the report's "
            "primary metrics are recall_at_10_llm and ndcg_at_10 (the "
            "headline) — saves ~1 OpenAI call per row x N samplers, "
            "which dominates CI wall-clock once UMBRELA is on a small model."
        ),
    )
    parser.add_argument("--dry-run", action="store_true", help="Validate selected sampler credentials and exit.")
    parser.add_argument("--results-dir", default="runs")
    parser.add_argument(
        "--run-dir",
        default=None,
        help=(
            "Write this run's artifacts into an explicit directory instead of "
            "creating a new ``runs/run_<ts>_...`` one (handy for scripted "
            "reruns that need a deterministic path). Each lane's CSV is "
            "written from scratch, so pointing at a directory that already "
            "holds a run overwrites its raw results. Overrides "
            "``--results-dir``."
        ),
    )
    parser.add_argument(
        "--significance-baseline",
        default="auto",
        help=(
            "Baseline system for paired-t significance testing. Every lane in "
            "the run is tested against this one system. `auto` picks "
            "`nimble_search`, falling back to the first system by sort order "
            "when that lane is not in the roster. Otherwise pass a bare "
            "provider name (e.g. `nimble_search`) or the full slash-joined "
            "system key (e.g. `nimble_search/search_results/synth`)."
        ),
    )
    parser.add_argument(
        "--min-successful-problems",
        type=non_negative_int,
        default=DEFAULT_MIN_SUCCESSFUL_PROBLEMS,
        help=(
            "Minimum number of ok-status rows a lane must have to count "
            "toward the leaderboard "
            "ranking and the significance baseline pool. Lanes below the gate "
            "still ship in the analyzed CSV (with failure_rate populated) so "
            "the report can surface them as 'excluded'. Default "
            f"{DEFAULT_MIN_SUCCESSFUL_PROBLEMS} matches the floor below which "
            "paired-t comparisons stop being meaningful. Pass 0 to disable."
        ),
    )
    parser.add_argument(
        "--max-failure-rate",
        type=unit_fraction,
        default=DEFAULT_MAX_FAILURE_RATE,
        help=(
            "Fraction of attempted problems a lane may fail before it is "
            "excluded from the leaderboard ranking and significance baseline "
            "pool. Default "
            f"{DEFAULT_MAX_FAILURE_RATE} keeps catastrophic-failure lanes "
            "(e.g. a sampler that 429'd on 96/100 queries) from sitting next "
            "to healthy lanes in the headline table. Pass 1.0 to disable. "
            "Lanes between 0 and this ceiling still get headline metrics "
            "computed -- always over their successful subset -- and the "
            "report surfaces the failure rate so the survivor sample is "
            "interpretable."
        ),
    )
    return parser


async def _amain(args: argparse.Namespace, settings: Settings) -> None:
    expanded_sampler_names = expand_sampler_names(args.samplers)
    require_credentials_for_samplers(settings, expanded_sampler_names)
    # Every run synthesizes its graded answers eval-side through the OpenAI
    # client, so a non-OpenAI --synthesis-model can never work; fail before
    # spending a single upstream request rather than at the first grade.
    if not synthesis_model_supports_eval_side_synthesis(args.synthesis_model):
        # Derived from the choices tuple rather than hand-enumerated so a new
        # entry can't leave this message advertising a stale roster.
        supported_choices = ", ".join(
            model for model in SYNTHESIS_MODEL_CHOICES if synthesis_model_supports_eval_side_synthesis(model)
        )
        raise RuntimeError(
            "Every graded answer is synthesized eval-side through the OpenAI chat-completions "
            f"client, but --synthesis-model={args.synthesis_model} cannot be called that way "
            "(either it is not an OpenAI model, or it is a '-pro' reasoning model, which OpenAI "
            "does not serve on /v1/chat/completions). "
            f"Pick a supported entry from SYNTHESIS_MODEL_CHOICES ({supported_choices})."
        )
    if args.dry_run:
        sampler_list = ", ".join(expanded_sampler_names)
        print(f"credential check OK — {len(expanded_sampler_names)} samplers configured: {sampler_list}")
        return

    require_openai_for_benchmark(settings)
    configure_openai_environment(settings)
    set_grader_model(args.grader_model)
    skipped_samplers = await preflight_or_die(
        base_url=settings.nimble_base_url,
        api_key=settings.nimble_api_key.get_secret_value() if settings.nimble_api_key is not None else None,
        sampler_names=expanded_sampler_names,
        settings=settings,
    )
    if skipped_samplers:
        # Preflight returned a list of lenient lanes (there are none today --
        # see ``preflight.LENIENT_LANES``) that hit transient 5xx / connection
        # errors. Drop them from the run so the eval continues with the
        # remaining samplers; the exclusion is surfaced in run.md so the
        # dispatcher notices the missing row at review time.
        skipped_set = set(skipped_samplers)
        expanded_sampler_names = [name for name in expanded_sampler_names if name not in skipped_set]
        logging.warning(
            "preflight excluded %d sampler(s) from this run due to transient upstream failures: %s",
            len(skipped_samplers),
            ", ".join(skipped_samplers),
        )
        if not expanded_sampler_names:
            raise RuntimeError(
                "preflight excluded every requested sampler "
                f"({', '.join(skipped_samplers)}); refusing to run an empty eval."
            )
    samplers = build_samplers(settings=settings, sampler_names=expanded_sampler_names)
    started_at = datetime.now(UTC)
    if args.run_dir:
        # Explicit output directory. ``mkdir(exist_ok=True)`` so a caller can
        # name either a fresh path or an existing one; the lane CSVs are
        # rewritten either way.
        run_dir = Path(args.run_dir)
        run_dir.mkdir(parents=True, exist_ok=True)
    else:
        run_dir = make_run_dir(
            results_root=args.results_dir,
            dataset=args.dataset,
            samplers=expanded_sampler_names,
            limit=args.limit,
            timestamp=started_at,
        )
    # From here on, everything also lands in <run_dir>/eval.log so the run
    # artifact is self-contained for post-mortem debugging.
    run_log = attach_run_log(run_dir)
    logging.getLogger(__name__).info("Run log: %s", run_log)
    sampler_configs = {sampler.name: _sampler_config(sampler, args) for sampler in samplers}
    await run_benchmark(
        samplers=samplers,
        dataset_name=args.dataset,
        run_dir=run_dir,
        synthesis_model=args.synthesis_model,
        judge_model=args.judge_model,
        judge_prompt_variant=args.judge_prompt_variant,
        judge_relevance_threshold=args.judge_relevance_threshold,
        limit=args.limit,
        random_state=args.random_state,
        max_concurrent_tasks=args.max_concurrent_tasks,
        judge_concurrency=args.judge_concurrency,
        grader_concurrency=args.grader_concurrency,
        synth_concurrency=args.synth_concurrency,
        sampler_configs=sampler_configs,
        skip_llm_judge=args.skip_llm_judge,
        skip_grader=args.skip_grader,
    )
    aggregate_run(
        run_dir=run_dir,
        dataset_name=args.dataset,
        significance_baseline=args.significance_baseline,
        min_successful_problems=args.min_successful_problems,
        max_failure_rate=args.max_failure_rate,
    )
    finished_at = datetime.now(UTC)
    args_dict = vars(args) | {
        "expanded_samplers": expanded_sampler_names,
        "preflight_skipped_samplers": skipped_samplers,
        "nimble_base_url": settings.nimble_base_url,
        "nimble_search_depth": settings.nimble_search_depth,
    }
    write_run_md(run_dir=run_dir, args=args_dict, started_at=started_at, finished_at=finished_at)
    write_run_json(run_dir=run_dir, args=args_dict, started_at=started_at, finished_at=finished_at)
    write_failures_json(run_dir=run_dir)
    # Interpretation layer, written last so it can read every artifact above.
    # Never allowed to fail the run: a benchmark that produced good CSVs must
    # not be marked failed because a summary writer tripped over an edge case.
    try:
        insights_path = write_insights_md(
            run_dir=run_dir, args=args_dict, started_at=started_at, finished_at=finished_at
        )
        errors_path = write_errors_md(run_dir=run_dir, args=args_dict)
        logger = logging.getLogger(__name__)
        logger.info("Insights: %s", insights_path)
        if errors_path.stat().st_size:
            logger.warning("This run had issues — see %s", errors_path)
        else:
            logger.info("No issues detected; %s is empty", errors_path)
    except Exception:
        logging.getLogger(__name__).exception("Failed to write insights/errors digest (run results are unaffected)")
    print(f"\nResults in: {run_dir}\n")


def _sampler_config(sampler: BaseSampler, args: argparse.Namespace) -> dict:
    config = {
        "limit": args.limit,
        "random_state": args.random_state,
        "search_depth": getattr(sampler, "search_depth", None),
    }
    # Recorded only when a lane opts in, so every other lane's config hash is
    # unchanged. No shipped lane sets it today; the branch stays so a lane that
    # does opts in without touching this function.
    if getattr(sampler, "full_content", False):
        config["full_content"] = True
    if sampler.name in NIMBLE_SAMPLERS:
        if sampler.name in NIMBLE_EXCLUDED_SAMPLERS:
            config["base_url"] = getattr(sampler, "base_url", None)
    elif sampler.name in {"exa_search_auto", "exa_search_fast"}:
        # ``search_type`` is what separates the two Exa lanes, so the recorded
        # config has to carry it -- otherwise the artifact cannot say which
        # product tier the row measured.
        config.update(
            {
                "base_url": getattr(sampler, "base_url", None),
                "search_type": getattr(sampler, "search_type", None),
                "summary": getattr(sampler, "summary", None),
                "num_results": getattr(sampler, "num_results", None),
                "timeout_s": getattr(sampler, "_timeout_s", None),
            }
        )
    elif sampler.name in {"parallel_search_basic", "parallel_search_turbo"}:
        # ``mode`` is what separates the two Parallel lanes, so the recorded
        # config has to carry it -- otherwise the artifact cannot say which
        # product tier the row measured.
        config.update(
            {
                "base_url": getattr(sampler, "base_url", None),
                "mode": getattr(sampler, "mode", None),
                "max_results": getattr(sampler, "max_results", None),
                "timeout_s": getattr(sampler, "_timeout_s", None),
            }
        )
    elif sampler.name == "firecrawl_search":
        config.update(
            {
                "base_url": getattr(sampler, "api_url", None),
                "num_results": getattr(sampler, "num_results", None),
                "rate_per_second": getattr(sampler, "rate_per_second", None),
                "timeout_s": getattr(sampler, "_timeout_s", None),
            }
        )
    return config


def main() -> None:
    args = build_parser().parse_args()
    log_file = setup_logging(console_level=args.log_level, log_dir=args.log_dir)
    logger = logging.getLogger(__name__)
    logger.info("Debug log: %s", log_file)
    try:
        asyncio.run(_amain(args, Settings()))
    except KeyboardInterrupt:
        logger.warning("Interrupted by user; any partial results are in the run directory.")
        raise SystemExit(130) from None
    except Exception:
        # Full traceback goes to the DEBUG log file as well as the console,
        # so a crashed CI/overnight run is diagnosable from the artifact.
        logger.critical("Run failed; see %s for the full DEBUG trace.", log_file, exc_info=True)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()

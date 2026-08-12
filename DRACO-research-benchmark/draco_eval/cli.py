"""draco-eval command line interface."""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Callable
from pathlib import Path

from dotenv import load_dotenv

from draco_eval import dataset, report
from draco_eval.judge import DEFAULT_JUDGE_MODEL, DEFAULT_JUDGE_SAMPLES, Judge
from draco_eval.runner import run as run_eval
from draco_eval.systems import System
from draco_eval.systems.chatgpt import ChatGPT
from draco_eval.systems.exa import Exa
from draco_eval.systems.gemini import Gemini
from draco_eval.systems.nimble import Nimble
from draco_eval.systems.parallel import Parallel

#: name -> (adapter, default tier). Importing an adapter is cheap: each one
#: loads its vendor SDK inside __init__, so a missing SDK never breaks the
#: others or the --help output.
SYSTEMS: dict[str, tuple[Callable[[str], System], str]] = {
    "exa": (Exa, "auto"),
    "parallel": (Parallel, "core"),
    "nimble": (Nimble, "high"),
    "chatgpt": (ChatGPT, "gpt-5.5"),
    "gemini": (Gemini, "preview"),
}


def build_system(spec: str) -> System:
    """Build a system from a "name/tier" spec, e.g. "exa/high"."""
    name, _, tier = spec.partition("/")
    if name not in SYSTEMS:
        raise SystemExit(f"Unknown system {name!r}. Built-in: {', '.join(SYSTEMS)}. Or import your own — see README.")
    adapter, default_tier = SYSTEMS[name]
    return adapter(tier or default_tier)


def _cmd_run(args: argparse.Namespace) -> None:
    items = dataset.load(
        cache=Path(args.cache) if args.cache else None,
        limit=args.limit,
        domains=args.domain,
    )
    if not items:
        raise SystemExit("no items matched the filters")

    system = build_system(args.system)
    judge = Judge(args.judge_model, concurrency=args.judge_concurrency, samples=args.judge_samples)
    out = Path(args.out or f"results/{args.system.replace('/', '_')}.jsonl")

    asyncio.run(
        run_eval(
            items=items,
            system=system,
            judge=judge,
            out_path=out,
            concurrency=args.concurrency,
            timeout_s=args.timeout,
            keep_verdicts=not args.no_verdicts,
            resume=not args.no_resume,
        )
    )

    summary = report.summarise(report.load(out))
    print("\n" + report.table([summary]))
    print(f"\nper-item results: {out}")


def _cmd_report(args: argparse.Namespace) -> None:
    summaries = [report.summarise(report.load(Path(p))) for p in args.results]
    print(report.table(summaries))


def main() -> None:
    load_dotenv()  # README tells users to put their keys in .env
    parser = argparse.ArgumentParser(prog="draco-eval", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="run a system over DRACO and judge it")
    run_p.add_argument("--system", required=True, help="system spec, e.g. exa/high, nimble/max, parallel/core")
    run_p.add_argument("--judge-model", default=DEFAULT_JUDGE_MODEL, help=f"default: {DEFAULT_JUDGE_MODEL}")
    run_p.add_argument("--limit", type=int, help="evaluate only the first N items (smoke test)")
    run_p.add_argument("--domain", action="append", help="restrict to a domain; repeatable")
    run_p.add_argument("--concurrency", type=int, default=4, help="items in flight (default: 4)")
    run_p.add_argument(
        "--judge-samples",
        type=int,
        default=DEFAULT_JUDGE_SAMPLES,
        help=f"independent verdicts per criterion, averaged (default: {DEFAULT_JUDGE_SAMPLES}); cost scales with this",
    )
    run_p.add_argument("--judge-concurrency", type=int, default=8, help="judge calls in flight, across the whole run")
    run_p.add_argument("--timeout", type=float, default=3600, help="per-item system timeout in seconds")
    run_p.add_argument("--out", help="results JSONL path (default: results/<system>.jsonl)")
    run_p.add_argument("--cache", default="data/draco.jsonl", help="local dataset cache; '' to always refetch")
    run_p.add_argument("--no-verdicts", action="store_true", help="omit per-criterion verdicts and answers")
    run_p.add_argument("--no-resume", action="store_true", help="re-run items already present in --out")
    run_p.set_defaults(func=_cmd_run)

    rep_p = sub.add_parser("report", help="build a comparison table from result files")
    rep_p.add_argument("results", nargs="+", help="one or more results JSONL files")
    rep_p.set_defaults(func=_cmd_report)

    args = parser.parse_args()
    try:
        args.func(args)
    except (RuntimeError, ValueError) as e:
        # Missing key, missing extra, bad tier — a one-line message beats a
        # traceback for what are all user-fixable setup problems.
        raise SystemExit(f"error: {e}") from e


if __name__ == "__main__":
    main()

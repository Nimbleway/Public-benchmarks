"""Paths, providers and settings shared by every command.

Every command takes the benchmark to work on as --suite <folder> (a path, or a folder name at the repo root):
its tasks.json is <folder>/tasks.json and all outputs go to <folder>/results/. summary takes --suite more than once
to add an "across suites" view; the first suite is the one it writes. The flag is read here, at import, and removed
from sys.argv, so each command's own arguments are what is left.
The judge's page cache goes to common/agent-bench/evidence_cache/ (not shipped, gitignored), shared by all suites.
"""
import json
import os
import sys
from datetime import date

PKG = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(PKG)  # common/agent-bench: .env, the evidence cache, and the working folder of Claude Code calls
REPO = os.path.dirname(os.path.dirname(PROJECT))
EVID = os.path.join(PROJECT, "evidence_cache")


def suite_path(s):
    """A suite folder given as a path, or as a folder name at the repo root."""
    return os.path.abspath(s if os.path.exists(s) else os.path.join(REPO, s))


def _suites(argv):
    """Remove every `--suite <folder>` from argv and return the folders, resolved."""
    out = []
    while "--suite" in argv:
        k = argv.index("--suite")
        if k + 1 >= len(argv):
            sys.exit("--suite needs a folder")
        s = argv.pop(k + 1)
        argv.pop(k)
        out.append(suite_path(s))
    return out


SUITES = _suites(sys.argv)
SUITE = SUITES[0] if SUITES else None
TASKS = SUITE and os.path.join(SUITE, "tasks.json")
RESULTS = SUITE and os.path.join(SUITE, "results")
RAW = SUITE and os.path.join(RESULTS, "raw")
VERIFIED = SUITE and os.path.join(RESULTS, "verified")
KEYS = SUITE and os.path.join(RESULTS, "answer_keys")
KEY_AUDIT = SUITE and os.path.join(RESULTS, "key_audit")

# Provider order matters: the judge's anonymous labels are a seeded shuffle of this list.
PROVIDERS = ["nimble", "exa", "parallel", "firecrawl", "gpt", "opus"]
NAMES = {"nimble": "Nimble", "exa": "Exa", "parallel": "Parallel", "firecrawl": "Firecrawl",
         "gpt": "GPT-6.1 Sol", "opus": "Claude Opus 5.5"}


def load_env(path=os.path.join(PROJECT, ".env")):
    """Read KEY=VALUE lines from .env into os.environ (existing variables win)."""
    if not os.path.exists(path):
        return
    for line in open(path):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            if v.strip():
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def load_tasks(suite=None):
    """One entry per job: id, name, vertical, tier, instructions, output_schema, inputs, unit, judge and scoring rules."""
    suite = suite or SUITE
    if not suite:
        sys.exit("--suite <folder> is required, e.g. --suite Finance-Use-Case-Benchmark")
    return json.load(open(os.path.join(suite, "tasks.json")))


def today():
    """The run / judge date. Instructions and judge prompts use it; BENCH_TODAY overrides it (YYYY-MM-DD)."""
    return os.environ.get("BENCH_TODAY") or date.today().isoformat()


load_env()

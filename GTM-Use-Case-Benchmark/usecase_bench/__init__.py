"""GTM use-case benchmark for web research agents (v2).

Paths: tasks.json lives next to this file; all outputs go to <folder>/results/ (override with BENCH_RESULTS).
The judge's page cache goes to <folder>/evidence_cache/ (not shipped, gitignored).
"""
import json
import os
from datetime import date

PKG = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(PKG)
RESULTS = os.environ.get("BENCH_RESULTS") or os.path.join(ROOT, "results")
RAW = os.path.join(RESULTS, "raw")
VERIFIED = os.path.join(RESULTS, "verified")
KEYS = os.path.join(RESULTS, "answer_keys")
KEY_AUDIT = os.path.join(RESULTS, "key_audit")
EVID = os.path.join(ROOT, "evidence_cache")

# Provider order matters: the judge's anonymous labels are a seeded shuffle of this list.
PROVIDERS = ["nimble", "exa", "parallel", "firecrawl", "gpt", "opus"]
NAMES = {"nimble": "Nimble", "exa": "Exa", "parallel": "Parallel", "firecrawl": "Firecrawl",
         "gpt": "GPT-6.1 Sol", "opus": "Claude Opus 5.5"}


def load_env(path=os.path.join(ROOT, ".env")):
    """Read KEY=VALUE lines from .env into os.environ (existing variables win)."""
    if not os.path.exists(path):
        return
    for line in open(path):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            if v.strip():
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def load_tasks(root=ROOT):
    """One entry per job: id, name, vertical, tier, instructions, output_schema, inputs, unit and judge rules."""
    return json.load(open(os.path.join(root, "usecase_bench", "tasks.json")))


def today():
    """The run / judge date. Instructions and judge prompts use it; BENCH_TODAY overrides it (YYYY-MM-DD)."""
    return os.environ.get("BENCH_TODAY") or date.today().isoformat()


load_env()

"""Finance use-case benchmark for web research agents.

Paths: tasks.json lives next to this file; all outputs go to <repo>/results/ (override with BENCH_RESULTS).
"""
import json
import os

PKG = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(PKG)
RESULTS = os.environ.get("BENCH_RESULTS") or os.path.join(ROOT, "results")
RAW = os.path.join(RESULTS, "raw")
CLAIMS = os.path.join(RESULTS, "claims")
VERIFIED = os.path.join(RESULTS, "verified")


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


def load_tasks():
    """One entry per use case: id, name, vertical, tier, instructions, output_schema, inputs, scoring and judge rules."""
    return json.load(open(os.path.join(PKG, "tasks.json")))


load_env()

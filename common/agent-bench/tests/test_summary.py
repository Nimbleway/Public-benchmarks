"""Smoke test: recompute the summary from the shipped results (offline) and compare with results/summary.json.

    uv run pytest      # or: python -m pytest
"""
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SUITES = ["Finance-Use-Case-Benchmark", "GTM-Use-Case-Benchmark"]


def rounded(x):
    if isinstance(x, float):
        return round(x, 3)
    if isinstance(x, dict):
        return {k: rounded(v) for k, v in x.items()}
    if isinstance(x, list):
        return [rounded(v) for v in x]
    return x


def test_summary_matches_shipped(tmp_path):
    # copy this suite (and the sibling suite, for the cross-suite view) so the shipped summary.json is not overwritten
    for name in SUITES:
        src = os.path.join(os.path.dirname(HERE), name)
        if os.path.exists(os.path.join(src, "usecase_bench", "tasks.json")):
            shutil.copytree(src, tmp_path / name, ignore=shutil.ignore_patterns(".venv", "__pycache__", "evidence_cache", ".env"))
    work = tmp_path / os.path.basename(HERE)
    env = {k: v for k, v in os.environ.items() if k != "BENCH_RESULTS"}
    subprocess.run([sys.executable, "-m", "usecase_bench.summary"], cwd=work, env=env, check=True, capture_output=True)
    shipped = json.load(open(os.path.join(HERE, "results", "summary.json")))
    fresh = json.load(open(work / "results" / "summary.json"))
    assert rounded(fresh) == rounded(shipped)

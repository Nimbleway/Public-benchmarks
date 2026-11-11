"""Fetch a page as markdown through Nimble Extract (renders JS, handles PDFs and most blocks). The judge's verifier,
retry pass, audit and answer-key writer may call it through Bash for pages their own WebFetch cannot read. It is used
the same way for every provider's citations and never sees which provider cited a page.

    python -m usecase_bench.fetch <url>      (run from the suite folder; needs NIMBLE_API_KEY)
"""
import json
import sys

from nimble_python import Nimble

from . import load_env

load_env()

if __name__ == "__main__":
    r = Nimble().extract.run(url=sys.argv[1], render=True, formats=["markdown"])
    d = r.model_dump() if hasattr(r, "model_dump") else r
    md = (d.get("data") or {}).get("markdown") or d.get("markdown") or json.dumps(d)[:20000]
    print(md[:40000])

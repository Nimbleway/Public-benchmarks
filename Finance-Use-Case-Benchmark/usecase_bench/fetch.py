"""Fetch a page as markdown through Nimble Extract (renders JS, handles PDFs and most blocks). Used by the judge
only when its own WebFetch fails.   python3 fetch.py <url>   (needs NIMBLE_API_KEY)"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import usecase_bench  # noqa: F401,E402  (loads .env)
from nimble_python import Nimble  # noqa: E402

r = Nimble().extract.run(url=sys.argv[1], render=True, formats=["markdown"])
d = r.model_dump() if hasattr(r, "model_dump") else r
md = (d.get("data") or {}).get("markdown") or d.get("markdown") or json.dumps(d)[:20000]
print(md[:40000])

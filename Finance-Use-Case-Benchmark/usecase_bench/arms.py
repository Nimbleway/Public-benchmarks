"""One function per provider. Same instructions, same task, same output schema. Each returns
{arm, tier, status, seconds, output, cost_usd, cost_basis, run_id, error}.

Price-matched tiers (list price per run):
  tier     Nimble          Exa Agent        Parallel Task      Firecrawl Agent (spark-2, dynamic credits)
  medium   medium $0.10    medium $0.10     pro $0.10          effort medium, maxCredits 2500 (default)
  high     high   $0.50    high   $0.50     ultra2x $0.60      effort high,   maxCredits 2500 (default)
No x-high tier: due diligence runs at high.

Keys come from the environment (or .env): NIMBLE_API_KEY, EXA_API_KEY, PARALLEL_API_KEY, FIRECRAWL_API_KEY.
"""
import copy
import json
import os
import time

import certifi
import httpx

from . import load_env

load_env()


def KEY(name):
    v = os.environ.get(name)
    if not v:
        raise RuntimeError(f"{name} is not set (see .env.example)")
    return v


H = dict(verify=certifi.where(), timeout=120)
_nimble = None


def nimble():
    global _nimble
    if _nimble is None:
        from nimble_python import Nimble
        _nimble = Nimble(api_key=KEY("NIMBLE_API_KEY"))
    return _nimble


TIERS = {
    "medium": {"nimble": "medium", "exa": "medium", "parallel": "pro", "firecrawl": ("medium", 2500)},
    "high": {"nimble": "high", "exa": "high", "parallel": "ultra2x", "firecrawl": ("high", 2500)},
}
LIST = {"nimble": {"medium": 0.10, "high": 0.50, "x-high": 2.00},
        "exa": {"medium": 0.10, "high": 0.50},
        "parallel": {"pro": 0.10, "ultra2x": 0.60, "ultra8x": 2.40}}
FC_CREDIT_USD = 99 / 100_000  # Firecrawl Standard plan rate


def prompt(spec, task):
    return f"{spec['instructions']}\n\nTask: {task}\n\nReturn JSON matching the output schema."


def poll(fn, every=10, max_wait=3600):
    t0 = time.time()
    while time.time() - t0 < max_wait:
        time.sleep(every)
        try:
            r = fn()
        except Exception:
            continue
        if r is not None:
            return r
    return None


def run_nimble(spec, task, tier):
    eff = TIERS[tier]["nimble"]
    n = nimble()
    t0 = time.time()
    run = n.agents.run(input=task, skill=spec["instructions"], output_schema=spec["output_schema"],
                       effort=eff, use_case=spec["use_case"])
    rec = {"arm": "nimble", "tier": eff, "run_id": run.id}

    def done():
        st = n.agents.runs.get(run.id, agent_id=run.web_search_agent_id).status
        return st if st in ("completed", "failed", "cancelled") else None
    status = poll(done, 15) or "timeout"
    rec.update(status=status, seconds=round(time.time() - t0))
    if status == "completed":
        res = n.agents.runs.result(run.id, agent_id=run.web_search_agent_id).model_dump()
        out = res.get("output") or {}
        rec["output"] = out.get("content") if isinstance(out, dict) else out
    rec.update(cost_usd=LIST["nimble"][eff], cost_basis="list")
    return rec


def run_exa(spec, task, tier):
    eff = TIERS[tier]["exa"]
    h = {"x-api-key": KEY("EXA_API_KEY"), "Content-Type": "application/json"}
    body = {"query": task, "systemPrompt": spec["instructions"], "effort": eff, "outputSchema": spec["output_schema"]}
    if spec.get("exa_data_sources"):
        body["dataSources"] = [{"provider": p} for p in spec["exa_data_sources"]]
    if eff == "ultra":
        body["budget"] = {"maxCostDollars": 2}
    t0 = time.time()
    r = httpx.post("https://api.exa.ai/agent/runs", headers=h, json=body, **H)
    if r.status_code >= 300:
        return {"arm": "exa", "tier": eff, "status": "rejected", "error": r.text[:800]}
    rid = r.json()["id"]

    def done():
        d = httpx.get(f"https://api.exa.ai/agent/runs/{rid}", headers=h, **H).json()
        return d if d.get("status") in ("completed", "failed", "cancelled") else None
    d = poll(done, 10) or {"status": "timeout"}
    cost = (d.get("costDollars") or {}).get("total") if isinstance(d.get("costDollars"), dict) else d.get("costDollars")
    return {"arm": "exa", "tier": eff, "run_id": rid, "status": d["status"], "seconds": round(time.time() - t0),
            "output": (d.get("output") or {}).get("structured") or (d.get("output") or {}).get("text"),
            "cost_usd": cost if cost is not None else LIST["exa"].get(eff), "cost_basis": "billed" if cost is not None else "list",
            "error": json.dumps(d.get("error"))[:500] if d.get("error") else None,
            "data_sources": spec.get("exa_data_sources"), "cost_breakdown": d.get("costDollars"), "usage": d.get("usage")}


def strict(schema):
    """Parallel wants every property required and no extra keys; nullable types keep 'unknown' possible."""
    s = copy.deepcopy(schema)

    def walk(n):
        if isinstance(n, dict):
            if n.get("type") == "object" or "properties" in n:
                props = n.get("properties", {})
                n["required"] = list(props)
                n["additionalProperties"] = False
                for v in props.values():
                    walk(v)
            if "items" in n:
                walk(n["items"])
    walk(s)
    return s


def run_parallel(spec, task, tier):
    proc = TIERS[tier]["parallel"]
    h = {"x-api-key": KEY("PARALLEL_API_KEY"), "Content-Type": "application/json"}
    body = {"input": prompt(spec, task), "processor": proc,
            "task_spec": {"output_schema": {"type": "json", "json_schema": strict(spec["output_schema"])}}}
    t0 = time.time()
    r = httpx.post("https://api.parallel.ai/v1/tasks/runs", headers=h, json=body, **H)
    if r.status_code >= 300:
        return {"arm": "parallel", "tier": proc, "status": "rejected", "error": r.text[:800]}
    rid = r.json()["run_id"]

    def done():
        d = httpx.get(f"https://api.parallel.ai/v1/tasks/runs/{rid}", headers=h, **H).json()
        return d if d.get("status") in ("completed", "failed", "cancelled") else None
    d = poll(done, 15) or {"status": "timeout"}
    rec = {"arm": "parallel", "tier": proc, "run_id": rid, "status": d["status"], "seconds": round(time.time() - t0),
           "cost_usd": LIST["parallel"][proc], "cost_basis": "list"}
    if d["status"] == "completed":
        res = httpx.get(f"https://api.parallel.ai/v1/tasks/runs/{rid}/result", headers=h, **H).json()
        rec["output"] = (res.get("output") or {}).get("content")
        rec["basis"] = (res.get("output") or {}).get("basis")
    else:
        rec["error"] = json.dumps(d.get("error") or d)[:500]
    return rec


def run_firecrawl(spec, task, tier):
    effort, cap = TIERS[tier]["firecrawl"]
    model = "spark-2"
    h = {"Authorization": f"Bearer {KEY('FIRECRAWL_API_KEY')}", "Content-Type": "application/json"}
    body = {"prompt": prompt(spec, task), "schema": spec["output_schema"], "model": model, "effort": effort, "maxCredits": cap,
            "exchange": {"enabled": True}}
    t0 = time.time()
    r = httpx.post("https://api.firecrawl.dev/v2/agent", headers=h, json=body, **H)
    if r.status_code >= 300:
        return {"arm": "firecrawl", "tier": f"{model}/{effort}", "status": "rejected", "error": r.text[:800]}
    rid = r.json()["id"]

    def done():
        d = httpx.get(f"https://api.firecrawl.dev/v2/agent/{rid}", headers=h, **H).json()
        return d if d.get("status") in ("completed", "failed", "cancelled") else None
    d = poll(done, 10) or {"status": "timeout"}
    credits = d.get("creditsUsed")
    return {"arm": "firecrawl", "tier": f"{model}/{effort}", "run_id": rid, "status": d["status"],
            "seconds": round(time.time() - t0), "output": d.get("data"), "credits": credits,
            "cost_usd": round(credits * FC_CREDIT_USD, 4) if credits is not None else None, "cost_basis": "credits x Standard rate",
            "error": d.get("error"), "exchange": d.get("exchange")}


ARMS = {"nimble": run_nimble, "exa": run_exa, "parallel": run_parallel, "firecrawl": run_firecrawl}

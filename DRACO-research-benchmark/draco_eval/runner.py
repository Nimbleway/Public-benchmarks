"""Run a system across DRACO items and judge each answer.

Results stream to a JSONL file as items finish, so a run that dies at item 87
keeps the first 86. Re-running the same output path resumes: already-scored
item ids are skipped.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import asdict
from pathlib import Path

from draco_eval.dataset import Item
from draco_eval.judge import Judge
from draco_eval.systems import Answer, System, sources_block, strip_trailing_bibliography


def _completed_ids(path: Path) -> set[str]:
    """Item ids already scored successfully.

    Errored items are deliberately excluded so a resume retries them — a
    transient vendor outage should not freeze an item at 0.0 forever.
    """
    if not path.exists():
        return set()
    ids = set()
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if "item_id" in record and not record.get("error"):
            ids.add(record["item_id"])
    return ids


async def _run_item(
    item: Item,
    system: System,
    judge: Judge,
    *,
    timeout_s: float,
    keep_verdicts: bool,
) -> dict:
    record: dict = {"item_id": item.id, "system": system.name, "domain": item.domain}
    try:
        answer: Answer = await asyncio.wait_for(system.run(item.question), timeout=timeout_s)
    except Exception as exc:
        # A failed system run scores zero rather than vanishing from the
        # average — dropping it would silently reward a system for timing out
        # on exactly the hardest items.
        record |= {"error": f"{type(exc).__name__}: {exc}", "overall": 0.0, "axes": {}}
        return record

    # Exactly one bibliography, in one format, for every system — enforced here
    # rather than in each adapter so a contributed adapter cannot break it.
    judged_text = strip_trailing_bibliography(answer.text) + sources_block(answer.citations)
    t0 = time.monotonic()
    try:
        # Bounded like the system call: with retries the judge can now back off for
        # minutes per call, and a sustained outage would otherwise stall the run
        # instead of failing the item and letting a later resume retry it.
        scores = await asyncio.wait_for(
            judge.score(question=item.question, answer=judged_text, rubric=item.rubric),
            timeout=timeout_s,
        )
    except Exception as exc:
        # The system call already succeeded and cost real money; a judge outage
        # must not discard it or abort the other items still in flight.
        record |= {"error": f"judge failed: {type(exc).__name__}: {exc}", "overall": 0.0, "axes": {}}
        return record

    record |= {
        "error": None,
        "overall": scores.overall.score,
        "axes": {axis: s.score for axis, s in scores.axes.items()},
        "axis_detail": {axis: asdict(s) for axis, s in scores.axes.items()},
        "latency_s": round(answer.latency_s, 2),
        "judge_s": round(time.monotonic() - t0, 2),
        "cost_usd": answer.cost_usd,
        "n_citations": len(answer.citations),
        "answer_chars": len(answer.text),
        "n_criteria": len(scores.verdicts),
        "n_unparsed": sum(1 for v in scores.verdicts if v["why"] == "judge returned no parseable verdict"),
        # Criteria where the sampled verdicts disagreed. A high number means the
        # rubric criterion is ambiguous against this answer, not that the answer
        # is borderline — worth reading before trusting the item's score.
        "n_split": sum(1 for v in scores.verdicts if 0.0 < v["met"] < 1.0),
        "judge_samples": judge.samples,
    }
    if keep_verdicts:
        record["verdicts"] = scores.verdicts
        record["answer"] = judged_text
    return record


async def run(
    *,
    items: list[Item],
    system: System,
    judge: Judge,
    out_path: Path,
    concurrency: int = 4,
    timeout_s: float = 3600,
    keep_verdicts: bool = True,
    resume: bool = True,
) -> list[dict]:
    """Evaluate `system` over `items`, appending one JSON record per item."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    done = _completed_ids(out_path) if resume else set()
    todo = [i for i in items if i.id not in done]

    if done:
        print(f"resuming: {len(done)} already scored, {len(todo)} to go", flush=True)
    print(f"{system.name}: {len(todo)} items, concurrency={concurrency}, judge={judge.model}", flush=True)

    sem = asyncio.Semaphore(concurrency)
    lock = asyncio.Lock()
    records: list[dict] = []
    started = time.monotonic()

    async def one(item: Item) -> None:
        async with sem:
            record = await _run_item(item, system, judge, timeout_s=timeout_s, keep_verdicts=keep_verdicts)
        async with lock:
            records.append(record)
            with out_path.open("a") as fh:
                fh.write(json.dumps(record, default=str) + "\n")
            status = "ERROR" if record.get("error") else f"{record['overall']:.3f}"
            elapsed = time.monotonic() - started
            print(
                f"  [{len(records):>3}/{len(todo)}] {item.id} {status:>7}  ({elapsed / 60:.1f} min elapsed)", flush=True
            )
            if record.get("error"):
                print(f"        {record['error']}", flush=True)

    await asyncio.gather(*(one(item) for item in todo))
    return records

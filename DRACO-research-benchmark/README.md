# draco-eval

This repository contains an evaluation framework for deep research agents,
built on the [DRACO benchmark](https://arxiv.org/abs/2602.11685). Each agent is
evaluated across 100 cross-domain tasks, judged against expert-curated rubrics
(~40 weighted criteria) on four axes: factual accuracy, breadth and depth of
analysis, presentation quality, and citation quality.

## Results

| System             | Score | Factual accuracy | Avg latency | Avg cost / item |
| :----------------- | :---: | :---------------: | :---------: | :--------------: |
| `nimble/x-high`     | 74.0% |       71.6%        |   8.7 min   |       $2.00       |
| `exa/xhigh`         | 72.1% |       67.0%        |   2.5 min   |       $1.00       |
| `nimble/high`       | 71.1% |       69.0%        |   5.5 min   |       $0.50       |
| `parallel/ultra8x`  | 71.0% |       68.9%        |  15.6 min   |       $2.40       |
| `exa/high`          | 70.3% |       65.1%        |   1.3 min   |       $0.50       |
| `openai/5.5`        | 69.8% |       67.6%        |   2.7 min   |       $0.56       |
| `parallel/ultra`    | 66.8% |       66.1%        |  10.6 min   |       $0.30       |
| `gemini/max`        | 43.5% |       46.4%        |  11.3 min   |       $5.41       |

Run on 2026-07-29. Judge: `anthropic:claude-sonnet-5`, `--judge-samples 3`.
100/100 items each.

### Sample wins

| # | Question | Nimble | Next best | Margin |
| :--- | :--- | :---: | :---: | :---: |
| 01 | DiamondRock financing strategy | 0.854 | 0.654 (ChatGPT) | +0.201 |
| 02 | Edge computing / IoT transformation | 0.704 | 0.521 (Parallel) | +0.184 |
| 03 | Staggered DiD methodology | 0.980 | 0.830 (Exa, Parallel) | +0.150 |

## Methodology

Each DRACO item pairs a research question with an expert-curated rubric
(~40 criteria total) grouped into four axes:

- `factual-accuracy` — verifiable claims the response must state correctly
- `breadth-and-depth-of-analysis` — synthesis, trade-offs, actionable guidance
- `presentation-quality` — terminology, format, readability, objectivity
- `citation-quality` — citations to primary source documents

Every criterion carries an integer weight: positive weights reward content,
negative weights penalize a specific error the response should avoid.

**Scoring formula** (per the DRACO paper), applied in Python, never by the LLM:

```
raw   = sum(v_i * w_i)              # v_i = 1 if criterion is MET, else 0
score = clamp(raw / sum(w_i for w_i > 0), 0.0, 1.0)
```

Normalizing by the sum of *positive* weights only — not the signed total — is
what makes negative criteria act as real penalties: a response that trips
every error criterion scores below one that simply omits the rewarded content.
The overall score is computed over the flat list of all criteria, not as an
average of the four axis scores, since axes hold different numbers of
criteria and different weight mass and averaging them would silently
re-weight the rubric.

**Judging.** One LLM call judges one criterion at a time, and is asked only
for a binary MET/UNMET verdict plus a one-sentence justification — never for
a score or a weighing of criteria, so the final number is reproducible from
the verdicts. By default 3 independent samples are drawn per criterion and
averaged (`--judge-samples`, default `anthropic:claude-sonnet-5`), which
shrinks per-criterion judge noise and breaks ties. A criterion the judge fails
to parse counts as UNMET rather than being dropped, biasing the score down
rather than granting free credit, and is flagged in the output record
(`n_unparsed`). Criteria where the samples disagreed are also tracked
(`n_split`) — a high count flags an ambiguous rubric criterion, not
necessarily a borderline answer.

**Running an eval.** Every system gets the exact same treatment before
judging: a system's raw output has any trailing bibliography stripped and
replaced with one bibliography in one format, built from its structured
citations, so no adapter can game presentation by hand-formatting sources.
Runs stream one JSON record per item to disk as they complete, so a run that
dies partway keeps everything scored so far; re-running the same output path
resumes and only retries items that errored or are still missing (a
transient vendor outage doesn't freeze an item at 0.0 forever). A system or
judge call that hits its timeout or throws is recorded as a hard `0.0` rather
than being excluded from the average — dropping it would silently reward a
system for failing on exactly its hardest items.

**Dataset.** DRACO is fetched at run time from HuggingFace
(`maxkru92/draco`, `test` split) rather than vendored, and cached locally so
a long multi-system comparison isn't at the mercy of HuggingFace uptime and
so every system is provably scored against byte-identical rubrics.

## Known limitations

- **Cost is not like-for-like.** Some vendors report billed price per run, some
  publish a list price per 1,000 requests, and some report nothing. The report
  prints `—` rather than guessing, and labels the caveat.
- **Latency is wall-clock**, including vendor queue time, and moves with load.
- **Rubric criteria can be stale.** DRACO tasks have factual answers that drift
  as the world changes.
- **Parallel is told to produce a report; the others are not.** Parallel's Task
  API is a task API, not a report product: with no output spec it answers
  `"US$391.035 billion"` in 18 characters, which a research rubric scores near
  zero. The adapter therefore requests a text output described as a
  "comprehensive, well-structured research report ... with inline citations"
  (`systems/parallel.py`). That is configuration parity, not a hint about the
  answer — but it is an instruction the other systems never receive, and you
  should know it is there before quoting a Parallel number.

## Install

Requires Python 3.12+

```bash
# Clone the repository
git clone https://github.com/nimbleway/web-search-agents-benchmark.git
cd web-search-agents-benchmark

# Install: judge + the systems you need
uv sync --extra data --extra anthropic --extra exa
cp .env.example .env   # add your keys
```

Extras: `data` (dataset fetch) · judges `anthropic` / `openai` / `bedrock` ·
systems `exa` / `parallel` / `nimble` / `chatgpt` / `gemini`.

## Run

```bash
# smoke-test on 3 items
draco-eval run --system exa/high --limit 3

# full 100-item run
draco-eval run --system exa/high

# compare
draco-eval report results/*.jsonl
```

### Built-in systems

| System | Tiers | Key |
| :--- | :--- | :--- |
| `exa` | low, medium, high, xhigh, auto | `EXA_API_KEY` |
| `parallel` | lite … ultra8x | `PARALLEL_API_KEY` |
| `nimble` | low, medium, high, x-high, max | `NIMBLE_API_KEY` |
| `chatgpt` | gpt-5.5, gpt-5.5-pro | `OPENAI_API_KEY` |
| `gemini` | preview, max | `GEMINI_API_KEY` |

## Evaluate your own agent

```python
from draco_eval.systems import Answer, Citation


class MyAgent:
    name = "my-agent/v1"

    async def run(self, question: str) -> Answer:
        result = await my_research_pipeline(question)
        return Answer(
            text=result.report,
            citations=[Citation(url=s.url, title=s.title) for s in result.sources],
            latency_s=result.elapsed,
        )
```

```python
import asyncio
from pathlib import Path
from draco_eval import dataset, report
from draco_eval.judge import Judge
from draco_eval.runner import run

records = asyncio.run(run(
    items=dataset.load(),
    system=MyAgent(),
    judge=Judge("anthropic:claude-sonnet-5", samples=3),
    out_path=Path("results/my-agent.jsonl"),
))
print(report.table([report.summarise(records)]))
```

## Requirements

- Python 3.12+
- A judge API key (`anthropic` / `openai` / `bedrock`)
- API credentials for the systems you're evaluating

## Dataset

DRACO is fetched at run time from HuggingFace
([`maxkru92/draco`](https://huggingface.co/datasets/maxkru92/draco)) and cached
to `data/draco.jsonl`. This repo deliberately does not vendor the data — the
benchmark authors stay the source of truth for their own dataset. The cache
guarantees every system in a comparison is scored against byte-identical
rubrics.

DRACO is the work of its authors, not of this project; it carries its own
license and terms. See [arxiv.org/abs/2602.11685](https://arxiv.org/abs/2602.11685).

## License

Apache-2.0. Vendor adapters call third-party APIs governed by those vendors'
own terms.

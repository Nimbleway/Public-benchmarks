# Bundled evaluation data

## `simpleqa_full_dataset.csv`

The **SimpleQA** test set, published by OpenAI alongside the paper
*"Measuring short-form factuality in large language models"*.

| | |
| --- | --- |
| Upstream source | <https://github.com/openai/simple-evals> (`simple_qa_test_set.csv`) |
| Upstream license | MIT — full text in [`LICENSE.simple-evals`](LICENSE.simple-evals) |
| Copyright | © 2024 OpenAI |
| Rows | 4,326 questions (the complete published test set) |
| Columns | `metadata`, `problem`, `answer` — unchanged from upstream |
| SHA-256 | `9ffe52a417d257eb49afa3153d9ca69263db559cded55e4851837f555d1f44c1` |

**Modifications: none.** The file is an unmodified copy of the upstream CSV
under a different filename. The column schema, row count, row order, and cell
contents are as published. Nothing in this repository rewrites it — the
harness reads it, samples from it, and never writes back.

It is vendored rather than downloaded at runtime so that a benchmark run is
reproducible offline and so the exact bytes behind a published number are
pinned by this repository's own history. Verify integrity with:

```bash
shasum -a 256 data/simpleqa_full_dataset.csv
```

### How the harness uses it

`--limit N` draws a subset via `random.Random(seed).sample(rows, N)` — the same
algorithm and stdlib RNG as
[`openai/simple-evals`](https://github.com/openai/simple-evals/blob/main/simpleqa_eval.py)'s
`SimpleQAEval`, with the same default seed of `0`. A bare `--limit 500`
therefore selects the identical 500 questions that published SimpleQA
benchmarks report against. See the "SimpleQA sampling" section of
[`CLAUDE.md`](../CLAUDE.md) for details.

The `metadata` column carries a Python-literal dict with `topic`,
`answer_type`, and `urls`; `urls` supplies the gold source URLs that the
URL-binary retrieval metrics (NDCG/Recall/MRR) score against.

## Other files

`leaderboard_sample.json` — a local render-preview snapshot produced by
`make leaderboard-snapshot`. It is gitignored and safe to delete; regenerate it
from any run directory.

## Related vendored asset

`src/nimble_benchmark/prompts/simpleqa_grader.txt` is the SimpleQA A/B/C
grader prompt, also taken verbatim from `openai/simple-evals` and covered by
the same MIT license recorded here. It is kept as a resource file rather than a
string literal so its provenance stays diffable against upstream.

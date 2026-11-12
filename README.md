# Public benchmarks

Public, reproducible benchmark suites published by Nimble.

## Benchmarks

- [`DRACO-research-benchmark/`](./DRACO-research-benchmark) — evaluation
  framework for deep research agents, built on the
  [DRACO benchmark](https://arxiv.org/abs/2602.11685). Scores agents across
  100 cross-domain tasks against expert-curated rubrics on factual accuracy,
  breadth/depth of analysis, presentation quality, and citation quality.
- [`Due-Diligence-Benchmark/`](./Due-Diligence-Benchmark) — 300 questions
  people search for or ask online when researching a company or fund to invest
  in (earnings, short interest, valuation, ownership, lawsuits and more): Google
  searches with at least 100 monthly US searches in Google Ads Keyword Planner,
  grown from generic topic searches, plus questions asked on Reddit and Quora,
  so the companies were chosen by public demand, not by us. Unlabeled; a
  50-question sample is run and judged with
  [`common/agent-bench`](./common/agent-bench), results in its README.
- [`Finance-Use-Case-Benchmark/`](./Finance-Use-Case-Benchmark) — real-world
  finance research jobs (due diligence, credit risk, KYC screening, deal
  sourcing, hedge-fund company research, grounded answers, adverse media,
  thesis testing), 40 tasks across six providers including two frontier
  models with their own web tools. Same instructions and output schema for
  every provider; every returned fact is judged against the fetched pages the
  providers cited, decision jobs are also graded against fixed answer keys.
  Includes all raw outputs, verdicts and answer keys; runs on
  [`common/agent-bench`](./common/agent-bench).
- [`GTM-Use-Case-Benchmark/`](./GTM-Use-Case-Benchmark) — real-world GTM
  research jobs (meeting prep, account signal tracking), 10 tasks, run and
  scored the same way as the finance benchmark. Includes all raw outputs,
  verdicts and answer keys; runs on [`common/agent-bench`](./common/agent-bench).
- [`Lead-Enrichment-Benchmark/`](./Lead-Enrichment-Benchmark) — 235 questions
  people search for or ask online about a company (layoffs, headcount, executives,
  revenue, subsidiaries and more), collected the same way as the due diligence
  set. Unlabeled; a 50-question sample is run and judged the same way.
- [`SimpleQA-Benchmark/`](./SimpleQA-Benchmark) — provider-neutral benchmark
  harness for web search APIs. Runs a [SimpleQA](https://github.com/openai/simple-evals)
  slice through each provider, grades answers with the official SimpleQA
  classifier, scores retrieval quality (URL-binary and LLM-judged), and
  measures latency, with reproducible run artifacts.

## Repository layout

Each benchmark is a top-level folder with its own README, license and data.
Code shared by several benchmarks lives in [`common/`](./common):

- [`common/agent-bench/`](./common/agent-bench): runs web research agents on a
  benchmark's tasks and judges every returned fact against the pages the
  providers cited and the live web. The finance, GTM, due diligence and lead
  enrichment benchmarks use it; [METHOD.md](./common/agent-bench/METHOD.md)
  explains how it measures.

DRACO and SimpleQA bring their own code.

## Adding a benchmark

1. Add a folder with a README, a LICENSE and your data.
2. Use the shared code in `common/` or bring your own. To use agent-bench, add
   a `tasks.json` ([format](./common/agent-bench/README.md#tasksjson)) and run
   its commands with `--suite <your folder>`; a question set in
   `data/questions.csv` can be turned into one with `question_tasks`.
3. Give the README a "How we measured" section. When it uses shared code, link
   to that code's method write-up and add only what is specific to your
   benchmark.
4. List it under [Benchmarks](#benchmarks) above.

# Public benchmarks

Public, reproducible benchmark suites published by Nimble.

## Benchmarks

- [`DRACO-research-benchmark/`](./DRACO-research-benchmark) — evaluation
  framework for deep research agents, built on the
  [DRACO benchmark](https://arxiv.org/abs/2602.11685). Scores agents across
  100 cross-domain tasks against expert-curated rubrics on factual accuracy,
  breadth/depth of analysis, presentation quality, and citation quality.
- [`Due-Diligence-Benchmark/`](./Due-Diligence-Benchmark) — 292 questions
  people search for on Google when researching a company or fund to invest in
  (earnings, short interest, valuation, ownership, lawsuits and more). Each is a
  real search with at least 100 monthly US searches in Google Ads Keyword
  Planner, grown from generic topic searches, so the companies were chosen by
  search demand, not by us. Unlabeled: score answers with an LLM judge.
- [`Finance-Use-Case-Benchmark/`](./Finance-Use-Case-Benchmark) — real-world
  finance research jobs (credit risk, due diligence, KYC screening, portfolio
  monitoring). Same instructions and output schema for every provider; every
  returned fact is verified against the live web and scored on coverage and
  accuracy. Includes all raw outputs, judge verdicts and the harness to rerun it.
- [`GTM-Use-Case-Benchmark/`](./GTM-Use-Case-Benchmark) — real-world GTM
  research jobs (account briefs, account signals), run and scored the same way
  as the finance benchmark. Includes all raw outputs, judge verdicts and the
  harness to rerun it.
- [`Lead-Enrichment-Benchmark/`](./Lead-Enrichment-Benchmark) — 234 questions
  people search for on Google about a company (layoffs, headcount, executives,
  revenue, subsidiaries and more), collected the same way as the due diligence
  set. Unlabeled.
- [`SimpleQA-Benchmark/`](./SimpleQA-Benchmark) — provider-neutral benchmark
  harness for web search APIs. Runs a [SimpleQA](https://github.com/openai/simple-evals)
  slice through each provider, grades answers with the official SimpleQA
  classifier, scores retrieval quality (URL-binary and LLM-judged), and
  measures latency, with reproducible run artifacts.

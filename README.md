# Public benchmarks

Public, reproducible benchmark suites published by Nimble.

## Benchmarks

- [`DRACO-research-benchmark/`](./DRACO-research-benchmark) — evaluation
  framework for deep research agents, built on the
  [DRACO benchmark](https://arxiv.org/abs/2602.11685). Scores agents across
  100 cross-domain tasks against expert-curated rubrics on factual accuracy,
  breadth/depth of analysis, presentation quality, and citation quality.
- [`GTM-Finance-Use-Case-Benchmark/`](./GTM-Finance-Use-Case-Benchmark) — real-world
  GTM and finance research jobs (credit risk, due diligence, KYC screening,
  portfolio monitoring, account briefs, account signals). Same instructions and
  output schema for every provider; every returned fact is verified against the
  live web and scored on coverage and accuracy. Includes all raw outputs, judge
  verdicts and the harness to rerun it.
- [`SimpleQA-Benchmark/`](./SimpleQA-Benchmark) — provider-neutral benchmark
  harness for web search APIs. Runs a [SimpleQA](https://github.com/openai/simple-evals)
  slice through each provider, grades answers with the official SimpleQA
  classifier, scores retrieval quality (URL-binary and LLM-judged), and
  measures latency, with reproducible run artifacts.

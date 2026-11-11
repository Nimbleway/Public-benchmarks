# Public benchmarks

Public, reproducible benchmark suites published by Nimble.

## Benchmarks

- [`DRACO-research-benchmark/`](./DRACO-research-benchmark) — evaluation
  framework for deep research agents, built on the
  [DRACO benchmark](https://arxiv.org/abs/2602.11685). Scores agents across
  100 cross-domain tasks against expert-curated rubrics on factual accuracy,
  breadth/depth of analysis, presentation quality, and citation quality.
- [`Finance-Use-Case-Benchmark/`](./Finance-Use-Case-Benchmark) — real-world
  finance research jobs (due diligence, credit risk, KYC screening, deal
  sourcing, hedge-fund company research, grounded answers, adverse media,
  thesis testing), 40 tasks across six providers including two frontier
  models with their own web tools. Same instructions and output schema for
  every provider; every returned fact is judged against the fetched pages the
  providers cited, decision jobs are also graded against fixed answer keys.
  Includes all raw outputs, verdicts, answer keys and the harness to rerun it.
- [`GTM-Use-Case-Benchmark/`](./GTM-Use-Case-Benchmark) — real-world GTM
  research jobs (meeting prep, account signal tracking), 10 tasks, run and
  scored the same way as the finance benchmark. Includes all raw outputs,
  verdicts, answer keys and the harness to rerun it.
- [`SimpleQA-Benchmark/`](./SimpleQA-Benchmark) — provider-neutral benchmark
  harness for web search APIs. Runs a [SimpleQA](https://github.com/openai/simple-evals)
  slice through each provider, grades answers with the official SimpleQA
  classifier, scores retrieval quality (URL-binary and LLM-judged), and
  measures latency, with reproducible run artifacts.

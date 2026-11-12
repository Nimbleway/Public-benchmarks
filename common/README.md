# common

Code and docs shared by benchmarks in this repo. A benchmark may use them or
bring its own.

- [`agent-bench/`](agent-bench): runs web research agents on a benchmark's
  tasks and judges every returned fact against the pages the providers cited
  and the live web. Used by the finance, GTM, due diligence and lead
  enrichment benchmarks. [How it measures](agent-bench/METHOD.md).

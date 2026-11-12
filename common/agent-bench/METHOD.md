# How agent-bench measures

This is the method shared by every benchmark that runs on
[agent-bench](README.md): the
[finance](../../Finance-Use-Case-Benchmark) and
[GTM](../../GTM-Use-Case-Benchmark) use-case suites, and the
[due diligence](../../Due-Diligence-Benchmark) and
[lead enrichment](../../Lead-Enrichment-Benchmark) question sets. Each
benchmark's README has a "How we measured" section with what is specific to it:
its jobs, the scoring unit of each job, and the counts from its run (pages
fetched, answer-key items, audits).

What a job is scored on is set per job in the benchmark's `tasks.json`
(see [the format](README.md#tasksjson)): the unit, the judge's tag that sets a
unit's weight, F1 or F2, whether coverage counts every true unit or only the
ones that answer the question, and whether there is an answer key.

## Pipeline

1. **Run.** Every provider gets the same instructions and the same output
   schema for a job, plus the task text. Parallel's schema is made strict
   (all properties required, no extra keys) because its API requires it; the
   fields are the same. One run per provider per task.
2. **Extract.** The judge (Claude Sonnet through the Claude Code CLI, no
   tools) turns each provider's output into units: atomic, checkable facts of
   the kind the job asks for (one credit fact, one adverse event, one KYC
   field, one candidate company, one dated event). Process notes, empty fields
   and statements about what was not found are skipped.
3. **Merge.** Units from all providers for one task are merged into one
   canonical list under shuffled anonymous labels. The same company, person,
   event or value from several providers becomes one unit only when they
   state the same fact with the same values, dates and attribution. A
   provider that adds a characterization, quote or figure the others don't
   make gets its own unit, or its own verdict on its own wording. Conflicting
   values stay separate. Each label's own wording is kept (`variants`).
   Company-list jobs are canonicalised by company domain.
4. **Evidence.** Every URL any provider cited for the task is fetched once,
   trying four web data layers in turn (Nimble Extract, Exa contents,
   Firecrawl scrape, Nimble Extract with JS rendering), and saved.
5. **Verify.** Claude Sonnet first decides each unit from the passages of the
   saved pages that best match it (no tools, 15 units per call). Units it
   cannot decide, and every unit of a company-list job, go to a verifier
   agent that can read and search the saved pages, search the web and fetch
   pages (5 units per call). Each unit gets true / false / unverifiable,
   whether it is in scope, a confidence, and the job's tags that set its
   weight (materiality and freshness, buying or minor signal, KYC group,
   related entity, false hit, red flag, list criteria, whether it answers
   the question). When providers word a unit differently in substance, each
   provider's wording gets its own verdict (`label_verdicts`). The judge is
   told never to mark a unit false for duplicating another.
6. **Retry.** Every unit still unverifiable gets a second, thorough attempt
   (every cited URL read through the fetcher, then other sources). Units left
   unverifiable count as unsupported claims (half wrong).
7. **Corrections.** Two corrections run on the judged tasks, added after an
   external review of the finance and GTM suites:
   - *Duplicate merge.* The merge step sometimes kept the same fact as
     several units (one event described three ways). Claude Sonnet groups
     units that state the same fact (same entity, same event or figure, same
     date); each group becomes one unit asserted by the union of providers
     (`merged_from`). Each provider keeps the verdict of its own wording
     (`label_verdicts`), so a wrong date on a real event still counts as
     wrong. Units in a conflict pair are never merged. Not applied to company
     lists (already one unit per company).
   - *Ownership rule (deal sourcing only).* Founder-owned, family-owned and
     owner-operated companies all satisfy a founder- or family-owned
     criterion when there are no institutional investors (PE, VC, a
     corporate parent or a public listing). A criterion fails only on
     evidence of such investors and is unknown when there is no ownership
     evidence. Claude Sonnet re-decides only the ownership criteria under
     this rule, from the recorded evidence notes (`result_original` keeps the
     first result).
8. **Score.** See [Scoring](#scoring).
9. **Answer keys** (jobs with `"answer_key": true`). A separate Claude run that
   never sees any provider output researches the input and writes the 5 to 8
   questions any good answer must cover, with the answer, a source and
   whether it is critical. Every run is graded on each question (correct,
   wrong or missing) in one call per task, with providers under anonymous
   labels. The task score is
   `combined x (0.5 + 0.5 x share of critical key items graded correct)`, so
   missing or getting wrong a decisive fact limits the score however many
   other facts are right.
10. **Stale-key audit.** Keys are written before the answers and some go
    stale. Every key item at least one provider was graded wrong on is
    re-checked from primary sources by Claude Sonnet 5.5 (effort high, web
    search and page fetch), a model that is not a contestant, with the
    conflicting answers shown under anonymous labels and no provider names.
    It decides key_wrong (the key answer is replaced with a corrected answer
    from pages it opened), both_ok (the key is widened to accept either) or
    key_right / unclear (no change). Every provider on a changed task is
    re-graded. The original answer is kept under `key_original`.
11. **Audit.** A seeded random ~10% of units is re-checked independently by
    Claude Opus with web search and page fetch. Its verdicts are stored as
    `audit` and only used to report agreement; they do not change scores.

## Scoring

The truth set for a task is the pool of units verified true and in scope,
from any provider.

- **Coverage** = weighted share of the truth set the provider returned. For
  weighted jobs, materiality is judged for the person deciding today:
  critical 6, useful 2, minor 0.5, x1.5 when fresh (last 90 days or the
  current state); signal tracking weighs a buying signal 3 and a minor
  release 0.5. No cap.
- **Accuracy** = weighted true / (true + wrong + off scope + 0.5 x
  unverifiable) among the provider's own units.
- **Combined**: decision jobs, where an error is costly, use F1 =
  `2 * acc * cov / (acc + cov)` (`"metric": "F1"`). List and event jobs,
  where reviewing an extra candidate is cheap, use F2 =
  `5 * acc * cov / (4 * acc + cov)`, coverage weighted twice
  (`"metric": "F2"`).

KYC screening and company lists have their own unit rules (groups and caps;
per-criterion pass / fail / unknown); each benchmark's README lists the
scoring unit of every job.

A run that failed or returned nothing scores 0 on its task. The win on a task
goes to the highest combined score; an exact tie splits it.

Coverage is relative to what the providers found together, so it measures who
missed what the others proved exists. It is not an exhaustive answer set.

### Answer-only coverage (`"coverage": "answers"`)

A question set has no answer key, so the truth set is the pool of true facts
the providers found. Pooled that way, a long answer's extra true facts could
outscore a short correct one. For these jobs the judge also tags each fact
"answers the question" or not:

- **Combined** (the headline) uses coverage of only the pooled true facts
  that answer the question.
- **Thoroughness** is the same F1 over coverage of every pooled true fact.
- A true fact that does not answer the question costs no accuracy; a wrong
  one still does.

The summary also reports each provider's median answer length in words and
how often the longest answer was among the winners, so any remaining length
bias is visible in the results.

## Judge

The judge is Claude (`claude-sonnet-5-5` for extraction, merge, verification,
grading and corrections; `claude-opus-5-5` for the audit sample; the code pins
these IDs, overridable with `JUDGE_MODEL` and `AUDIT_MODEL`; the stale-key
audit uses `claude-sonnet-5-5`, overridable with `KEY_AUDIT_MODEL`), and
Claude writes the answer keys, while Claude Opus 5.5 is one of the
contestants. The judge may prefer answers written the way Claude writes; we
have not measured this. Mitigations: providers are shown under shuffled
anonymous labels, verdicts are decided from the saved source pages first, and
the stale-key audit hides provider names.

Nimble Extract is one of the four page fetchers the judge uses (with Exa
contents and Firecrawl scrape), and the verifier can also read a page through
[`agent_bench/fetch.py`](agent_bench/fetch.py) (Nimble Extract with
rendering). The fetchers are used the same way for every provider's citations
and never see which provider cited a page.

## What reproduces exactly

`agent_bench.summary` on a benchmark's shipped results reproduces every
published number exactly, offline. Rerunning the providers or the judge will
not reproduce the exact numbers: the live web changes, providers update their
products and models, and LLM judging is not deterministic. Compare relative
standings, not third decimals.

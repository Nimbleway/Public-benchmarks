# Due diligence question set

300 questions that people search for or ask online when they research a company or a fund before investing: when it
reports earnings, how heavily its stock is shorted, what it is worth, who owns it, what lawsuits it faces, whether a
hedge fund has shut down. It is built for comparing web search and research APIs on that kind of lookup.

Every question comes from public demand, from one of two sources:

- **292 Google searches**, each searched **at least 100 times a month in the US** according to Google Ads Keyword
  Planner, rewritten as a clean question. We chose the topics; Google's search data chose the companies, funds and
  people.
- **8 questions people asked in investing communities** on Reddit and Quora. An agent chose which subreddits and Quora
  topics to read; the people posting there chose the questions. Someone asking it in public, and others discussing it,
  is the demand.

No company name was typed by us at any point.

The set is **unlabeled**: there are no reference answers. Many answers change over time (a short interest figure, the
next earnings date, a valuation), so a fixed answer key would go stale. Score answers with an LLM judge that rates each
one on its own, for example on accuracy, relevance, depth and sourcing, rather than against a gold answer.

A 50-question sample has been run through six web search and research APIs: see [Results](#results).

## Results

A sample of 50 of the 300 questions (in proportion to each category, fixed seed; listed in
[`tasks.json`](tasks.json)) was run through six providers on November 11, 2026, and judged with
[agent-bench](../common/agent-bench). Higher is better for combined, accuracy, coverage and thoroughness. Combined is the
headline score: F1 of accuracy and of coverage of the facts that answer the question. Thoroughness is the same F1 over
every true fact found. Wins = questions where the provider had the highest combined score; an exact tie splits the win.

| Provider | Combined | Accuracy | Coverage | Thoroughness | Median words | Wins | Avg cost / run | Median seconds |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| Nimble | 0.698 | 0.958 | 0.567 | 0.688 | 352 | 30/50 | $0.10 | 109 |
| Claude Opus 5.5 | 0.620 | 0.969 | 0.479 | 0.598 | 307 | 10.7/50 | $0.14 | 50 |
| Exa | 0.561 | 0.973 | 0.408 | 0.513 | 168 | 3.7/50 | $0.10 | 66 |
| Firecrawl | 0.506 | 0.938 | 0.366 | 0.460 | 171 | 4.3/50 | $0.09 | 54 |
| GPT-6.1 Sol | 0.504 | 0.978 | 0.357 | 0.457 | 147.5 | 1/50 | $0.09 | 33 |
| Parallel | 0.424 | 0.937 | 0.294 | 0.385 | 137.5 | 0.3/50 | $0.10 | 128 |

The longest answer was among the winners on 31 of 50 questions.

Units per provider, as judged (true / wrong / off scope / unverifiable):

| Provider | Units | True | Wrong | Off scope | Unverifiable |
| :--- | :---: | :---: | :---: | :---: | :---: |
| Nimble | 960 | 919 | 34 | 6 | 1 |
| Claude Opus 5.5 | 686 | 658 | 17 | 4 | 7 |
| Exa | 465 | 451 | 10 | 4 | 0 |
| Firecrawl | 491 | 471 | 18 | 0 | 2 |
| GPT-6.1 Sol | 387 | 381 | 5 | 0 | 1 |
| Parallel | 358 | 347 | 11 | 0 | 0 |

Judge audit agreement: 162/178 units (a seeded ~10% sample re-checked independently by Claude Opus).

All raw outputs, verdicts and per-question scores are in [`results/`](results); `results/summary.json` has the tables
above. `verified/` does not include the judge's per-provider extraction lists or fetched page text, and verdict notes
are cut to 300 characters.

## How we measured

The sample runs on [agent-bench](../common/agent-bench), the runner and judge shared with the
[finance](../Finance-Use-Case-Benchmark) and [GTM](../GTM-Use-Case-Benchmark) use-case benchmarks. The full method is
in [METHOD.md](../common/agent-bench/METHOD.md). What is specific to this set:

- **One job, one question per task.** Every provider gets the finance suite's "Answer a current question with sources"
  instructions and output schema (an answer, key facts with sources, caveats), at the medium tier.
- **Answer-only scoring.** There is no answer key, so the truth set is the pool of true facts the six providers found,
  and the judge tags each fact "answers the question" or not. The headline score counts only the facts that answer the
  question, so extra true context cannot outscore a short correct answer; a true extra fact costs no accuracy, a wrong
  one does. See [answer-only coverage](../common/agent-bench/METHOD.md#answer-only-coverage-coverage-answers).
- **Length is reported.** Longer answers still tend to find more of the answer: the median answer length and how often
  the longest answer won are in the table above.
- **Evidence.** 889 of 906 cited pages were fetched. The duplicate merge correction applies; there are no answer
  keys, so no key audit.
- **Cost.** About $31 for all provider runs and $63 for the judge and corrections.

To recompute the tables from the shipped results (offline), from `common/agent-bench`:

```bash
uv run python -m agent_bench.summary --suite Due-Diligence-Benchmark
```

To rerun, see [agent-bench's README](../common/agent-bench/README.md#commands): the sample's `tasks.json` is rebuilt
with `question_tasks`, and every step takes `--suite Due-Diligence-Benchmark`. Limitations: 50 questions and one run per provider per
question, so small differences are within run-to-run noise; verdicts are model judgments; and many answers change over
time, so a later run is a different test.

## Why demand-based questions

A question set written by the benchmark's authors measures what the authors think matters, and it is easy, even
without meaning to, to pick companies or questions a particular tool handles well. We wanted the opposite.

- **Real use, weighted by demand.** Each question is something people actually search for or ask. The set leans
  towards what is searched most (earnings dates, short interest, valuations), because that is what people look up, and
  the community questions add the specific, harder lookups investors raise in discussion.
- **No hand-picked companies.** We started from generic topic searches that name no company ("short interest",
  "valuation", "who owns"), and from investing communities rather than from companies. Every company, fund and person
  in the set came from Google's list of related searches or from someone's post.
- **Auditable.** Anyone with a Google Ads account can open Keyword Planner, enter the same seed searches and see the
  same ideas and volumes (they shift a little month to month), and anyone can read the same subreddits and Quora topics.
- **Not tuned to any provider.** The questions were collected and frozen before any search or research API was run on
  them, and no answer quality was looked at when choosing them.

## What's in it

| | |
| --- | --- |
| File | [`data/questions.csv`](data/questions.csv) |
| Questions | 300, about 225 different companies, funds and people |
| Search volume | 100 to 368,000 a month per searched question (median 720) |
| Collected | November 2026, United States, English (volumes for October 2025 – September 2026) |
| Answers | none (unlabeled) |

| Category | Questions | Covers |
| :--- | ---: | :--- |
| `funding_valuation_and_ipo` | 60 | Valuation, IPO plans and dates, funding rounds, IPO filings |
| `stock_and_short_interest` | 59 | Short interest, stock splits, insider trading |
| `financial_results_and_reports` | 53 | Earnings dates and results, annual reports and 10-K filings |
| `legal_esg_and_governance` | 29 | Lawsuits, class actions and settlements |
| `strategy_and_operations` | 25 | Acquisitions, and strategic moves such as winding down a product or entering a market |
| `competitive_landscape` | 24 | Who the competitors are |
| `ownership_and_shareholders` | 23 | Who owns the company, and who its shareholders are |
| `funds_and_managers` | 14 | A hedge fund or manager: returns, 13F holdings, whether it shut down, its pitches, where it invests |
| `company_profile` | 7 | What the company or fund is |
| `supply_chain_and_customers` | 6 | Who the company buys from, and its supply chain |

## Columns

| Column | Meaning |
| :--- | :--- |
| `question` | The question, as a clean, self-contained sentence |
| `category` | The topic, from the table above (for a search, the topic of the seed search it came from) |

## How it was built

### Google searches (292 questions)

1. **Generic seed searches.** We wrote 31 searches that name no company, two to four per topic of interest to an
   investment analyst: for example "shareholders", "who owns", "competitors", "earnings report", "10-k", "valuation",
   "ipo date", "credit rating", "short interest", "insider trading", "stock split", "13f holdings", "lawsuit" and
   "acquisition".

2. **What people search.** For each seed, Google Ads Keyword Planner (United States, English, last 12 months) lists
   related searches with their average monthly volume. We took the 100 most searched ideas per seed, 2,724 in all.
   Many of them name a company: "nvda earning date", "gme short interest", "who owns comcast".

3. **Filter.** An LLM read every idea and kept only those that name a specific company, fund or person and ask
   something an analyst would look up, rewriting the keyword into one clean question with the same intent and entity
   ("nvda earning date" → "When is NVIDIA's next earnings date?"). It dropped searches with no named entity
   ("credit score"), consumer searches (debt relief, card settlements), and stock-price chatter ("tesla stock").

4. **Rules, no hand-picking.**
   - The search must be searched at least 100 times a month.
   - It must contain its seed's topic word or a close variant ("amazon earnings" for "earnings report"), so a bare
     company or person name is not counted as a question about that topic.
   - Per company, only the most searched question per topic and at most three questions in all, the most searched
     first, so a few famous companies do not fill the set.

5. **Read-through.** We read the final list and fixed what the rules missed, each change listed with its reason in
   the build script: the same company under two names (Facebook and Meta, Google and Alphabet, AMC and AMC
   Entertainment) counts as one company, two ambiguous names were made clear ("Who owns Sky, the UK broadcaster?"),
   two famous consumer cases (the McDonald's hot coffee and Red Bull "gives you wings" lawsuits) were dropped as
   trivia rather than research on a company, and ETFs (TQQQ, VOO, SLV, USO, XRT) were dropped as products rather
   than companies or funds an analyst researches. No question was added by hand.

**What people do not search.** Some topics an analyst cares about barely appear in search with a company named:
Keyword Planner returned no ideas for "bankruptcy", and the ideas for "credit rating", "debt", "esg rating",
"restructuring", "insider buying" and "largest shareholders" were consumer or generic searches, or under 100 a month
once a company was named. Those topics are left out rather than filled by hand; the set reflects what is searched.

Questions per seed: valuation 40, short interest 37, earnings report 27, competitors 24, acquisition 23, stock split 21,
annual report 21, ipo date 18, who owns 18, class action lawsuit 16, lawsuit 13, company profile 6, 10-k 5, hedge fund
returns 5, shareholders 5, 13f holdings 4, suppliers 4, supply chain 2, funding round 1, hedge fund performance 1,
insider trading 1.

### Community questions (8 questions)

1. **Sources.** An agent chose the investing communities to read: the newest posts in r/SecurityAnalysis,
   r/ValueInvesting, r/hedgefund and r/investing, and the questions on Quora's Investing, Hedge Funds and Apple topic
   pages (about 170 posts and questions).
2. **Filter.** The same LLM filter kept only requests for information about a named company, fund or person, rewriting
   a post title into one clean question ("Freestone Grove shuttered?" → "Has the hedge fund Freestone Grove shut
   down?"). Most posts were opinions, news, theses or personal advice and were dropped.
3. **Category.** Each was assigned to the category above that fits it.

| Source | Questions | Example |
| :--- | ---: | :--- |
| Quora, Hedge Funds topic | 3 | "Why has hedge fund billionaire David Tepper been investing heavily in China?" |
| Reddit, r/SecurityAnalysis | 2 | "What are the key takeaways from Anthropic's S-1 filing?" |
| Reddit, r/investing | 2 | "Why is SpaceX interested in becoming a telecom carrier?" |
| Reddit, r/hedgefund | 1 | "Has the hedge fund Freestone Grove shut down?" |
| Reddit, r/ValueInvesting; Quora, Investing and Apple topics | 0 | |

## Questions by source

| Source | Questions |
| :--- | ---: |
| Google Ads Keyword Planner | 292 |
| Quora | 3 |
| Reddit | 5 |

The searches were collected with Google Ads Keyword Planner, the posts with Nimble's web data APIs, and the filter run
with an LLM, by Nimble. Questions are as of November 2026; many ask for the latest figure, so their answers will move.

## License

Apache 2.0; see [LICENSE](LICENSE).

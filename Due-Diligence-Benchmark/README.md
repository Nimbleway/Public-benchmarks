# Due diligence question set

292 questions that people search for on Google when they research a company or a fund before investing: when it
reports earnings, how heavily its stock is shorted, what it is worth, who owns it, what lawsuits it faces. It is built
for comparing web search and research APIs on that kind of lookup.

Every question is a real Google search, searched **at least 100 times a month in the US** according to Google Ads
Keyword Planner, rewritten as a clean question. We chose the topics; Google's search data chose the companies, funds
and people. No company name was typed by us at any point.

The set is **unlabeled**: there are no reference answers. Many answers change over time (a short interest figure, the
next earnings date, a valuation), so a fixed answer key would go stale. Score answers with an LLM judge that rates each
one on its own, for example on accuracy, relevance, depth and sourcing, rather than against a gold answer.

Results will be published here once providers are run against it.

## Why demand-based questions

A question set written by the benchmark's authors measures what the authors think matters, and it is easy, even
without meaning to, to pick companies or questions a particular tool handles well. We wanted the opposite.

- **Real use, weighted by demand.** Each question is a search people actually make, at measured volume. The set leans
  towards what is searched most (earnings dates, short interest, valuations), because that is what people look up.
- **No hand-picked companies.** We started from generic topic searches that name no company ("short interest",
  "valuation", "who owns"). Every company, fund and person in the set came from Google's list of related searches.
- **Auditable.** Anyone with a Google Ads account can open Keyword Planner, enter the same seed searches and see the
  same ideas and volumes (they shift a little month to month).
- **Not tuned to any provider.** The questions were collected and frozen before any search or research API was run on
  them, and no answer quality was looked at when choosing them.

## What's in it

| | |
| --- | --- |
| File | [`data/questions.csv`](data/questions.csv) |
| Questions | 292, about 220 different companies, funds and people |
| Search volume | 100 to 368,000 a month per question (median 720) |
| Collected | November 2026, United States, English (volumes for October 2025 – September 2026) |
| Answers | none (unlabeled) |

| Category | Questions | Covers |
| :--- | ---: | :--- |
| `stock_and_short_interest` | 59 | Short interest, stock splits, insider trading |
| `funding_valuation_and_ipo` | 59 | Valuation, IPO plans and dates, funding rounds |
| `financial_results_and_reports` | 53 | Earnings dates and results, annual reports and 10-K filings |
| `legal_esg_and_governance` | 29 | Lawsuits, class actions and settlements |
| `competitive_landscape` | 24 | Who the competitors are |
| `strategy_and_operations` | 23 | Acquisitions the company has made or is making |
| `ownership_and_shareholders` | 23 | Who owns the company, and who its shareholders are |
| `funds_and_managers` | 10 | A hedge fund's returns and its 13F holdings |
| `supply_chain_and_customers` | 6 | Who the company buys from, and its supply chain |
| `company_profile` | 6 | What the company is |

## Columns

| Column | Meaning |
| :--- | :--- |
| `question` | The question, as a clean, self-contained sentence |
| `category` | The topic, from the table above (the topic of the seed search it came from) |

## How it was built

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

The data was collected with Google Ads Keyword Planner and the filter run with an LLM, by Nimble. Questions are as of
November 2026; many ask for the latest figure, so their answers will move.

## License

Apache 2.0; see [LICENSE](LICENSE).

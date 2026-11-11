# Due diligence question set

79 questions that people actually search for and ask online when they research a company or a fund before investing:
who owns it, how its finances look, who supplies it, how its stock is shorted, how a hedge fund has performed. It is
built for comparing web search and research APIs on that kind of lookup.

Every question traces back to a public signal of real demand: a question Google shows under "People also ask", a
related search Google suggests, a search phrase with measured monthly volume in Google Ads Keyword Planner, or a post
on Reddit or Quora. We did not write the questions ourselves; we collected them, filtered them and cleaned up their
wording.

The set is **unlabeled**: there are no reference answers. Many answers change over time (a short interest figure, a
credit rating, the latest annual report), so a fixed answer key would go stale. Score answers with an LLM judge that
rates each one on its own, for example on accuracy, relevance, depth and sourcing, rather than against a gold answer.

Results will be published here once providers are run against it.

## Why demand-based questions

A question set written by the benchmark's authors measures what the authors think matters, and it is easy, even without
meaning to, to write questions a particular tool handles well. We wanted the opposite: questions chosen by the people
who do this research, before any provider was run.

- **Real use.** Each question is something people type into Google or ask in an investing community, so a provider's
  score reflects lookups that actually happen, in the words people use.
- **Auditable.** Each source is public: anyone can run the same Google searches, open the same Keyword Planner ideas
  or read the same subreddits and Quora topics, and check that the questions came from there.
- **Not tuned to any provider.** The questions were collected and frozen before any search or research API was run on
  them, and no answer quality was looked at when choosing them.

## What's in it

| | |
| --- | --- |
| File | [`data/questions.csv`](data/questions.csv) |
| Questions | 79 |
| Collected | November 2026, United States, English |
| Answers | none (unlabeled) |

| Category | Questions | Covers |
| :--- | ---: | :--- |
| `ownership_and_shareholders` | 7 | Who owns or holds shares, now or at a past date, including stakes bought by named investors |
| `company_profile` | 4 | What kind of company or fund this is, and who founded it |
| `competitive_landscape` | 5 | Who the competitors are and how the company compares |
| `supply_chain_and_customers` | 11 | Who it buys from, ships through or sells to; its distribution and supply-chain situation |
| `financial_results_and_reports` | 8 | Reported revenue, earnings and income, and the statements or annual reports that publish them |
| `funding_valuation_and_ipo` | 9 | Funding rounds, valuation and its history, IPO plans, filings, status, date and price |
| `financial_health_and_credit` | 9 | Whether it is financially sound: debt, credit ratings, distress, layoffs |
| `stock_and_short_interest` | 13 | Share-price moves, splits, insider buying and selling, short sellers, short interest and borrow |
| `funds_and_managers` | 7 | A hedge fund or manager: returns, whether it has shut down, its pitches to investors, where its manager invests, its risks |
| `legal_esg_and_governance` | 4 | Lawsuits and settlements, ESG controversies, and governance policies such as insider trading rules |
| `strategy_and_operations` | 2 | Strategic moves, such as winding down a business line or entering a new market, and why |

## Columns

| Column | Meaning |
| :--- | :--- |
| `question` | The question, as a clean, self-contained sentence |
| `category` | The topic, from the table above |

## How it was built

1. **Twelve company searches.** We picked twelve searches that pair a named company or fund with a diligence topic,
   spread across topics and sectors. Google Ads Keyword Planner shows how often people in the US search each one:

   | Search | Avg. monthly searches |
   | :--- | ---: |
   | Datadog competitors | 880 |
   | Boeing lawsuits | 390 |
   | Apple suppliers | 210 |
   | GameStop short interest | 110 |
   | Tesla insider trading | 50 |
   | Carvana credit rating | 40 |
   | Citadel hedge fund performance | 30 |
   | Rippling revenue 2025 | 30 |
   | Peloton debt | 20 |
   | Revolut financial statements | 20 |
   | Klarna shareholders | 10 |
   | Nestle ESG controversies | too few to show |

2. **What people search next.** For each of the twelve we collected:
   - the questions Google shows under **"People also ask"** and the **related searches** at the bottom of the results
     page (US, English);
   - the **Keyword Planner ideas** that name the same company, with their monthly search volume (for example "tesla
     insider buying", "apple chip supplier", "gamestop short interest january 2021").

3. **What investors ask in communities.** The newest posts in r/SecurityAnalysis, r/ValueInvesting, r/hedgefund and
   r/investing, and the questions on Quora's Investing, Hedge Funds and Apple topic pages. Most of these are opinions,
   news or personal advice, so few survived the filter, but the ones that did are real questions about named companies
   and funds.

4. **Filter.** Duplicates, single words and very long texts were dropped by rule. An LLM then read every remaining item
   and either kept it, rewrote it, or dropped it:
   - **kept** a clean question about a named company, fund or person that public web sources can answer;
   - **rewrote** a keyword phrase or a post title into one clean question with the same intent and entity, adding no
     facts ("carvana credit rating" → "What is Carvana's credit rating?");
   - **dropped** questions with no named entity, consumer questions (car financing, card statements), stock-price
     chatter ("Will Peloton stock go up?"), opinions and news.

5. **Trim.** At most three questions per company and topic, so no single company dominates (for example, nine Revolut
   annual-report questions became three).

6. **Categories.** The categories were drawn from the questions themselves, and each question was assigned to one by
   an LLM and checked by hand.

Questions by source:

| Source | Questions |
| :--- | ---: |
| Google related searches | 34 |
| Google "People also ask" | 20 |
| Google Ads Keyword Planner | 17 |
| Reddit | 5 |
| Quora | 3 |

The data was collected with Nimble's web data APIs. Questions are as of November 2026; many name a year or ask for the
latest figure, so their answers will move.

## License

Apache 2.0; see [LICENSE](LICENSE).

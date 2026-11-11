# Lead enrichment question set

61 questions that people actually search for online about a company they want to sell to: who runs it, how big it is,
how it is funded, what it earns, where it is registered and who its customers are. It is built for comparing web search
and research APIs on that kind of account research.

Every question traces back to a public signal of real demand: a question Google shows under "People also ask", a
related search Google suggests, a search phrase with measured monthly volume in Google Ads Keyword Planner, or a
question on Quora. We did not write the questions ourselves; we collected them, filtered them and cleaned up their
wording.

The set is **unlabeled**: there are no reference answers. Many answers change over time (a headcount, an executive,
the latest funding round), so a fixed answer key would go stale. Score answers with an LLM judge that rates each one on
its own, for example on accuracy, relevance, depth and sourcing, rather than against a gold answer.

Results will be published here once providers are run against it.

## Why demand-based questions

A question set written by the benchmark's authors measures what the authors think matters, and it is easy, even without
meaning to, to write questions a particular tool handles well. We wanted the opposite: questions chosen by the people
who do this research, before any provider was run.

- **Real use.** Each question is something people type into Google or ask online, so a provider's score reflects
  lookups that actually happen, in the words people use.
- **Auditable.** Each source is public: anyone can run the same Google searches, open the same Keyword Planner ideas
  or read the same Quora topics, and check that the questions came from there.
- **Not tuned to any provider.** The questions were collected and frozen before any search or research API was run on
  them, and no answer quality was looked at when choosing them.

## What's in it

| | |
| --- | --- |
| File | [`data/questions.csv`](data/questions.csv) |
| Questions | 61 |
| Collected | November 2026, United States, English |
| Answers | none (unlabeled) |

| Category | Questions | Covers |
| :--- | ---: | :--- |
| `leadership_and_ownership` | 8 | Who runs, founded or owns the company, and what a named person does there |
| `funding_and_valuation` | 11 | Investors, funding rounds and history, valuation, unicorn status |
| `revenue_and_earnings` | 10 | Revenue, growth, quarterly earnings, how the company makes money, profitability |
| `ipo_and_public_status` | 3 | Whether and when the company went public |
| `company_overview` | 7 | What the company is, what it does, what it offers |
| `subsidiaries_and_corporate_structure` | 4 | Owned companies and subsidiaries, and how acquired or branded units relate to the parent |
| `locations_and_registration` | 6 | Headquarters, offices, registered addresses, company registration numbers, contact numbers |
| `scale_and_headcount` | 6 | Size and hiring: user counts, employee counts, open roles, layoffs |
| `customers_and_market` | 5 | Who uses the company, customer stories, competitors, partners |
| `tech_stack` | 1 | The software and tools the company uses internally |

## Columns

| Column | Meaning |
| :--- | :--- |
| `question` | The question, as a clean, self-contained sentence |
| `category` | The topic, from the table above |

## How it was built

1. **Twelve company searches.** We picked twelve searches that pair a named company with something a seller looks up
   about an account, spread across topics. Google Ads Keyword Planner shows how often people in the US search each one:

   | Search | Avg. monthly searches |
   | :--- | ---: |
   | Notion revenue | 390 |
   | Deel headquarters | 260 |
   | Canva hiring | 140 |
   | Ramp customers | 140 |
   | Rippling layoffs | 140 |
   | HubSpot employee count | 110 |
   | Snowflake CTO | 90 |
   | Salesforce subsidiaries | 50 |
   | Mistral AI investors | 50 |
   | Figma funding history | 10 |
   | Gong tech stack | too few to show |
   | Revolut company registration number | too few to show |

2. **What people search next.** For each of the twelve we collected:
   - the questions Google shows under **"People also ask"** and the **related searches** at the bottom of the results
     page (US, English);
   - the **Keyword Planner ideas** that name the same company, with their monthly search volume.

3. **What sellers ask in communities.** The newest posts in r/sales, r/salesforce and r/RevOps, and the questions on
   Quora's Sales, Lead Generation, Salesforce and HubSpot topic pages. These communities talk about careers, tools and
   sales technique rather than about specific accounts, so only one question survived the filter.

4. **Filter.** Duplicates, single words and very long texts were dropped by rule. An LLM then read every remaining item
   and either kept it, rewrote it, or dropped it:
   - **kept** a clean question about a named company or person that public web sources can answer;
   - **rewrote** a keyword phrase into one clean question with the same intent and entity, adding no facts ("snowflake
     cto" → "Who is Snowflake's CTO?");
   - **dropped** questions with no named entity, job seeking, login and product support, opinions and news.

5. **Trim.** At most three questions per company and topic, so no single company dominates.

6. **Categories.** The categories were drawn from the questions themselves, and each question was assigned to one by
   an LLM and checked by hand.

Questions by source:

| Source | Questions |
| :--- | ---: |
| Google related searches | 39 |
| Google "People also ask" | 13 |
| Google Ads Keyword Planner | 8 |
| Quora | 1 |

The data was collected with Nimble's web data APIs. Questions are as of November 2026; many name a year or ask for the
latest figure, so their answers will move.

## License

Apache 2.0; see [LICENSE](LICENSE).

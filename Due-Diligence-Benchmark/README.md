# Due diligence question set

1,000 questions that investment analysts, at hedge funds and other investment firms, ask the web about a company, a
fund or a market before they invest or while they hold a position. It is built for comparing web search and research
APIs on the work these teams actually do: each question comes from a specific use case, such as "Bankruptcy check",
"Discontinued products" or "Shareholder proposals on ESG", and no use case has more than 3 questions, so no single
topic dominates a score.

The set is **unlabeled**: there are no reference answers. Many answers change over time (a share count, a headcount,
the latest filing), so a fixed answer key would go stale. Score answers with an LLM judge that rates each one on its
own, for example on accuracy, relevance, depth and sourcing, rather than against a gold answer.

Results will be published here once providers are run against it.

## What's in it

| | |
| --- | --- |
| File | [`data/questions.csv`](data/questions.csv) |
| Questions | 1,000 |
| Use cases | 371, at most 3 questions each |
| Built | 2026-11-11 |
| Answers | none (unlabeled) |

Questions by topic:

| Category | Questions | Covers |
| :--- | ---: | :--- |
| `fundamentals_financials` | 84 | Revenue, margins, cash, debt, guidance, valuation multiples |
| `legal_regulatory` | 84 | Lawsuits, regulator actions, sanctions, fines, licences |
| `management_people` | 84 | Executives and board, their tenure, background and changes |
| `alt_data_signals` | 84 | App rankings and ratings, web traffic, store counts, product listings |
| `ownership_insiders` | 83 | Major holders, insider trades, institutional positions, share structure |
| `fund_manager_odd` | 78 | Operational due diligence on fund managers: registrations, disclosures, service providers |
| `hiring_workforce` | 72 | Headcount, open roles, layoffs, hiring by team or location |
| `deals_ma_funding` | 71 | Acquisitions, divestitures, funding rounds, debt raises |
| `competitive_landscape` | 67 | Competitors, market share, positioning |
| `products_pricing` | 64 | Products, launches, pricing and plans |
| `sentiment_reviews` | 64 | Customer and employee reviews, ratings |
| `esg_controversies` | 58 | Environmental, safety and governance incidents |
| `supply_chain_customers` | 56 | Key suppliers, customers, contracts and concentration |
| `industry_macro` | 51 | Sector and macro data that move the company |

558 questions are marked `search` (a short answer from a few pages) and 442 `research` (an answer that needs several
sources put together); see `endpoint_fit` below.

## Columns

| Column | Meaning |
| :--- | :--- |
| `question` | The question, as an analyst would ask it |
| `category` | The topic, from the table above |
| `use_case` | The specific job the question serves, e.g. "Parent-entity reporting check for Apple Inc." |
| `persona` | Who asks it: hedge fund equity analyst, portfolio manager, research associate, operational due diligence analyst, credit analyst, short-selling analyst, event-driven / merger-arb analyst, alternative data analyst, ESG / stewardship analyst, macro / sector strategist, compliance officer, compliance / KYC analyst |
| `endpoint_fit` | `search` for a question a single search should answer, `research` for one that needs a multi-step research run |

## How it was built

1. **Use cases.** We listed what due diligence teams look up, by persona and topic, each with what the analyst wants
   to learn and why.
2. **Questions grounded in real public data.** For a sample of companies and funds, we read the data that public sites
   actually show and wrote questions whose answers are on those pages. That way the set asks for information that is
   really published, and in the form it is published. 193 questions (19%) were written this way, from these sites:

   | Site | What we read | Questions |
   | :--- | :--- | ---: |
   | stockanalysis.com | Public-company statistics, financials and ownership | 68 |
   | finance.yahoo.com | Quotes, key statistics, holders and profiles | 24 |
   | g2.com | Software product ratings, reviews and categories | 22 |
   | getlatka.com | SaaS revenue, customer and headcount estimates | 15 |
   | wikipedia.org | Company histories, executives and corporate events | 12 |
   | github.com | Open-source organisations and repository activity | 9 |
   | apps.apple.com | App Store listings, ratings and reviews | 7 |
   | Bing News | Recent news coverage | 6 |
   | find-and-update.company-information.service.gov.uk | UK Companies House filings, officers and owners | 6 |
   | apps.shopify.com | Shopify app listings and reviews | 5 |
   | play.google.com | Google Play listings, ratings and reviews | 4 |
   | crunchbase.com | Funding, investors and acquisitions | 3 |
   | builtin.com | Company profiles and hiring | 3 |
   | gleif.org | Legal Entity Identifiers and parent companies | 3 |
   | amazon.com | Product listings, ratings and reviews | 3 |
   | chromewebstore.google.com | Browser extension listings and users | 3 |

3. **Questions from the use cases.** An LLM wrote further questions for every use case, naming real companies, funds
   and people. We dropped questions whose answer is speculative or not public (for example, a company's internal tools
   or its estimated IT spend).
4. **Selection.** Duplicates were removed. The 1,000 were picked in turns across categories, and across use cases
   within a category, with the grounded questions first, so every topic and use case gets its share.

Questions are written as of November 2026; many say "as of" a date, and their answers will move.

## License

Apache 2.0; see [LICENSE](LICENSE).

# Lead enrichment question set

1,000 questions that sales, RevOps and marketing teams ask the web about an account or a lead: who runs it, how big it
is, what it uses, where it operates and what just changed. It is built for comparing web search and research APIs on
the work these teams actually do: each question comes from a specific use case, such as "CEO name", "Singapore UEN" or
"Hiring velocity", and no use case has more than 5 questions (most have 3 or fewer), so no single topic dominates a
score.

The set is **unlabeled**: there are no reference answers. Many answers change over time (a headcount, an executive,
the latest funding round), so a fixed answer key would go stale. Score answers with an LLM judge that rates each one on
its own, for example on accuracy, relevance, depth and sourcing, rather than against a gold answer.

Results will be published here once providers are run against it.

## What's in it

| | |
| --- | --- |
| File | [`data/questions.csv`](data/questions.csv) |
| Questions | 1,000 |
| Use cases | 313, at most 5 questions each |
| Built | 2026-11-11 |
| Answers | none (unlabeled) |

Questions by topic:

| Category | Questions | Covers |
| :--- | ---: | :--- |
| `leadership_contacts` | 107 | Executives, decision-makers and their roles |
| `firmographics` | 101 | Size, revenue, headcount, industry, listing and ticker |
| `technographics` | 93 | The software, cloud and tools an account uses, and migrations |
| `web_social_presence` | 89 | Website, social profiles, app listings, open-source presence |
| `growth_intent_signals` | 87 | Expansion, launches, new markets and other buying signals |
| `locations_subsidiaries` | 81 | Offices, sites, data centres and subsidiaries |
| `registry_identifiers` | 78 | Company registry numbers, LEIs and other official identifiers |
| `funding_investors` | 77 | Funding rounds, amounts and investors |
| `products_pricing` | 76 | Products, plans and pricing |
| `customers_partners` | 73 | Named customers, customer counts and partners |
| `hiring_signals` | 71 | Open roles, hiring pace and teams being built |
| `news_triggers` | 67 | Recent news that makes a good reason to reach out |

686 questions are marked `search` (a short answer from a few pages) and 314 `research` (an answer that needs several
sources put together); see `endpoint_fit` below.

## Columns

| Column | Meaning |
| :--- | :--- |
| `question` | The question, as a seller or marketer would ask it |
| `category` | The topic, from the table above |
| `use_case` | The specific job the question serves, e.g. "Customer base sizing for Gong" |
| `persona` | Who asks it: account executive, SDR / BDR, sales development rep, sales engineer, RevOps analyst, RevOps / data enrichment analyst, marketing operations manager, ABM marketer, growth marketer, partnerships manager |
| `endpoint_fit` | `search` for a question a single search should answer, `research` for one that needs a multi-step research run |

## How it was built

1. **Use cases.** We listed what sales, RevOps and marketing teams look up about accounts and leads, by persona and
   topic, each with what they want to learn and why.
2. **Questions grounded in real public data.** For a sample of companies, we read the data that public sites actually
   show and wrote questions whose answers are on those pages. That way the set asks for information that is really
   published, and in the form it is published. 178 questions (18%) were written this way, from these sites:

   | Site | What we read | Questions |
   | :--- | :--- | ---: |
   | finance.yahoo.com | Quotes, key statistics, holders and profiles | 28 |
   | builtin.com | Company profiles, tech stack and hiring | 17 |
   | stockanalysis.com | Public-company statistics and financials | 16 |
   | g2.com | Software product ratings, reviews and categories | 16 |
   | crunchbase.com | Firmographics, funding and investors | 15 |
   | wikipedia.org | Company histories, executives and locations | 15 |
   | getlatka.com | SaaS revenue, customer and headcount estimates | 12 |
   | find-and-update.company-information.service.gov.uk | UK Companies House registrations, officers and owners | 12 |
   | github.com | Open-source organisations and repository activity | 11 |
   | gleif.org | Legal Entity Identifiers and parent companies | 9 |
   | Bing News | Recent news coverage | 8 |
   | apps.apple.com | App Store listings, ratings and reviews | 7 |
   | apps.shopify.com | Shopify app listings and reviews | 5 |
   | play.google.com | Google Play listings, ratings and reviews | 4 |
   | chromewebstore.google.com | Browser extension listings and users | 3 |

3. **Questions from the use cases.** An LLM wrote further questions for every use case, naming real companies and
   people. We dropped questions whose answer is speculative or not public (for example, a company's email format or
   its internal tools).
4. **Selection.** Duplicates were removed. The 1,000 were picked in turns across categories, and across use cases
   within a category, with the grounded questions first, so every topic and use case gets its share. Use cases were
   capped at 3 questions, and up to 5 only where that was needed to reach 1,000.

Questions are written as of November 2026; many say "as of" a date, and their answers will move.

## License

Apache 2.0; see [LICENSE](LICENSE).

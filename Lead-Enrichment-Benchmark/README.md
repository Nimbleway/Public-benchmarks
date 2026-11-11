# Lead enrichment question set

235 questions that people search for or ask online about a company: who runs it, whether it is laying people off,
how many employees it has, what it earns, what it owns and who owns it. It is built for comparing web search and
research APIs on the account research sales, RevOps and marketing teams do.

Every question comes from public demand, from one of two sources:

- **234 Google searches**, each searched **at least 100 times a month in the US** according to Google Ads Keyword
  Planner, rewritten as a clean question. We chose the topics; Google's search data chose the companies and people.
- **1 question people asked in a sales community** on Quora. An agent chose which subreddits and Quora topics to read;
  the people posting there chose the questions. Someone asking it in public, and others discussing it, is the demand.

No company name was typed by us at any point.

The set is **unlabeled**: there are no reference answers. Many answers change over time (a headcount, an executive,
the latest layoffs), so a fixed answer key would go stale. Score answers with an LLM judge that rates each one on its
own, for example on accuracy, relevance, depth and sourcing, rather than against a gold answer.

Results will be published here once providers are run against it.

## Why demand-based questions

A question set written by the benchmark's authors measures what the authors think matters, and it is easy, even
without meaning to, to pick companies or questions a particular tool handles well. We wanted the opposite.

- **Real use, weighted by demand.** Each question is something people actually search for or ask. The set leans
  towards what is searched most (layoffs, executives, revenue, parent companies), because that is what people look up.
- **No hand-picked companies.** We started from generic topic searches that name no company ("ceo", "layoffs",
  "number of employees"), and from sales communities rather than from companies. Every company and person in the set
  came from Google's list of related searches or from someone's post.
- **Auditable.** Anyone with a Google Ads account can open Keyword Planner, enter the same seed searches and see the
  same ideas and volumes (they shift a little month to month), and anyone can read the same subreddits and Quora topics.
- **Not tuned to any provider.** The questions were collected and frozen before any search or research API was run on
  them, and no answer quality was looked at when choosing them.

## What's in it

| | |
| --- | --- |
| File | [`data/questions.csv`](data/questions.csv) |
| Questions | 235, about 160 different companies and people |
| Search volume | 100 to 450,000 a month per searched question (median 320) |
| Collected | November 2026, United States, English (volumes for October 2025 – September 2026) |
| Answers | none (unlabeled) |

| Category | Questions | Covers |
| :--- | ---: | :--- |
| `scale_and_headcount` | 77 | Layoffs and employee counts |
| `subsidiaries_and_corporate_structure` | 54 | Subsidiaries, and who the parent company is |
| `leadership_and_ownership` | 50 | Who the CEO, CTO or founder is |
| `revenue_and_earnings` | 41 | Annual revenue and quarterly results |
| `ipo_and_public_status` | 10 | Whether the company is publicly traded |
| `company_overview` | 2 | What the company does |
| `locations_and_registration` | 1 | Company registration numbers |

## Columns

| Column | Meaning |
| :--- | :--- |
| `question` | The question, as a clean, self-contained sentence |
| `category` | The topic, from the table above (for a search, the topic of the seed search it came from) |

## How it was built

### Google searches (234 questions)

1. **Generic seed searches.** We wrote 26 searches that name no company, two or three per topic a seller looks up
   about an account: for example "ceo", "cto", "founder", "funding", "investors", "annual revenue", "ipo",
   "subsidiaries", "parent company", "headquarters", "company registration number", "number of employees", "layoffs",
   "hiring", "customers" and "tech stack".

2. **What people search.** For each seed, Google Ads Keyword Planner (United States, English, last 12 months) lists
   related searches with their average monthly volume. We took the 100 most searched ideas per seed, 2,045 in all.
   Many of them name a company: "amazon layoffs", "palantir ceo", "facebook parent company".

3. **Filter.** An LLM read every idea and kept only those that name a specific company or person and ask something a
   seller would look up, rewriting the keyword into one clean question with the same intent and entity ("cto
   accenture" → "Who is Accenture's CTO?"). It dropped searches with no named entity ("ceo meaning"), job seeking
   ("amazon hiring"), customer service, and stock-price chatter.

4. **Rules, no hand-picking.**
   - The search must be searched at least 100 times a month.
   - It must contain its seed's topic word or a close variant ("apple ceo" for "ceo"). This drops searches for a bare
     name such as "bob iger", whose volume measures interest in the person, not in who runs the company.
   - Per company, only the most searched question per topic and at most three questions in all, the most searched
     first, so a few famous companies do not fill the set.

5. **Read-through.** We read the final list and fixed what the rules missed, each change listed with its reason in
   the build script: the same company under two names (Facebook and Meta, BNY and BNY Mellon, Verizon and Verizon
   Wireless) counts as one company; two bare firm names that matched "investors" only through their own name
   ("fortress investment group", "alpine investors") were dropped; Starbucks' employer identification number moved to
   registration; and "amazon ceo jeff bezos" is asked as the question it searches for ("Who is Amazon's CEO?"). No
   question was added by hand.

**What people do not search.** Several topics a seller cares about barely appear in search with a company named.
Ideas for "headquarters", "office locations", "company registration number", "customers", "case study", "partners",
"tech stack", "funding", "investors" and "series b" were generic, consumer or job searches, or under 100 a month once
a company was named; "hiring" ideas were job searches, and IPO searches ("spacex ipo") were left to the due diligence
set as investor questions. Those topics are left out or thin rather than filled by hand; the set reflects what is
searched.

Questions per seed: layoffs 54, annual revenue 40, subsidiaries 30, cto 26, number of employees 24, parent company 24,
ceo 20, publicly traded 10, founder 4, company overview 2.

### Community questions (1 question)

1. **Sources.** An agent chose the sales communities to read: the newest posts in r/sales, r/salesforce and r/RevOps,
   and the questions on Quora's Sales, Lead Generation, Salesforce and HubSpot topic pages (about 160 posts and
   questions).
2. **Filter.** The same LLM filter kept only requests for information about a named company or person, rewriting a
   post into one clean question. These communities talk about careers, tools and sales technique rather than about
   specific companies, so one question survived.
3. **Category.** It was assigned to the category above that fits it.

| Source | Questions | Example |
| :--- | ---: | :--- |
| Quora, HubSpot topic | 1 | "Why did HubSpot beat expectations in its second-quarter 2020 results?" |
| Reddit, r/sales, r/salesforce and r/RevOps; Quora, Sales, Lead Generation and Salesforce topics | 0 | |

## Questions by source

| Source | Questions |
| :--- | ---: |
| Google Ads Keyword Planner | 234 |
| Quora | 1 |
| Reddit | 0 |

The searches were collected with Google Ads Keyword Planner, the posts with Nimble's web data APIs, and the filter run
with an LLM, by Nimble. Questions are as of
November 2026; many ask for the latest figure, so their answers will move.

## License

Apache 2.0; see [LICENSE](LICENSE).

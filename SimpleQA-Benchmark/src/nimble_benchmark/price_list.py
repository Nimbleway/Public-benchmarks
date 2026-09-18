"""Static vendor list prices for the lanes under test, normalized to $/1k queries.

This is a **hand-copied snapshot of publicly published pricing**, not a live
feed: nothing in the harness calls a billing API. The unit prices here are
static; the Cost column in ``run.md`` and the leaderboard multiplies them by the
number of requests the lane actually made in that run
(:func:`usd_for_calls`), so it scales with ``--limit`` and answers "what would
this run have cost at list price?".

What "calls" means, precisely
-----------------------------
One call per dataset row the lane attempted -- the ``problem_count`` the
analyzer writes, which is the denominator of the table's ``n`` column. Two
known biases, in opposite directions, both small at benchmark sizes:

* **Retry attempts are not counted.** A row that hit a transient error retries
  up to ``retry.RetryConfig.max_attempts`` times, but attempts are not recorded
  per row, so a run with a flaky upstream made more HTTP calls than this
  column charges for.
* **Failed rows are counted.** They were requests the harness sent, but most
  vendors don't bill a 5xx or a rate-limit rejection, so a lane with a high
  failure rate is charged for calls it likely wasn't billed for. ``n``'s
  ``ok/total`` form is what makes that visible.

So this is a list-price estimate of the run's spend, accurate to the vendor's
published rate card and the harness's row count -- not an invoice.

Normalization
-------------
``usd_per_1k_queries`` is always dollars per 1,000 *queries* at the lane's
default result count (10 results, which is what every lane in the roster
requests), so the column is comparable down its length even where the vendor
publishes a different unit:

* Request-metered vendors (Nimble, Parallel, Exa, Brave) publish $/1k requests
  directly, so the normalized figure is the list price.
* Tavily meters API credits at a published $/credit, so the normalized figure
  is credits-per-query times that rate.
* Firecrawl is credit-metered and subscription-gated: /search costs 2 credits
  per 10 results, so 1,000 queries burns 2,000 credits, and the normalized
  figure is ``plan_price / plan_credits * 2000``.

What is deliberately excluded: overage beyond 10 results (Parallel and Exa both
charge ~$1/1k extra results), Exa's optional AI page summaries ($1/1k pages),
Exa's Deep Search tiers ($12-15/1k), and every volume/annual discount other
than the one noted per row. A lane whose product tier has no published
per-query price at all carries ``usd_per_1k_queries=None``, so its Cost renders
``—`` at any call count rather than a made-up number. No lane is in that state
today.

Maintenance: prices move. ``PRICE_LIST_AS_OF`` is the date this snapshot was
taken from the vendor pages in :data:`PRICE_SOURCES`; re-check those pages
before quoting the column externally. Every lane in
``samplers.PROVIDER_CAPABILITIES`` must have an entry here (a unit test
enforces it) so a newly added lane fails loudly instead of silently rendering
an em dash.
"""

from __future__ import annotations

from dataclasses import dataclass

# Date this snapshot was copied from the vendor pricing pages below. Every row
# but ``nimble_search`` was re-read against the live pages on this date; see
# that entry for what is still unconfirmed.
PRICE_LIST_AS_OF = "2026-09-17"

# Public pricing pages the figures below were read from.
PRICE_SOURCES: tuple[str, ...] = (
    "https://docs.parallel.ai/getting-started/pricing",
    "https://parallel.ai/pricing",
    "https://exa.ai/pricing",
    "https://exa.ai/docs/reference/pricing",
    "https://exa.ai/docs/reference/search-api-guide",
    "https://brave.com/search/api/",
    "https://brave.com/blog/most-powerful-search-api-for-ai",
    "https://firecrawl.dev/pricing",
    "https://www.nimbleway.com/pricing",
    # The Tavily endpoint reference is listed because it, not the credits page,
    # is where the per-depth credit costs (basic/fast/ultra-fast 1, advanced 2)
    # are published.
    "https://tavily.com/pricing",
    "https://docs.tavily.com/documentation/api-credits",
    "https://docs.tavily.com/api-reference/endpoint/search",
)


@dataclass(frozen=True)
class LanePrice:
    """List price for one benchmark lane's product tier.

    ``list_price`` keeps the vendor's own wording/unit so a reader can trace
    the normalized figure back to the published page; ``usd_per_1k_queries`` is
    the comparable column (``None`` when the lane has no per-query list price).
    """

    vendor: str
    tier: str
    list_price: str
    usd_per_1k_queries: float | None
    notes: str = ""


# Keyed by sampler/lane name, matching ``samplers.PROVIDER_CAPABILITIES``.
PRICE_LIST: dict[str, LanePrice] = {
    # Nimble publishes two search tiers: Search (covers fast/standard and deep
    # depth) and Lite. ``nimble_search`` follows NIMBLE_SEARCH_DEPTH, so its
    # price is resolved through NIMBLE_DEPTH_USD_PER_1K when the run's captured
    # ``search_depth`` is available; the Search tier here is the default (the
    # config default depth is ``fast``).
    #
    # UNCONFIRMED: the public pricing page shows a single "Search API
    # $1.1 / 1k requests" line and no Search/Lite split, so only the $1.10 Lite
    # figure is reconfirmable there. Left at $5.00 rather than repriced on one
    # page read -- this is the significance baseline, so the Cost cell most
    # likely to be quoted. Resolve against the vendor rate card before
    # publishing the column.
    "nimble_search": LanePrice(
        vendor="Nimble",
        tier="Search",
        list_price="$5.00 / 1k requests",
        usd_per_1k_queries=5.00,
        notes=(
            "Depth-dependent: this lane follows NIMBLE_SEARCH_DEPTH, and the Search tier price "
            "covers fast and deep. A run pinned to lite is priced at the Lite tier instead. "
            "Not reconfirmed in the 2026-09-17 re-read -- see the comment above."
        ),
    ),
    "parallel_search_turbo": LanePrice(
        vendor="Parallel",
        tier="Turbo",
        list_price="$1.00 / 1k requests",
        usd_per_1k_queries=1.00,
        notes=(
            "Pay-as-you-go, no subscription. Includes 10 results/excerpts per request; "
            "additional results are $1 / 1k beyond that."
        ),
    ),
    "parallel_search_basic": LanePrice(
        vendor="Parallel",
        tier="Basic",
        list_price="$5.00 / 1k requests",
        usd_per_1k_queries=5.00,
        notes=(
            "Pay-as-you-go, no subscription. Includes 10 results/excerpts per request; "
            "additional results are $1 / 1k beyond that."
        ),
    ),
    # Exa's two lanes are a latency/quality split, not a price split: auto and
    # fast are billed identically.
    "exa_search_auto": LanePrice(
        vendor="Exa",
        tier="Auto",
        list_price="$7.00 / 1k requests",
        usd_per_1k_queries=7.00,
        notes=(
            "Pay-as-you-go, up to 10 results. Extra results $1 / 1k; AI page summaries $1 / 1k pages; "
            "Deep Search variants ($12-15 / 1k) are a separate tier not priced here."
        ),
    ),
    "exa_search_fast": LanePrice(
        vendor="Exa",
        tier="Fast",
        list_price="$7.00 / 1k requests",
        usd_per_1k_queries=7.00,
        notes=(
            "Same price as Auto -- the difference between the lanes is latency/quality tuning, not cost. "
            "Extra results $1 / 1k; AI page summaries $1 / 1k pages."
        ),
    ),
    "brave_search": LanePrice(
        vendor="Brave",
        tier="Search (Web / News / Images / LLM Context)",
        list_price="$5.00 / 1k requests",
        usd_per_1k_queries=5.00,
        notes=(
            "Flat rate across surfaces. The old 2,000-5,000 queries/month free tier was replaced in 2025 "
            "with a $5/month credit that requires a card on file plus attribution; usage above the credit "
            "bills automatically with no published cap."
        ),
    ),
    # Credit-metered and plan-gated rather than pay-as-you-go: /search costs
    # 2 credits per 10 results, so 1k queries = 2k credits. Normalized off the
    # month-to-month Standard plan ($99 / 100k credits) to stay comparable with
    # the no-commitment pricing every other row uses; the annual-billed price
    # ($83/mo) works out to $1.66 / 1k.
    "firecrawl_search": LanePrice(
        vendor="Firecrawl",
        tier="Search (Standard plan, monthly)",
        list_price="$99 / month for 100k credits",
        usd_per_1k_queries=1.98,
        notes=(
            "Credit-based and bundled with Firecrawl's scrape/crawl product: 2 credits per 10 results, "
            "so 1k queries = 2k credits. Annual billing ($83/mo) is $1.66 / 1k, and the larger Growth/Scale "
            "plans bring the per-credit cost down further. 1,000 free credits one-time, no recurring free tier."
        ),
    ),
    # Tavily bills API credits at $0.008 each (pay-as-you-go). The per-depth
    # credit cost is published on the /search API reference rather than on the
    # credits page, which still lists only ``basic`` and ``advanced``: the
    # reference documents ``basic``, ``fast`` and ``ultra-fast`` at 1 credit
    # and ``advanced`` at 2. So ``fast`` costs the same as ``basic``, and that
    # is read off a vendor page rather than inferred from ``basic``.
    "tavily_search_basic": LanePrice(
        vendor="Tavily",
        tier="Search (basic)",
        list_price="1 API credit @ $0.008 / credit",
        usd_per_1k_queries=8.00,
        notes="1 credit x $0.008 = $0.008/query.",
    ),
    "tavily_search_fast": LanePrice(
        vendor="Tavily",
        tier="Search (fast)",
        list_price="1 API credit @ $0.008 / credit",
        usd_per_1k_queries=8.00,
        notes=(
            "Read from the /search API reference, which prices search_depth=fast at "
            "1 API credit -- same as basic, so 1 x $0.008 = $0.008/query. The credits page still "
            "omits the depth; the reference is the source for this row. Pay-as-you-go rate: the "
            "$30/mo Project plan works out lower per credit."
        ),
    ),
}


# Nimble search depth -> published tier price, for the depth-configurable
# ``nimble_search`` lane. ``lite`` is the Lite tier; ``fast`` and ``deep`` are
# both billed at the Search tier.
NIMBLE_DEPTH_USD_PER_1K: dict[str, float] = {
    "lite": 1.10,
    # ``standard`` is the server's current name for the Search tier and
    # ``fast`` its deprecated alias, so both price the same.
    "standard": 5.00,
    "fast": 5.00,
    "deep": 5.00,
}


def usd_per_1k_queries(provider: str, *, search_depth: str | None = None) -> float | None:
    """List price in dollars per 1,000 queries for ``provider``.

    Returns ``None`` for an unknown lane and for a lane with no per-query list
    price (the token-metered answer lane), so callers render ``—`` instead of
    guessing.

    ``search_depth`` resolves the depth-configurable ``nimble_search`` lane
    against the tier it actually ran at (the run's captured
    ``sampler_config_nimble_search.json``). Pinned lanes ignore it: their depth
    is part of the lane definition, so the mapping above already reflects it.
    """
    price = PRICE_LIST.get(provider)
    if price is None:
        return None
    if provider == "nimble_search" and search_depth:
        return NIMBLE_DEPTH_USD_PER_1K.get(search_depth, price.usd_per_1k_queries)
    return price.usd_per_1k_queries


def usd_for_calls(
    provider: str,
    calls: float | str | None,
    *,
    search_depth: str | None = None,
) -> float | None:
    """List-price cost of ``calls`` requests to ``provider``.

    ``calls`` is the lane's request count for the run (the analyzer's
    ``problem_count``); see the module docstring for exactly what it counts.
    Returns ``None`` -- rendered ``—`` -- when the lane has no per-query list
    price, when it is unknown, or when the call count is missing or unusable,
    so a report never shows a cost it can't substantiate.
    """
    per_1k = usd_per_1k_queries(provider, search_depth=search_depth)
    if per_1k is None:
        return None
    count = _to_float(calls)
    if count is None or count < 0:
        return None
    return per_1k * count / 1_000


def format_cost(
    provider: str,
    calls: float | str | None,
    *,
    search_depth: str | None = None,
) -> str:
    """Render the Cost cell for a lane that made ``calls`` requests, or ``—``.

    Shared by ``run.md`` (plain Markdown) and the leaderboard (JSX table) so the
    unit and precision are identical on both surfaces. Precision is adaptive:
    a smoke run's sub-dime totals would otherwise all collapse to ``$0.00`` at
    two decimals, so anything under $0.10 renders four.
    """
    value = usd_for_calls(provider, calls, search_depth=search_depth)
    if value is None:
        return "—"
    if 0 < value < 0.1:
        return f"${value:,.4f}"
    return f"${value:,.2f}"


def _to_float(value: float | str | None) -> float | None:
    """Coerce a CSV/DataFrame cell to float, tolerating None/""/NaN."""
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if number != number else number  # NaN != NaN


def search_depth_from_configs(provider: str, sampler_configs: dict[str, dict] | None) -> str | None:
    """Pull a lane's captured ``search_depth`` out of the run's sampler configs.

    Returns ``None`` when the configs are unavailable or the lane doesn't record
    a depth, which sends :func:`usd_per_1k_queries` back to the lane's default
    tier.
    """
    if not sampler_configs:
        return None
    depth = sampler_configs.get(provider, {}).get("search_depth")
    return depth if isinstance(depth, str) and depth else None

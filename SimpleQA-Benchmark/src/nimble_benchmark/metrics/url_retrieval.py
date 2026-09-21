"""URL-binary retrieval metrics."""

from __future__ import annotations

from math import log2

from yarl import URL


def normalize_url(url: str) -> str:
    """Normalize ``url`` for URL-binary NDCG/Recall matching.

    Composes two layers:

    1. :class:`yarl.URL` does the RFC-3986 canonicalization work the project
       used to leave on the floor: default-port stripping (``:80``/``:443``),
       percent-encoding decoding for unreserved bytes, dot-segment
       collapsing, and IDN to Punycode for host labels (exposed via
       ``raw_host``). yarl is already in the install graph as a transitive
       of ``aiohttp``, so this costs no extra dep weight.
    2. Project-specific URL-binary policy on top of the canonical form: drop
       the query and fragment, strip a leading ``www.`` host label, and
       strip a trailing path slash. The leaderboard's gold URLs are
       deliberately tracked at the canonical page level (no query params,
       no anchor); engines that return ``?utm_*`` / ``#section`` variants
       collapse onto the same gold URL after this step.

    Empty / unparseable inputs (no parseable host) collapse to ``""`` so
    callers can dedupe them out via the standard set-membership / first-seen
    pipeline.
    """
    if not url:
        return ""
    try:
        parsed = URL(url.strip())
    except (ValueError, TypeError):
        # yarl raises on a few malformed shapes (e.g. embedded NULs). Fall
        # back to the empty string so the dedupe pipeline stays clean
        # instead of injecting a bogus URL into the run scores.
        return ""
    host = parsed.raw_host
    if not host:
        return ""
    if host.startswith("www."):
        host = host[4:]
    scheme = parsed.scheme.lower() or "https"
    path = parsed.path.rstrip("/") or ""
    return str(URL.build(scheme=scheme, host=host, path=path))


def _dedupe_preserve_order(urls: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for url in urls:
        normalized = normalize_url(url)
        if normalized and normalized not in seen:
            seen.add(normalized)
            out.append(normalized)
    return out


def _dcg(relevance: list[int]) -> float:
    return sum(rel / log2(index + 2) for index, rel in enumerate(relevance))


def _ndcg_at(predicted: list[str], gold: set[str], k: int) -> float:
    if not predicted or not gold:
        return 0.0
    relevance = [1 if url in gold else 0 for url in predicted[:k]]
    ideal_hits = min(k, len(gold))
    ideal = [1] * ideal_hits
    ideal_dcg = _dcg(ideal)
    return _dcg(relevance) / ideal_dcg if ideal_dcg else 0.0


def _recall_at(predicted: list[str], gold: set[str], k: int) -> float:
    if not predicted or not gold:
        return 0.0
    return len(set(predicted[:k]) & gold) / len(gold)


def score_urls(predicted: list[str], gold: list[str]) -> dict[str, float | int]:
    predicted_normalized = _dedupe_preserve_order(predicted)
    gold_normalized = set(_dedupe_preserve_order(gold))
    first_hit = next((i for i, url in enumerate(predicted_normalized) if url in gold_normalized), None)
    mrr = 0.0 if first_hit is None else 1.0 / (first_hit + 1)

    scores: dict[str, float | int] = {"mrr": mrr}
    for k in (5, 10):
        scores[f"ndcg_at_{k}"] = _ndcg_at(predicted_normalized, gold_normalized, k)
        scores[f"recall_at_{k}"] = _recall_at(predicted_normalized, gold_normalized, k)
        scores[f"hit_at_{k}"] = int(any(url in gold_normalized for url in predicted_normalized[:k]))
    return scores

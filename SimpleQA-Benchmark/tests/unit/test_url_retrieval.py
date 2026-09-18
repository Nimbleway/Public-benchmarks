import pytest

from nimble_benchmark.metrics.url_retrieval import normalize_url, score_urls


@pytest.mark.unit
def test_normalize_url_strips_www_and_trailing_slash():
    assert normalize_url("https://www.example.com/a/") == normalize_url("https://example.com/a")


@pytest.mark.unit
def test_paren_url_not_truncated():
    url = "https://en.wikipedia.org/wiki/Bug_(Breaking_Bad)"
    assert normalize_url(url).endswith("Bug_(Breaking_Bad)")


@pytest.mark.unit
def test_score_urls_all_relevant_top_k():
    scores = score_urls(
        predicted=["https://a.com", "https://b.com", "https://c.com"],
        gold=["https://a.com", "https://b.com", "https://c.com"],
    )

    assert scores["ndcg_at_5"] == pytest.approx(1.0)
    assert scores["recall_at_5"] == pytest.approx(1.0)
    assert scores["mrr"] == pytest.approx(1.0)
    assert scores["hit_at_5"] == 1


@pytest.mark.unit
def test_score_urls_missing_gold_reduces_ndcg():
    complete = score_urls(predicted=["https://a.com", "https://b.com"], gold=["https://a.com", "https://b.com"])
    missing = score_urls(predicted=["https://a.com"], gold=["https://a.com", "https://b.com"])

    assert missing["ndcg_at_5"] < complete["ndcg_at_5"]
    assert missing["recall_at_5"] == pytest.approx(0.5)


@pytest.mark.unit
def test_normalize_url_strips_default_ports():
    """yarl collapses default ports -- engines that serialize the port
    explicitly must hash to the same URL as those that omit it."""
    assert normalize_url("https://example.com:443/a") == normalize_url("https://example.com/a")
    assert normalize_url("http://example.com:80/a") == normalize_url("http://example.com/a")


@pytest.mark.unit
def test_normalize_url_collapses_dot_segments():
    """`/a/./b/../c` must canonicalize to `/a/c`."""
    assert normalize_url("https://example.com/a/./b/../c") == normalize_url("https://example.com/a/c")


@pytest.mark.unit
def test_normalize_url_percent_encoding_case_insensitive():
    """`%2A` and `%2a` are the same byte per RFC 3986 — must match."""
    assert normalize_url("https://example.com/path%2Aname") == normalize_url("https://example.com/path%2aname")


@pytest.mark.unit
def test_normalize_url_drops_query_and_fragment():
    """Project policy: URL-binary match operates at canonical page level,
    so tracking params (`?utm_*`) and anchors (`#section`) collapse onto
    the bare URL. Locks the lossy semantics on top of yarl."""
    base = normalize_url("https://example.com/page")
    assert normalize_url("https://example.com/page?utm_source=x") == base
    assert normalize_url("https://example.com/page#section-2") == base
    assert normalize_url("https://example.com/page?utm_source=x#section-2") == base


@pytest.mark.unit
def test_normalize_url_idn_punycode():
    """IDN hosts canonicalize to Punycode so an engine that ships the IDN
    form matches a gold URL stored as ACE-encoded ASCII."""
    idn = normalize_url("https://例え.テスト/a")
    ace = normalize_url("https://xn--r8jz45g.xn--zckzah/a")
    assert idn == ace

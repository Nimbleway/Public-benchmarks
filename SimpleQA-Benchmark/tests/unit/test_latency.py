import pandas as pd
import pytest

from nimble_benchmark.metrics.latency import (
    KNOWN_SERVER_TIMING_STAGES,
    aggregate_latency,
    parse_server_timing,
    parse_server_timing_total_dur,
)


@pytest.mark.unit
def test_parse_server_timing_total_dur():
    assert parse_server_timing_total_dur("total;dur=12.3") == pytest.approx(12.3)
    assert parse_server_timing_total_dur("db;dur=5, total;dur=42.0") == pytest.approx(42.0)
    assert parse_server_timing_total_dur("nototal;dur=1.0") is None
    assert parse_server_timing_total_dur(None) is None


@pytest.mark.unit
def test_parse_server_timing_extracts_all_stages():
    """Parser returns every ``name;dur=...`` entry the server emits."""
    header = "plan;dur=1600.76, search;dur=9085.50, synthesis;dur=8286.54, total;dur=18974.38"
    timings = parse_server_timing(header)
    assert timings == pytest.approx(
        {
            "plan": 1600.76,
            "search": 9085.50,
            "synthesis": 8286.54,
            "total": 18974.38,
        }
    )


@pytest.mark.unit
def test_parse_server_timing_handles_empty_and_malformed_inputs():
    assert parse_server_timing(None) == {}
    assert parse_server_timing("") == {}
    # Entries without ``dur=`` are silently skipped.
    assert parse_server_timing("plan;desc='ok', synthesis;dur=12.3") == pytest.approx({"synthesis": 12.3})
    # Whitespace tolerance — the header is allowed extra spaces around tokens.
    assert parse_server_timing("  plan ; dur=10.0 ,  search ; dur=20.5  ") == pytest.approx(
        {"plan": 10.0, "search": 20.5}
    )


@pytest.mark.unit
def test_parse_server_timing_respects_quoted_descriptors_with_commas():
    """W3C Server-Timing spec allows ``desc="..."`` with structural delimiters
    (``,`` ``;``) inside the quoted value. A naive comma-split would fracture
    such entries — lock the spec-compliant behavior so a future server that
    emits richer descriptors doesn't silently corrupt the parsed timings."""
    header = 'plan;desc="db query, cache",dur=42.0, search;dur=9085.5'
    # Note: actual spec has ``dur`` and ``desc`` as parallel params on the same
    # entry separated by ``;``, not ``,``. The hardened parser must therefore
    # split top-level commas only outside quotes.
    canonical = 'plan;dur=42.0;desc="db query, cache", search;dur=9085.5'
    assert parse_server_timing(canonical) == pytest.approx({"plan": 42.0, "search": 9085.5})
    # And the (technically malformed-but-survivable) variant where desc and
    # dur are split across multiple entries still parses search correctly
    # rather than collapsing on the quoted comma.
    parsed = parse_server_timing(header)
    assert parsed["search"] == pytest.approx(9085.5)


@pytest.mark.unit
def test_parse_server_timing_double_quoted_dur_value_is_unwrapped():
    """Some proxies wrap ``dur`` values in quotes; the spec allows it via the
    token-or-quoted-string production. We unwrap and parse the float."""
    assert parse_server_timing('total;dur="123.4"') == pytest.approx({"total": 123.4})


@pytest.mark.unit
def test_parse_server_timing_drops_unparseable_dur_silently():
    """A flaky upstream emitting ``dur=NaN`` or non-numeric content must not
    raise — drop the entry and keep parsing the rest of the header."""
    assert parse_server_timing("plan;dur=oops, search;dur=12.3") == pytest.approx({"search": 12.3})


@pytest.mark.unit
def test_aggregate_latency():
    df = pd.DataFrame(
        {
            "provider_response_time_ms": [40.0, 50.0, 60.0],
            "request_response_time_ms": [100.0, 200.0, 300.0],
            "internal_response_time_ms": [10.0, None, 30.0],
        }
    )

    aggregate = aggregate_latency(df)

    assert aggregate["provider_response_time_ms_p50"] == 50.0
    assert aggregate["provider_response_time_ms_mean"] == 50.0
    assert aggregate["request_response_time_ms_p50"] == 200.0
    assert aggregate["request_response_time_ms_mean"] == 200.0
    assert aggregate["internal_response_time_ms_p50"] == 20.0


@pytest.mark.unit
def test_aggregate_latency_drops_rows_without_a_provider_round_trip():
    """A row that never reached the network carries ``None``, and must be
    dropped from the percentiles rather than pulling them toward 0."""
    df = pd.DataFrame(
        {
            "provider_response_time_ms": [100.0, None, 300.0],
            "request_response_time_ms": [100.0, 5000.0, 300.0],
            "internal_response_time_ms": [None, None, None],
        }
    )

    aggregate = aggregate_latency(df)

    assert aggregate["provider_response_time_ms_p50"] == 200.0
    assert aggregate["provider_response_time_ms_mean"] == 200.0


@pytest.mark.unit
def test_aggregate_latency_emits_provider_keys_when_column_absent():
    """Stable schema: a frame from an older run without the column still
    produces the keys as ``None`` instead of a KeyError downstream."""
    aggregate = aggregate_latency(pd.DataFrame({"request_response_time_ms": [100.0]}))

    for stat in ("mean", "p50", "p95"):
        assert aggregate[f"provider_response_time_ms_{stat}"] is None


@pytest.mark.unit
def test_aggregate_latency_includes_known_stage_columns():
    """Known stage columns roll up to mean/p50/p95 when present, ``None`` when absent."""
    df = pd.DataFrame(
        {
            "request_response_time_ms": [100.0, 200.0, 300.0],
            "internal_response_time_ms": [10.0, 20.0, 30.0],
            "stage_plan_ms": [50.0, 60.0, 70.0],
            "stage_search_ms": [400.0, 500.0, 600.0],
            "stage_synthesis_ms": [800.0, 900.0, 1000.0],
        }
    )
    aggregate = aggregate_latency(df)

    for stage in KNOWN_SERVER_TIMING_STAGES:
        for stat in ("mean", "p50", "p95"):
            key = f"stage_{stage}_ms_{stat}"
            assert key in aggregate, key
            assert aggregate[key] is not None, key
    assert aggregate["stage_plan_ms_p50"] == 60.0
    assert aggregate["stage_search_ms_p50"] == 500.0
    assert aggregate["stage_synthesis_ms_p50"] == 900.0


@pytest.mark.unit
def test_aggregate_latency_emits_none_for_missing_stage_columns():
    """Third-party APIs that don't emit Server-Timing produce a frame
    without any ``stage_*`` columns; aggregate still emits the keys as None
    so downstream consumers can rely on a stable schema."""
    df = pd.DataFrame(
        {
            "request_response_time_ms": [100.0, 200.0],
            "internal_response_time_ms": [None, None],
        }
    )
    aggregate = aggregate_latency(df)
    for stage in KNOWN_SERVER_TIMING_STAGES:
        for stat in ("mean", "p50", "p95"):
            key = f"stage_{stage}_ms_{stat}"
            assert key in aggregate
            assert aggregate[key] is None

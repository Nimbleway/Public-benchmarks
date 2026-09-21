"""Nimble endpoint preflight checks.

The Nimble ``/search`` lane uses one base URL + API key, so preflight is a
healthcheck plus a single ``POST /search`` probe per distinct connection.
Every failure here is strict:
the caller explicitly asked for these endpoints, and running the eval without
them silently would be misleading -- the leaderboard would be missing the
lane that anchors its significance baseline.

The skip-list return value is preserved for the caller (``cli._amain``), which
removes skipped samplers from the expanded sampler list before building
samplers and surfaces the exclusion in ``run.md``. With :data:`LENIENT_LANES`
empty it is always an empty list today; the plumbing stays so treating a flaky
lane leniently is a one-line change.
"""

from __future__ import annotations

import logging

import aiohttp
from pydantic import SecretStr

from nimble_benchmark.config import Settings, require_credentials_for_samplers

logger = logging.getLogger(__name__)

# Ordered, not a set: the lane that probes first is the one named in a
# preflight error message, and "nimble_search" is the most recognizable label
# for a connection every lane shares.
NIMBLE_SEARCH_LANES: tuple[str, ...] = ("nimble_search",)

LENIENT_LANES: frozenset[str] = frozenset()


async def preflight_or_die(
    *,
    sampler_names: list[str],
    base_url: str | None = None,
    api_key: str | None = None,
    settings: Settings | None = None,
) -> list[str]:
    """Preflight Nimble lanes, returning samplers that should be excluded.

    Returns the list of sampler names that the caller should drop from the
    run because their lane's upstream is transiently failing. Strict lanes
    raise; only :data:`LENIENT_LANES` can land in the returned list, which is
    empty today, so in practice this either returns ``[]`` or raises.
    """
    skipped: list[str] = []
    if settings is not None:
        require_credentials_for_samplers(settings, sampler_names)

    nimble_lanes = _nimble_lanes_for_preflight(
        sampler_names=sampler_names,
        base_url=base_url,
        api_key=api_key,
        settings=settings,
    )
    if not nimble_lanes:
        return skipped

    # Map base URLs that ONLY a lenient lane uses to that lane's name. Bases
    # shared with any strict lane stay strict -- the strict lane's needs take
    # precedence.
    strict_bases = {nimble_lanes[name][0] for name in nimble_lanes if name not in LENIENT_LANES}
    lenient_bases_to_lanes: dict[str, list[str]] = {}
    for lane_name in LENIENT_LANES:
        if lane_name not in nimble_lanes:
            continue
        base = nimble_lanes[lane_name][0]
        if base not in strict_bases:
            lenient_bases_to_lanes.setdefault(base, []).append(lane_name)

    async with aiohttp.ClientSession() as session:
        for base, lane_name in _distinct_base_urls(nimble_lanes):
            lenient_lanes = lenient_bases_to_lanes.get(base, [])
            try:
                async with session.get(f"{base}/healthcheck") as response:
                    if response.status in {200, 404}:
                        continue
                    if lenient_lanes and response.status >= 500:
                        logger.warning(
                            "%s healthcheck returned %d on %s; excluding %s from this run",
                            lane_name,
                            response.status,
                            base,
                            ", ".join(lenient_lanes),
                        )
                        skipped.extend(lane for lane in lenient_lanes if lane not in skipped)
                        continue
                    raise RuntimeError(f"{lane_name} healthcheck failed with status {response.status}")
            except aiohttp.ClientError as exc:
                if lenient_lanes:
                    logger.warning(
                        "%s healthcheck network error on %s (%s); excluding %s from this run",
                        lane_name,
                        base,
                        exc,
                        ", ".join(lenient_lanes),
                    )
                    skipped.extend(lane for lane in lenient_lanes if lane not in skipped)
                    continue
                raise

        for lane_name, (base, lane_api_key) in _distinct_search_lanes(nimble_lanes):
            if lane_name in skipped:
                continue
            try:
                async with session.post(
                    f"{base}/search",
                    json={"query": "ping", "max_results": 1},
                    headers=_headers(lane_api_key),
                ) as response:
                    if response.status in {401, 403}:
                        raise RuntimeError(f"{lane_name} /search auth failed")
                    if response.status >= 500:
                        if lane_name in LENIENT_LANES:
                            logger.warning(
                                "%s /search returned %d on %s; excluding from this run",
                                lane_name,
                                response.status,
                                base,
                            )
                            skipped.append(lane_name)
                            continue
                        raise RuntimeError(f"{lane_name} /search preflight failed with status {response.status}")
                    if response.status >= 400:
                        raise RuntimeError(f"{lane_name} /search preflight failed with status {response.status}")
            except aiohttp.ClientError as exc:
                if lane_name in LENIENT_LANES:
                    logger.warning(
                        "%s /search network error on %s (%s); excluding from this run",
                        lane_name,
                        base,
                        exc,
                    )
                    skipped.append(lane_name)
                    continue
                raise

    return skipped


def _headers(api_key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json", "Accept": "application/json"}


def _nimble_lanes_for_preflight(
    *,
    sampler_names: list[str],
    base_url: str | None,
    api_key: str | None,
    settings: Settings | None,
) -> dict[str, tuple[str, str]]:
    # Every Nimble lane shares one connection -- only
    # ``search_depth`` / ``full_content`` differ, neither of which the probe
    # sends -- so ``_distinct_search_lanes`` collapses them to a single probe.
    lanes: dict[str, tuple[str, str]] = {
        lane_name: _nimble_connection(base_url=base_url, api_key=api_key, settings=settings)
        for lane_name in NIMBLE_SEARCH_LANES
        if lane_name in sampler_names
    }
    return {
        lane_name: (lane_base_url.rstrip("/"), lane_api_key)
        for lane_name, (lane_base_url, lane_api_key) in lanes.items()
    }


def _nimble_connection(
    *,
    base_url: str | None,
    api_key: str | None,
    settings: Settings | None,
) -> tuple[str, str]:
    if settings is not None:
        return settings.nimble_base_url, _required_secret(settings.nimble_api_key, "NIMBLE_API_KEY")
    if base_url is None or api_key is None:
        raise RuntimeError("NIMBLE_BASE_URL and NIMBLE_API_KEY are required for Nimble preflight")
    return base_url, api_key


def _distinct_base_urls(lanes: dict[str, tuple[str, str]]) -> list[tuple[str, str]]:
    seen: set[str] = set()
    distinct: list[tuple[str, str]] = []
    for lane_name, (base_url, _) in lanes.items():
        if base_url in seen:
            continue
        seen.add(base_url)
        distinct.append((base_url, lane_name))
    return distinct


def _distinct_search_lanes(lanes: dict[str, tuple[str, str]]) -> list[tuple[str, tuple[str, str]]]:
    seen: set[tuple[str, str]] = set()
    distinct: list[tuple[str, tuple[str, str]]] = []
    for lane_name, connection in lanes.items():
        base_url, api_key = connection
        if lane_name not in NIMBLE_SEARCH_LANES or (base_url, api_key) in seen:
            continue
        seen.add((base_url, api_key))
        distinct.append((lane_name, connection))
    return distinct


def _optional_secret(secret: SecretStr | None) -> str | None:
    if secret is None:
        return None
    stripped = secret.get_secret_value().strip()
    return stripped or None


def _required_secret(secret: SecretStr | None, env_name: str) -> str:
    optional_secret = _optional_secret(secret)
    if optional_secret is None:
        raise RuntimeError(f"{env_name} is required for Nimble preflight")
    return optional_secret

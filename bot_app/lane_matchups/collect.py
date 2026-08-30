"""Snowball harvester for laning-phase counter-pick data (PLAN.md §7).

This is a **manually-triggered offline maintenance path**, not something the
bot runs on its own. It is never imported by ``bot_app.commands`` or any
startup/poller module — call :func:`harvest` yourself (from a one-off script
or an interactive shell) when the corpus needs topping up. That keeps it
subordinate to the bot's actual job: live match/live-game announcement
polling shares the same :class:`~bot_app.riot.RiotClient`, and this module
adds load to that same rate-limited client rather than a separate one, so it
must never run unattended alongside the tracker without a human deciding to
spend the request budget on it.

Crawl shape: League-V4 apex leagues seed a starting set of puuids (a
structural proxy for skill tier, since Match-V5 carries no rank — PLAN.md
§6.2); each puuid's recent ranked-solo match ids are listed; each new match
id not already in ``matches.sqlite`` costs two requests (match + timeline);
every participant in a newly-fetched match becomes a new seed. Matches
already present in the cache are skipped before spending a request on them,
per PLAN.md §7's "dedupe against matches.sqlite before spending a request".

Each harvested match feeds two corpora from the one pair of requests: the
``lane_matchups`` sufficient statistics this module was built for, and the
``rating_baselines`` population statistics :mod:`bot_app.rating` normalises
against. :func:`backfill_rating_baselines` fills the latter from matches
already cached, without spending any requests at all.
"""

from __future__ import annotations

import logging
from collections import deque
from typing import Iterable

from ..match_cache import MatchCache, RatingBaselineStat, get_match_cache
from ..queues import QUEUE_TYPES, SOLO_QUEUE_ID
from ..routing import DEFAULT_PLATFORM
from ..services.riot_api import RiotAPIError, get_client
from ..rating import rating_samples
from ..rating_baselines import reset_default_baselines
from .extract import lane_outcomes

LOGGER = logging.getLogger(__name__)

APEX_TIERS: tuple[str, ...] = ("challenger", "grandmaster", "master")

_QUEUE_TYPE = QUEUE_TYPES[SOLO_QUEUE_ID]

_MATCH_IDS_PER_SEED = 20


def seed_puuids(
    server: str = DEFAULT_PLATFORM, *, tiers: Iterable[str] = APEX_TIERS
) -> list[str]:
    """Puuids from the chosen apex tier(s), the crawl's structural rank control."""
    client = get_client()
    puuids: list[str] = []
    for tier in tiers:
        try:
            league = client.apex_league(tier, _QUEUE_TYPE, server)
        except RiotAPIError as error:
            LOGGER.warning("Could not fetch %s league on %s: %s", tier, server, error)
            continue
        for entry in league.get("entries", []) or []:
            puuid = entry.get("puuid")
            if puuid:
                puuids.append(puuid)
    return puuids


def harvest(
    server: str = DEFAULT_PLATFORM,
    *,
    max_matches: int = 500,
    seed_tiers: Iterable[str] = APEX_TIERS,
    cache: MatchCache | None = None,
) -> int:
    """Snowball-crawl ranked-solo matches, writing lane statistics as it goes.

    Returns the number of matches newly fetched from the API this call (not
    the number of matches already cached and skipped). Safe to call
    repeatedly — every match id is deduped against ``matches.sqlite`` first,
    so a resumed run costs nothing extra for ground already covered.

    ``max_matches`` bounds one call's request spend; callers running this as
    a background maintenance job should call it in small batches rather than
    once with a huge budget, so the shared rate limiter always has slack for
    the tracker's own polling.
    """
    client = get_client()
    cache = cache or get_match_cache()

    frontier: deque[str] = deque(seed_puuids(server, tiers=seed_tiers))
    seen_puuids: set[str] = set(frontier)
    seen_matches: set[str] = set()
    processed = 0

    while frontier and processed < max_matches:
        puuid = frontier.popleft()
        try:
            match_ids = client.match_ids(puuid, server, count=_MATCH_IDS_PER_SEED)
        except RiotAPIError as error:
            LOGGER.warning("Could not list matches for %s: %s", puuid, error)
            continue

        for match_id in match_ids:
            if processed >= max_matches:
                break
            if match_id in seen_matches:
                continue
            seen_matches.add(match_id)
            if cache.get(match_id) is not None:
                # Already in the corpus from a prior harvest (or from live
                # tracking) — dedupe before spending the match/timeline pair
                # of requests PLAN.md §7 costs each usable match at.
                continue

            try:
                match = client.match(match_id, server)
            except RiotAPIError as error:
                LOGGER.warning("Could not fetch match %s: %s", match_id, error)
                continue

            if match.get("info", {}).get("queueId") != SOLO_QUEUE_ID:
                processed += 1
                continue

            try:
                timeline = client.match_timeline(match_id, server)
            except RiotAPIError as error:
                LOGGER.warning("Could not fetch timeline for %s: %s", match_id, error)
                timeline = None

            outcomes = lane_outcomes(match, timeline)
            if outcomes:
                cache.record_lane_outcomes(outcomes)
            # The same match/timeline pair also funds the rating population
            # baselines (bot_app.rating_baselines) for free — it is already
            # fetched and parsed, so this costs no extra request budget.
            cache.record_rating_samples(rating_samples(match, timeline))
            processed += 1

            for participant in match.get("info", {}).get("participants", []) or []:
                candidate = participant.get("puuid")
                if candidate and candidate not in seen_puuids:
                    seen_puuids.add(candidate)
                    frontier.append(candidate)

    if processed:
        reset_default_baselines()

    LOGGER.info(
        "Lane-matchup harvest on %s processed %d matches (%d puuids seen)",
        server,
        processed,
        len(seen_puuids),
    )
    return processed


def backfill_rating_baselines(cache: MatchCache | None = None) -> int:
    """Rebuild ``rating_baselines`` from matches already in the cache.

    Spends no API requests: every payload is one the bot has already stored
    from live tracking or a previous harvest. Because cached matches carry
    no timeline, this only funds the box-score metrics — the timeline-derived
    ones (lane diffs, bounty ledgers, objective participation) stay on the
    lobby fallback until :func:`harvest` has supplied enough of them.

    Safe to re-run: totals are aggregated in memory and written through
    :meth:`MatchCache.replace_rating_baselines`, which overwrites each key
    rather than adding to it, and leaves keys this pass produced no samples
    for untouched.

    Returns the number of ``(patch, position, metric)`` rows written.
    """
    cache = cache or get_match_cache()
    totals: dict[tuple[str, str, str], list[float]] = {}
    matches = 0
    for match in cache.iter_match_payloads():
        samples = rating_samples(match)
        if not samples:
            continue
        matches += 1
        for sample in samples:
            key = (sample.patch, sample.position, sample.metric)
            running = totals.setdefault(key, [0.0, 0.0, 0.0])
            running[0] += 1
            running[1] += sample.value
            running[2] += sample.value * sample.value

    rows = [
        RatingBaselineStat(
            patch=patch,
            position=position,
            metric=metric,
            samples=int(samples),
            sum_value=total,
            sum_value_sq=total_sq,
        )
        for (patch, position, metric), (samples, total, total_sq) in totals.items()
    ]
    written = cache.replace_rating_baselines(rows)
    reset_default_baselines()
    LOGGER.info(
        "Rating-baseline backfill wrote %d rows from %d cached matches",
        written,
        matches,
    )
    return written

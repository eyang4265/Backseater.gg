"""Small OP.GG adapter for live League champion statistics."""

from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass

import requests

from .routing import opgg_champion_url

LOGGER = logging.getLogger(__name__)

_TIMEOUT_SECONDS = 20.0
_CACHE_SECONDS = 300.0
_STATS_PATTERN = re.compile(
    r'\\?"rateWin\\?":\s*([0-9.]+),\\?"ratePick\\?":\s*([0-9.]+),'
    r'\\?"rateBan\\?":\s*([0-9.]+)'
)
_TIER_PATTERN = re.compile(r'\\?"children\\?":\\?"([^"\\]+ Tier)')
_PATCH_PATTERN = re.compile(r"patch ([0-9]+(?:\.[0-9]+){1,2})", re.IGNORECASE)
_ALT_PATTERN = re.compile(r'"alt":"([^"]+)"')
_QUANTITY_BADGE_PATTERN = re.compile(
    r'"className":"absolute bottom-0 right-0[^"\\]*".{0,500}?"children":(\d+)',
    re.DOTALL,
)
_ACTIVE_RUNE_PATTERN = re.compile(
    r'"name":"([^"]+)".{0,140}?"isActive":true', re.DOTALL
)


class OPGGError(RuntimeError):
    """The OP.GG page could not be fetched or parsed."""


@dataclass(frozen=True)
class ChampionStats:
    """Live champion statistics reported by OP.GG."""

    win_rate: float
    pick_rate: float
    ban_rate: float
    tier: str | None
    patch: str | None
    position: str
    starter_items: tuple[str, ...]
    core_items: tuple[str, ...]
    boots: tuple[str, ...]
    primary_style: str | None
    secondary_style: str | None
    runes: tuple[str, ...]
    skill_order: tuple[str, ...] = ()
    starter_item_ids: tuple[str, ...] = ()
    core_item_ids: tuple[str, ...] = ()
    boot_ids: tuple[str, ...] = ()
    primary_runes: tuple[str, ...] = ()
    secondary_runes: tuple[str, ...] = ()
    shard_runes: tuple[str, ...] = ()
    starter_item_counts: tuple[int, ...] = ()
    core_item_counts: tuple[int, ...] = ()
    boot_counts: tuple[int, ...] = ()
    situational_items: tuple[str, ...] = ()
    situational_item_ids: tuple[str, ...] = ()


_cache: dict[str, tuple[float, ChampionStats]] = {}
_cache_lock = threading.Lock()


def _row_item_details(html: str, row: str) -> tuple[
    tuple[str, ...], tuple[str, ...], tuple[int, ...]
]:
    """Extract item names and numeric IDs from an OP.GG build row."""
    start = html.find(f"{row}_0")
    if start < 0:
        start = html.find(f'"{row}"')
        if start < 0:
            return (), (), ()
    next_row = html.find(f"{row}_1", start + len(row))
    section = html[start : next_row if next_row >= 0 else start + 6000]
    names = tuple(_ALT_PATTERN.findall(section))
    ids = tuple(re.findall(
        r'(?:item/(?:[^/]+/)?|"(?:itemId|item_id|id)"\s*:\s*)(\d{3,6})',
        section,
        re.IGNORECASE,
    ))
    counts = []
    name_matches = tuple(_ALT_PATTERN.finditer(section))
    for index, name_match in enumerate(name_matches):
        # A quantity badge belongs only to the image that precedes it.  Stop
        # before the next item's ``alt`` attribute so, for example, a potion
        # x2 badge cannot be attributed to Doran's Ring.
        next_name_start = (
            name_matches[index + 1].start()
            if index + 1 < len(name_matches)
            else min(len(section), name_match.end() + 1000)
        )
        item_section = section[name_match.start() : next_name_start]
        nearby = section[max(0, name_match.start() - 500) : next_name_start]
        count_match = re.search(
            r'"(?:count|quantity|amount|itemCount|item_count|stackCount)"\s*:\s*(\d+)',
            nearby,
            re.IGNORECASE,
        )
        if count_match:
            counts.append(int(count_match.group(1)))
            continue
        # The current React Flight payload places the quantity inside the
        # number badge as ``"children": 2`` rather than under a count key.
        # Look only for the badge immediately after this item's image so item
        # IDs, image dimensions, and neighbouring build rows cannot count.
        badge_match = _QUANTITY_BADGE_PATTERN.search(
            item_section
        )
        counts.append(int(badge_match.group(1)) if badge_match else 1)
    return names, ids, tuple(counts)


def _row_item_names(html: str, row: str) -> tuple[str, ...]:
    """Extract item names from the first OP.GG build row of a given type."""
    return _row_item_details(html, row)[0]


def _situational_item_details(html: str, limit: int = 12) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Extract fourth- through sixth-item recommendations as situational ideas."""
    names: list[str] = []
    ids: list[str] = []
    for depth in (4, 5, 6):
        for index in range(5):
            row_names, row_ids, _ = _row_item_details(
                html, f"depth_{depth}_item_{index}"
            )
            if not row_names:
                continue
            item_name = row_names[0]
            item_id = row_ids[0] if row_ids else ""
            if item_id in ids or (not item_id and item_name in names):
                continue
            names.append(item_name)
            ids.append(item_id)
            if len(names) >= limit:
                return tuple(names), tuple(ids)
    return tuple(names), tuple(ids)


def _rune_details(html: str) -> tuple[
    str | None,
    str | None,
    tuple[str, ...],
    tuple[str, ...],
    tuple[str, ...],
    tuple[str, ...],
]:
    """Extract the most-played rune page from an OP.GG response."""
    start = html.rfind('"rune_pages"')
    if start < 0:
        return None, None, (), (), (), ()
    section = html[start : start + 16000]
    end = section.find('],"win_rate"')
    if end >= 0:
        section = section[:end]
    main_start = section.find('"main_runes"')
    sub_start = section.find('"sub_runes"')
    shards_start = section.find('"shards"')
    main_section = section[main_start:sub_start] if main_start >= 0 else ""
    sub_section = section[sub_start:shards_start] if sub_start >= 0 else ""
    shards_section = section[shards_start:] if shards_start >= 0 else ""
    primary = re.search(
        r'"primary_perk_style":\{"id":\d+,"name":"([^"]+)"', section
    )
    secondary = re.search(
        r'"perk_sub_style":\{"id":\d+,"name":"([^"]+)"', section
    )
    runes = tuple(
        _ACTIVE_RUNE_PATTERN.findall(main_section)
        + _ACTIVE_RUNE_PATTERN.findall(sub_section)
        + _ACTIVE_RUNE_PATTERN.findall(shards_section)
    )
    primary_runes = tuple(_ACTIVE_RUNE_PATTERN.findall(main_section))
    secondary_runes = tuple(_ACTIVE_RUNE_PATTERN.findall(sub_section))
    shard_runes = tuple(_ACTIVE_RUNE_PATTERN.findall(shards_section))
    return (
        primary.group(1) if primary else None,
        secondary.group(1) if secondary else None,
        runes,
        primary_runes,
        secondary_runes,
        shard_runes,
    )


def _skill_order(html: str) -> tuple[str, ...]:
    """Extract the most-played Q/W/E/R leveling sequence from OP.GG data."""
    # OP.GG has used both snake_case and camelCase keys, and has changed the
    # surrounding build object a few times. Keep this deliberately tolerant:
    # the page's visible SkillOrder table is backed by one of these arrays.
    key_pattern = re.compile(
        r'"(?:skill_order|skillOrder|skill_build|skillBuild|skill_builds|skillBuilds|skills)"\s*:\s*',
        re.IGNORECASE,
    )
    for match in key_pattern.finditer(html):
        section = html[match.end() : match.end() + 12000]
        array_match = re.match(r"\s*\[([^\]]+)\]", section, re.DOTALL)
        if array_match:
            values = re.findall(r'"([QWER])"', array_match.group(1).upper())
            if len(values) >= 3:
                return tuple(values)

        values = re.findall(
            r'"(?:skill|skill_id|skillId|key)"\s*:\s*"([QWER])"',
            section,
            re.IGNORECASE,
        )
        if len(values) >= 3:
            return tuple(value.upper() for value in values)
    return ()


def parse_champion_stats(html: str, position: str = "all") -> ChampionStats:
    """Extract the headline champion statistics from an OP.GG page."""
    match = _STATS_PATTERN.search(html)
    if match is None:
        raise OPGGError("OP.GG returned a page without champion statistics")
    tier_match = _TIER_PATTERN.search(html)
    patch_match = _PATCH_PATTERN.search(html)
    normalized = html.replace('\\"', '"').replace("\\'", "'")
    (
        primary_style,
        secondary_style,
        runes,
        primary_runes,
        secondary_runes,
        shard_runes,
    ) = _rune_details(normalized)
    # OP.GG orders starter rows by popularity; the first row is the
    # most-often-bought starter set, including repeated items such as potions.
    starter_items, starter_item_ids, starter_item_counts = _row_item_details(normalized, "starter_items")
    core_items, core_item_ids, core_item_counts = _row_item_details(normalized, "core_items")
    boots, boot_ids, boot_counts = _row_item_details(normalized, "boots")
    situational_items, situational_item_ids = _situational_item_details(normalized)
    return ChampionStats(
        win_rate=float(match.group(1)),
        pick_rate=float(match.group(2)),
        ban_rate=float(match.group(3)),
        tier=tier_match.group(1) if tier_match else None,
        patch=patch_match.group(1) if patch_match else None,
        position=position,
        starter_items=starter_items,
        core_items=core_items,
        boots=boots,
        primary_style=primary_style,
        secondary_style=secondary_style,
        runes=runes,
        skill_order=_skill_order(normalized),
        starter_item_ids=starter_item_ids,
        core_item_ids=core_item_ids,
        boot_ids=boot_ids,
        starter_item_counts=starter_item_counts,
        core_item_counts=core_item_counts,
        boot_counts=boot_counts,
        situational_items=situational_items,
        situational_item_ids=situational_item_ids,
        primary_runes=primary_runes,
        secondary_runes=secondary_runes,
        shard_runes=shard_runes,
    )


def fetch_champion_stats(
    server: str, internal_id: str, position: str | None = None
) -> ChampionStats:
    """Fetch and briefly cache live champion statistics from OP.GG."""
    url = opgg_champion_url(server, internal_id, position)
    if url is None:
        raise OPGGError(f"OP.GG does not support server {server!r}")

    cache_key = url
    now = time.monotonic()
    with _cache_lock:
        cached = _cache.get(cache_key)
        if cached and now - cached[0] < _CACHE_SECONDS:
            return cached[1]

    try:
        response = requests.get(
            url,
            timeout=_TIMEOUT_SECONDS,
            headers={"User-Agent": "VibeCode Bot/1.0"},
        )
        response.raise_for_status()
        stats = parse_champion_stats(response.text, position or "all")
    except (requests.RequestException, OPGGError, ValueError) as error:
        LOGGER.warning("Could not fetch OP.GG champion stats from %s: %s", url, error)
        if isinstance(error, OPGGError):
            raise
        raise OPGGError("OP.GG did not respond with usable champion statistics") from error

    with _cache_lock:
        _cache[cache_key] = (now, stats)
    return stats

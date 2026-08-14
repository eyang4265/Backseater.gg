"""Embed construction and the shared team-column layout.

One column builder serves live lobbies, ranked-match announcements, and the
guest tracker. Previously those were three near-identical ~90-line functions
that had already diverged in small ways.

The lookups a column needs (riot id, ranked standing) are per-player HTTP
calls. The match builders used to make them one after another — twenty
sequential round trips per announcement. Here all ten players are resolved
concurrently, and both ranked queues come from a single cached request.
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Iterable, Sequence

import discord

from . import ddragon, emoji as emoji_lookup
from .positions import POSITION_LABELS, ROLE_ORDER, assign_team_positions, role_sort_key
from .ranks import (
    APEX_TIERS,
    RankSnapshot,
    TIER_LABELS,
    average_value,
    fetch_ranks,
    rank_queue_for_match,
    rank_queue_label,
    value_to_rank,
)
from .riot import get_client
from .store import tracked_puuids

LOGGER = logging.getLogger(__name__)

BLUE_TEAM_ID = 100
RED_TEAM_ID = 200
TEAM_IDS = (BLUE_TEAM_ID, RED_TEAM_ID)

#: Widest fan-out is a ten-player lobby; one worker each keeps latency flat.
_LOOKUP_WORKERS = 10


# -- basic embeds ----------------------------------------------------------


def make_embed(
    description: str,
    *,
    title: str | None = None,
    color: discord.Color | None = None,
) -> discord.Embed:
    """Every message the bot sends goes out as an embed, for a consistent look."""
    return discord.Embed(
        title=title,
        description=description,
        color=color if color is not None else discord.Color.green(),
    )


def outcome_color(outcome: str | None) -> discord.Color:
    """Blue for a win, red for a loss, gold when there's no single outcome.

    Remakes, and matches where tracked players ended up on opposing teams,
    have nothing to colour the whole embed by.
    """
    if outcome == "Victory":
        return discord.Color.blue()
    if outcome == "Defeat":
        return discord.Color.red()
    return discord.Color.gold()


def format_duration(total_seconds: int) -> str:
    """``"M:SS"``."""
    minutes, seconds = divmod(max(int(total_seconds), 0), 60)
    return f"{minutes}:{seconds:02d}"


_RELATIVE_UNITS: tuple[tuple[float, str, float], ...] = (
    (60, "min", 60),
    (24, "hours", 3600),
    (14, "days", 86400),
    (8, "weeks", 86400 * 7),
    (12, "months", 86400 * 30),
)


def relative_time(timestamp_ms: int | None) -> str:
    """Short ``"3 days ago"`` label for a millisecond epoch timestamp."""
    if not timestamp_ms:
        return "Never"
    elapsed = max(time.time() - timestamp_ms / 1000, 0)
    for limit, label, unit_seconds in _RELATIVE_UNITS:
        amount = elapsed / unit_seconds
        if amount < limit:
            return f"{max(int(amount), 1)} {label} ago"
    return f"{int(elapsed / (86400 * 365))} years ago"


def kda_text(participant: dict[str, Any]) -> str:
    return (
        f"{participant.get('kills', 0)}/"
        f"{participant.get('deaths', 0)}/"
        f"{participant.get('assists', 0)}"
    )


# -- rank labels -----------------------------------------------------------


def rank_text(snapshot: RankSnapshot | None, *, with_winrate: bool = False) -> str | None:
    """``"🏆 Plat II (0 LP)"``. None when there is no rank to show."""
    if snapshot is None or not snapshot.is_ranked:
        return None
    tier = snapshot.tier or ""
    label = TIER_LABELS.get(tier, tier.title())
    if tier in APEX_TIERS or not snapshot.division:
        text = emoji_lookup.prefixed(emoji_lookup.rank_emoji(tier), f"{label} ({snapshot.lp} LP)")
    else:
        text = emoji_lookup.prefixed(
            emoji_lookup.rank_emoji(tier), f"{label} {snapshot.division} ({snapshot.lp} LP)"
        )
    if with_winrate and snapshot.winrate is not None:
        text += f" · {snapshot.winrate * 100:.0f}%"
    return text


def average_rank_text(value: float | None) -> str:
    """Label for a team's averaged rank value."""
    decoded = value_to_rank(value)
    if decoded is None:
        return "Unranked"
    tier, division, lp = decoded
    icon = emoji_lookup.rank_emoji(tier)
    if not division:
        return emoji_lookup.prefixed(icon, f"Master+ ({lp} LP)")
    return emoji_lookup.prefixed(icon, f"{TIER_LABELS.get(tier, tier.title())} {division} ({lp} LP)")


def winrate_text(snapshot: RankSnapshot | None) -> str | None:
    if snapshot is None or snapshot.winrate is None:
        return None
    return f"{snapshot.winrate * 100:.0f}%"


# -- team columns ----------------------------------------------------------


class NameStyle(Enum):
    """What identifies a player in the left-hand column."""

    SUMMONER = "summoner"
    #: Used by the guest tracker, where one player deliberately has no name shown.
    CHAMPION = "champion"


@dataclass(frozen=True)
class TeamColumns:
    blue_names: list[str] = field(default_factory=list)
    blue_ranks: list[str] = field(default_factory=list)
    red_names: list[str] = field(default_factory=list)
    red_ranks: list[str] = field(default_factory=list)
    blue_average: str = "Unranked"
    red_average: str = "Unranked"
    #: Header over both rank columns; names the queue when it isn't the default.
    rank_header: str = "Rank:"
    debug_lines: list[str] = field(default_factory=list)


@dataclass
class _Row:
    team_id: int
    position: str
    name: str
    rank: str
    value: int | None
    debug: str = ""


def add_team_columns(embed: discord.Embed, columns: TeamColumns) -> None:
    """Add each team's name and rank columns as adjacent inline fields.

    Discord packs inline fields three to a row and splits the row's width
    evenly between them, so each team gets a trailing zero-width spacer: that
    keeps both rows at three fields, which is what lines Blue's and Red's rank
    columns up at the same horizontal position, and stops Discord from
    flowing Red's names up into Blue's row. The blank line after Blue's
    entries adds breathing room without the gap a full-width field leaves.
    """
    spacer = "​"  # zero-width space: Discord rejects an empty field value
    embed.add_field(
        name=f"Blue Team | {columns.blue_average}",
        value=("\n".join(columns.blue_names) or "—") + f"\n{spacer}",
        inline=True,
    )
    embed.add_field(
        name=columns.rank_header,
        value=("\n".join(columns.blue_ranks) or "—") + f"\n{spacer}",
        inline=True,
    )
    embed.add_field(name=spacer, value=spacer, inline=True)
    embed.add_field(
        name=f"Red Team | {columns.red_average}",
        value="\n".join(columns.red_names) or "—",
        inline=True,
    )
    embed.add_field(name=columns.rank_header, value="\n".join(columns.red_ranks) or "—", inline=True)
    embed.add_field(name=spacer, value=spacer, inline=True)


def _columns_from_rows(rows: Sequence[_Row], *, rank_header: str = "Rank:") -> TeamColumns:
    def team(team_id: int) -> tuple[list[str], list[str], float | None]:
        entries = sorted(
            (row for row in rows if row.team_id == team_id),
            key=lambda row: role_sort_key(row.position),
        )
        values = [row.value for row in entries if row.value is not None]
        return (
            [row.name for row in entries],
            [row.rank for row in entries],
            average_value(values),
        )

    blue_names, blue_ranks, blue_average = team(BLUE_TEAM_ID)
    red_names, red_ranks, red_average = team(RED_TEAM_ID)
    return TeamColumns(
        blue_names=blue_names,
        blue_ranks=blue_ranks,
        red_names=red_names,
        red_ranks=red_ranks,
        blue_average=average_rank_text(blue_average),
        red_average=average_rank_text(red_average),
        rank_header=rank_header,
        debug_lines=[row.debug for row in rows if row.debug],
    )


def _positions_by_index(
    participants: Sequence[dict[str, Any]],
    champion_ids: Sequence[str],
    *,
    use_reported_positions: bool,
) -> list[str]:
    """Assign a role to every participant, resolving each team as a unit."""
    tags = ddragon.champion_tags_by_internal_id()
    positions: list[str] = ["Top"] * len(participants)

    for team_id in TEAM_IDS:
        indices = [
            index
            for index, participant in enumerate(participants)
            if participant.get("teamId", BLUE_TEAM_ID) == team_id
        ]
        if not indices:
            continue
        known = (
            [
                POSITION_LABELS.get(
                    participants[index].get("teamPosition")
                    or participants[index].get("individualPosition")
                )
                for index in indices
            ]
            if use_reported_positions
            else None
        )
        assigned = assign_team_positions(
            [participants[index] for index in indices],
            [champion_ids[index] for index in indices],
            tags,
            known,
        )
        for index, role in zip(indices, assigned):
            positions[index] = role

    return positions


def _resolve_concurrently(items: Sequence[Any], resolve: Callable[[Any], Any]) -> list[Any]:
    """Run a blocking per-player lookup across all players at once.

    These are independent, network-bound, and rate-limited centrally, so
    fanning them out turns ten serial round trips into roughly one.
    """
    if not items:
        return []
    with ThreadPoolExecutor(max_workers=min(_LOOKUP_WORKERS, len(items))) as pool:
        return list(pool.map(resolve, items))


@dataclass(frozen=True)
class _PlayerLookup:
    riot_id: str | None
    rank: RankSnapshot | None
    #: True when the rank request itself failed, as opposed to the player
    #: simply being unranked.
    rank_unavailable: bool


def _lookup_player(puuid: str | None, server: str | None, queue_id: int) -> _PlayerLookup:
    if not puuid or not server:
        return _PlayerLookup(None, None, True)
    client = get_client()
    riot_id = client.riot_id(puuid, server)
    ranks = fetch_ranks(puuid, server)
    if ranks is None:
        return _PlayerLookup(riot_id, None, True)
    return _PlayerLookup(riot_id, ranks.get(queue_id, RankSnapshot()), False)


def build_match_columns(
    participants: Sequence[dict[str, Any]],
    *,
    server: str | None,
    queue_id: int | None = None,
    highlight_puuids: Iterable[str] = (),
    name_style: NameStyle = NameStyle.SUMMONER,
) -> TeamColumns:
    """Columns for a finished match, ordered Top/Jungle/Mid/Bottom/Support.

    Names are bolded for any player tracked in data.json — not just the ones
    this announcement is about — so a tracked player who happened to be in the
    lobby still stands out. ``queue_id`` selects which ranked queue's standing
    is shown: a Flex match shows Flex rank.
    """
    if not participants:
        return TeamColumns()

    rank_queue = rank_queue_for_match(queue_id)
    highlighted = set(highlight_puuids) | set(tracked_puuids())
    catalog = ddragon.catalog()

    # match-v5's championName is already Data Dragon's internal id.
    champion_ids = [p.get("championName", "Unknown champion") for p in participants]
    positions = _positions_by_index(participants, champion_ids, use_reported_positions=True)

    lookups = _resolve_concurrently(
        [(p.get("puuid"), server) for p in participants],
        lambda pair: _lookup_player(pair[0], pair[1], rank_queue),
    )

    rows: list[_Row] = []
    for index, participant in enumerate(participants):
        champion = catalog.by_key(participant.get("championId")) if catalog else None
        champion_name = champion.name if champion else champion_ids[index]
        icon = emoji_lookup.champion_emoji(champion, name=champion_ids[index])
        lookup = lookups[index]

        if name_style is NameStyle.CHAMPION:
            label = champion_name
        else:
            label = lookup.riot_id or "-"

        entry = f"{emoji_lookup.prefixed(icon, label)} ({kda_text(participant)})"
        if participant.get("puuid") in highlighted:
            entry = f"**{entry}**"

        rows.append(
            _Row(
                team_id=participant.get("teamId", BLUE_TEAM_ID),
                position=positions[index],
                name=entry,
                rank=rank_text(lookup.rank) or "Unranked",
                value=lookup.rank.value if lookup.rank else None,
            )
        )

    return _columns_from_rows(rows)


def build_lobby_columns(game: dict[str, Any], server: str) -> TeamColumns:
    """Columns for a live game from the Spectator API.

    The lobby's own queue picks which ranked standing is shown — a Flex game
    shows Flex rank — and the column header names that queue, so a Flex rank
    isn't misread as the player's Solo/Duo one.

    A player's rank reads ``"-"`` when the request failed outright and
    ``"Unranked"`` when it succeeded but they have no entry, so a Riot outage
    doesn't look like a lobby full of unranked players. Ranked players also
    get their win rate.
    """
    participants = game.get("participants", []) or []
    if not participants:
        return TeamColumns()

    rank_queue = rank_queue_for_match(game.get("gameQueueConfigId"))

    catalog = ddragon.catalog()
    champions = [catalog.by_key(p.get("championId")) if catalog else None for p in participants]
    champion_ids = [
        champion.internal_id if champion else f"Champion {p.get('championId')}"
        for champion, p in zip(champions, participants)
    ]
    positions = _positions_by_index(participants, champion_ids, use_reported_positions=False)

    lookups = _resolve_concurrently(
        [p.get("puuid") for p in participants],
        lambda puuid: _lookup_player(puuid, server, rank_queue),
    )

    highlighted = tracked_puuids()
    tags = ddragon.champion_tags_by_internal_id()
    rows: list[_Row] = []

    for index, participant in enumerate(participants):
        champion = champions[index]
        champion_name = champion.name if champion else champion_ids[index]
        icon = emoji_lookup.champion_emoji(champion, name=champion_name)
        lookup = lookups[index]

        label = emoji_lookup.prefixed(icon, lookup.riot_id or "-")
        if participant.get("puuid") in highlighted:
            label = f"**{label}**"

        if lookup.rank_unavailable:
            rank_label = "-"
        else:
            rank_label = rank_text(lookup.rank, with_winrate=True) or "Unranked"

        rows.append(
            _Row(
                team_id=participant.get("teamId", BLUE_TEAM_ID),
                position=positions[index],
                name=label,
                rank=rank_label,
                value=lookup.rank.value if lookup.rank else None,
                debug=(
                    f"{champion_name}: spells="
                    f"{(participant.get('spell1Id'), participant.get('spell2Id'))} "
                    f"tags={tags.get(champion_ids[index])} -> {positions[index]}"
                ),
            )
        )

    return _columns_from_rows(rows, rank_header=rank_queue_label(rank_queue))


def profile_author_icon(puuid: str, server: str) -> str | None:
    """Data Dragon URL for a player's current profile icon."""
    return ddragon.profile_icon_url(get_client().profile_icon_id(puuid, server))


__all__ = [
    "NameStyle",
    "ROLE_ORDER",
    "TeamColumns",
    "add_team_columns",
    "average_rank_text",
    "build_lobby_columns",
    "build_match_columns",
    "format_duration",
    "kda_text",
    "make_embed",
    "outcome_color",
    "profile_author_icon",
    "rank_text",
    "relative_time",
    "winrate_text",
]

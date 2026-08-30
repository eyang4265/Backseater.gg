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
from .queues import ARENA_QUEUE_IDS, ARENA_TEAM_SIZES
from .rating import PlayerRating
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
from .riot import RiotAPIError, TTLCache, get_client
from .store import tracked_puuids

LOGGER = logging.getLogger(__name__)

BLUE_TEAM_ID = 100
RED_TEAM_ID = 200
TEAM_IDS = (BLUE_TEAM_ID, RED_TEAM_ID)


_LOOKUP_WORKERS = 10
_MASTERY_CACHE = TTLCache(ttl_seconds=6 * 3600, max_entries=4096)


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
    """Handle text."""
    return (
        f"{participant.get('kills', 0)}/"
        f"{participant.get('deaths', 0)}/"
        f"{participant.get('assists', 0)}"
    )


def rank_text(
    snapshot: RankSnapshot | None, *, with_winrate: bool = False
) -> str | None:
    """``"🏆 Plat II (0 LP)"``. None when there is no rank to show."""
    if snapshot is None or not snapshot.is_ranked:
        return None
    tier = snapshot.tier or ""
    label = TIER_LABELS.get(tier, tier.title())
    if tier in APEX_TIERS or not snapshot.division:
        text = emoji_lookup.prefixed(
            emoji_lookup.rank_emoji(tier), f"{label} ({snapshot.lp} LP)"
        )
    else:
        text = emoji_lookup.prefixed(
            emoji_lookup.rank_emoji(tier),
            f"{label} {snapshot.division} ({snapshot.lp} LP)",
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
    return emoji_lookup.prefixed(
        icon, f"{TIER_LABELS.get(tier, tier.title())} {division} ({lp} LP)"
    )


def winrate_text(snapshot: RankSnapshot | None) -> str | None:
    """Handle text."""
    if snapshot is None or snapshot.winrate is None:
        return None
    return f"{snapshot.winrate * 100:.0f}%"


class NameStyle(Enum):
    """What identifies a player in the left-hand column."""

    SUMMONER = "summoner"

    CHAMPION = "champion"


@dataclass(frozen=True)
class TeamColumns:
    blue_names: list[str] = field(default_factory=list)
    blue_ranks: list[str] = field(default_factory=list)
    red_names: list[str] = field(default_factory=list)
    red_ranks: list[str] = field(default_factory=list)
    blue_average: str = "Unranked"
    red_average: str = "Unranked"
    arena_teams: list[tuple[str, list[str], list[str], str]] = field(default_factory=list)

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


def _game_name_counts(all_riot_ids: Sequence[str]) -> dict[str, int]:
    """Count how many players share each bare game name, computed once per lobby."""
    counts: dict[str, int] = {}
    for other in all_riot_ids:
        game_name = other.split("#", 1)[0]
        counts[game_name] = counts.get(game_name, 0) + 1
    return counts


def _summoner_label(riot_id: str, game_name_counts: dict[str, int]) -> str:
    """Game name alone, unless another player in this game shares it — then Name#Tag."""
    game_name = riot_id.split("#", 1)[0]
    return riot_id if game_name_counts.get(game_name, 0) > 1 else game_name


def add_team_columns(
    embed: discord.Embed, columns: TeamColumns, *, include_rank_rows: bool = True
) -> None:
    """Add blue/red player and rank columns as two aligned inline rows.

    Discord packs inline fields three to a row. Ordering blue players, red
    players, spacer, then blue ranks, red ranks, spacer gives the live-game
    embed the requested two-column team layout while preventing field flow. A
    A full-width rule separates the player and rank rows. The zero-width line
    after each blue value gives stacked mobile fields breathing room before
    the corresponding red field.

    ``include_rank_rows`` is set to false when the name column already shows
    each player's rank, so the bottom rank block isn't shown twice.
    """
    spacer = "​"
    if columns.arena_teams:
        for label, names, _ranks, average in columns.arena_teams:
            embed.add_field(
                name=f"{label} | {average}",
                value=("\n".join(names) or "—"),
                inline=True,
            )
        if not include_rank_rows:
            return
        embed.add_field(name=spacer, value="━━━━━━━━━━━━━━━━━━━━━━━━", inline=False)
        for label, _names, ranks, _average in columns.arena_teams:
            embed.add_field(
                name=f"{label} Rank",
                value=("\n".join(ranks) or "—"),
                inline=True,
            )
        return
    embed.add_field(
        name=f"Blue Team | {columns.blue_average}",
        value=("\n".join(columns.blue_names) or "—") + f"\n{spacer}",
        inline=True,
    )
    embed.add_field(
        name=f"Red Team | {columns.red_average}",
        value="\n".join(columns.red_names) or "—",
        inline=True,
    )
    embed.add_field(name=spacer, value=spacer, inline=True)
    if not include_rank_rows:
        return
    embed.add_field(name=spacer, value="━━━━━━━━━━━━━━━━━━━━━━━━", inline=False)
    embed.add_field(
        name=f"Blue Team {columns.rank_header.rstrip(':')}",
        value=("\n".join(columns.blue_ranks) or "—") + f"\n{spacer}",
        inline=True,
    )
    embed.add_field(
        name=f"Red Team {columns.rank_header.rstrip(':')}",
        value="\n".join(columns.red_ranks) or "—",
        inline=True,
    )
    embed.add_field(name=spacer, value=spacer, inline=True)


@dataclass(frozen=True)
class RatingColumns:
    """Same blue/red (or Arena subteam) grouping as :class:`TeamColumns`.

    A rating embed needs a name column, a score column, and a K/D/A column
    per side rather than the name/rank pair :class:`TeamColumns` carries, so
    it gets its own small struct instead of overloading that one's fields.
    """

    blue_names: list[str] = field(default_factory=list)
    blue_scores: list[str] = field(default_factory=list)
    blue_kda: list[str] = field(default_factory=list)
    red_names: list[str] = field(default_factory=list)
    red_scores: list[str] = field(default_factory=list)
    red_kda: list[str] = field(default_factory=list)
    arena_teams: list[tuple[str, list[str], list[str], list[str]]] = field(
        default_factory=list
    )


def _rating_label(rating: "PlayerRating | None") -> str:
    """Score, label, and MVP/ACE tag: ``"7.4 Good · MVP"``, or "—" if unrated."""
    if rating is None:
        return "—"
    text = f"{rating.score:.1f} {rating.grade}"
    return f"{text} · {rating.label}" if rating.label else text


def build_rating_columns(
    match: dict[str, Any], ratings: dict[str, "PlayerRating"]
) -> RatingColumns:
    """Per-team name/score/KDA columns for :func:`add_rating_columns`.

    Follows the same Top/Jungle/Mid/Bottom/Support ordering, and the same
    Arena-subteam grouping by ``playerSubteamId``, as :func:`build_match_columns`
    so the two embeds read consistently.
    """
    info = match.get("info", {})
    participants = info.get("participants", []) or []
    if not participants:
        return RatingColumns()

    LOGGER.debug("Building rating columns for %d participants", len(participants))
    arena = info.get("queueId") in ARENA_QUEUE_IDS
    catalog = ddragon.catalog()

    def label_for(participant: dict[str, Any]) -> str:
        """Handle for."""
        champion_id = participant.get("championName", "Unknown champion")
        champion = catalog.by_key(participant.get("championId")) if catalog else None
        champion_name = champion.name if champion else champion_id
        icon = emoji_lookup.champion_emoji(champion, name=champion_id)
        return emoji_lookup.prefixed(icon, champion_name)

    def score_for(participant: dict[str, Any]) -> str:
        """Handle for."""
        return _rating_label(ratings.get(participant.get("puuid")))

    if arena:
        groups: dict[int, list[dict[str, Any]]] = {}
        for participant in participants:
            key = participant.get("playerSubteamId") or participant.get("teamId") or 0
            groups.setdefault(key, []).append(participant)
        arena_teams = [
            (
                f"Arena Team {number}",
                [label_for(p) for p in group],
                [score_for(p) for p in group],
                [kda_text(p) for p in group],
            )
            for number, group in enumerate(
                (groups[key] for key in sorted(groups)), start=1
            )
        ]
        return RatingColumns(arena_teams=arena_teams)

    def team(team_id: int) -> tuple[list[str], list[str], list[str]]:
        """Build the team rows for one side."""
        entries = sorted(
            (p for p in participants if p.get("teamId") == team_id),
            key=lambda p: role_sort_key(
                POSITION_LABELS.get(str(p.get("teamPosition") or ""))
            ),
        )
        return (
            [label_for(p) for p in entries],
            [score_for(p) for p in entries],
            [kda_text(p) for p in entries],
        )

    blue_names, blue_scores, blue_kda = team(BLUE_TEAM_ID)
    red_names, red_scores, red_kda = team(RED_TEAM_ID)
    return RatingColumns(
        blue_names=blue_names,
        blue_scores=blue_scores,
        blue_kda=blue_kda,
        red_names=red_names,
        red_scores=red_scores,
        red_kda=red_kda,
    )


_ITEM_SLOTS = ("item0", "item1", "item2", "item3", "item4", "item5", "item6")

# Keep the fallback useful when Data Dragon is unavailable or an older item
# catalog does not contain a boot that appears in a historical timeline.
_KNOWN_BOOT_IDS = frozenset({
    1001, 3005, 3006, 3008, 3009, 3010, 3013, 3020, 3047, 3111, 3117,
    3158, 3168, 3170, 3171, 3172, 3173, 3174, 3175, 3176,
})

_KNOWN_BOOT_NAMES = {
    1001: "Boots",
    3005: "Ghostcrawlers",
    3006: "Berserker's Greaves",
    3008: "Gluttonous Greaves",
    3009: "Boots of Swiftness",
    3010: "Symbiotic Soles",
    3013: "Synchronized Souls",
    3020: "Sorcerer's Shoes",
    3047: "Plated Steelcaps",
    3111: "Mercury's Treads",
    3117: "Mobility Boots",
    3158: "Ionian Boots of Lucidity",
    3168: "Immortal Path",
    3170: "Swiftmarch",
    3171: "Crimson Lucidity",
    3172: "Gunmetal Greaves",
    3173: "Chainlaced Crushers",
    3174: "Armored Advance",
    3175: "Spellslinger's Shoes",
    3176: "Forever Forward",
}


@dataclass(frozen=True)
class InventoryColumns:
    """Same blue/red (or Arena subteam) grouping as :class:`TeamColumns`.

    Pairs each player's name with their final item-slot icons, so it gets its
    own small struct rather than overloading :class:`TeamColumns`.
    """

    blue_names: list[str] = field(default_factory=list)
    blue_items: list[str] = field(default_factory=list)
    red_names: list[str] = field(default_factory=list)
    red_items: list[str] = field(default_factory=list)
    arena_teams: list[tuple[str, list[str], list[str]]] = field(default_factory=list)


_EMPTY_ITEM_SLOT = "⬛"


def _is_boot_item(item_id: Any) -> bool:
    """Return whether an item id is a completed boot item."""
    try:
        identifier = int(item_id)
    except (TypeError, ValueError):
        return False
    if identifier in _KNOWN_BOOT_IDS:
        return True
    metadata = ddragon.item_metadata().get(identifier)
    if metadata is None:
        return False
    return "Boots" in metadata.tags or any(
        word in metadata.name.casefold() for word in ("boots", "shoes", "greaves", "treads")
    )


def _item_icon(item_id: Any) -> Any | None:
    """Resolve an item emoji, retaining a visible fallback for known boots."""
    name = ddragon.item_name(item_id) or _KNOWN_BOOT_NAMES.get(int(item_id)) if item_id else None
    icon = emoji_lookup.item_emoji(name, item_id=item_id)
    if icon is None and _is_boot_item(item_id):
        return "🥾"
    return icon


def _last_purchased_boot(timeline: dict[str, Any] | None, participant_id: Any) -> int | None:
    """Return an ADC's last unsold boot purchase when final slots omit it."""
    if not timeline or participant_id is None:
        return None
    try:
        wanted_id = int(participant_id)
    except (TypeError, ValueError):
        return None
    active_purchases: dict[int, int] = {}
    for frame in (timeline.get("info", {}).get("frames", []) or []):
        for event in (frame.get("events", []) or []):
            try:
                if int(event.get("participantId")) != wanted_id:
                    continue
            except (TypeError, ValueError):
                continue
            event_type = event.get("type")
            item_id = event.get("itemId")
            if event_type == "ITEM_PURCHASED" and _is_boot_item(item_id):
                try:
                    active_purchases[int(item_id)] = int(event.get("timestamp", 0))
                except (TypeError, ValueError):
                    pass
            elif event_type == "ITEM_SOLD" and _is_boot_item(item_id):
                try:
                    active_purchases.pop(int(item_id), None)
                except (TypeError, ValueError):
                    pass
            elif event_type == "ITEM_DESTROYED" and _is_boot_item(item_id):
                # Bot-lane role-quest completion moves boots into the
                # dedicated quest slot and is reported as ITEM_DESTROYED.
                # Keep that boot as the end-of-game fallback; selling it still
                # removes it above.
                pass
            elif event_type == "ITEM_UNDO":
                for key in (event.get("beforeId"), event.get("itemId")):
                    if _is_boot_item(key):
                        try:
                            active_purchases.pop(int(key), None)
                        except (TypeError, ValueError):
                            pass
                after_id = event.get("afterId")
                if _is_boot_item(after_id):
                    try:
                        active_purchases[int(after_id)] = int(event.get("timestamp", 0))
                    except (TypeError, ValueError):
                        pass
    return max(active_purchases.items(), key=lambda item: item[1], default=(None, 0))[0]


def _final_items_text(
    participant: dict[str, Any], timeline: dict[str, Any] | None = None
) -> str:
    """Icon-only row of a participant's end-of-game item slots, trinket included.

    Purchased items are left-packed (an empty slot mid-inventory, from an
    unsold item or no boots, doesn't leave a gap), then padded with ⬛ up to
    six icons so every player's row lines up at the same width. The
    trinket/ward icon follows directly, with no separator.
    """
    core_item_ids = [participant.get(slot) for slot in _ITEM_SLOTS[:-1]]
    has_final_boot = any(_is_boot_item(item_id) for item_id in core_item_ids)
    position = str(participant.get("teamPosition") or "").upper()
    if position in {"BOTTOM", "BOT", "ADC"} and not has_final_boot:
        fallback_boot = _last_purchased_boot(timeline, participant.get("participantId"))
        if fallback_boot:
            LOGGER.debug(
                "Using last purchased ADC boot %s for participant %s",
                fallback_boot,
                participant.get("participantId"),
            )
            core_item_ids.append(fallback_boot)

    core_icons = []
    for item_id in core_item_ids:
        if not item_id:
            continue
        icon = _item_icon(item_id)
        if icon is not None:
            core_icons.append(str(icon))
    core_icons.extend(_EMPTY_ITEM_SLOT for _ in range(len(_ITEM_SLOTS) - 1 - len(core_icons)))

    trinket_id = participant.get(_ITEM_SLOTS[-1])
    trinket_icon = (
        _item_icon(trinket_id)
        if trinket_id
        else None
    )

    icons = list(core_icons)
    if trinket_icon is not None:
        icons.append(str(trinket_icon))
    return " ".join(icons)


def build_inventory_columns(
    match: dict[str, Any], timeline: dict[str, Any] | None = None
) -> InventoryColumns:
    """Per-team name/final-items columns for :func:`add_inventory_columns`.

    Follows the same Top/Jungle/Mid/Bottom/Support ordering, and the same
    Arena-subteam grouping by ``playerSubteamId``, as :func:`build_match_columns`
    so the embed reads consistently with the other Display views.
    """
    info = match.get("info", {})
    participants = info.get("participants", []) or []
    if not participants:
        return InventoryColumns()

    LOGGER.debug("Building inventory columns for %d participants", len(participants))
    arena = info.get("queueId") in ARENA_QUEUE_IDS
    catalog = ddragon.catalog()

    def label_for(participant: dict[str, Any]) -> str:
        """Handle for."""
        champion_id = participant.get("championName", "Unknown champion")
        champion = catalog.by_key(participant.get("championId")) if catalog else None
        champion_name = champion.name if champion else champion_id
        icon = emoji_lookup.champion_emoji(champion, name=champion_id)
        return emoji_lookup.prefixed(icon, champion_name)

    if arena:
        groups: dict[int, list[dict[str, Any]]] = {}
        for participant in participants:
            key = participant.get("playerSubteamId") or participant.get("teamId") or 0
            groups.setdefault(key, []).append(participant)
        arena_teams = [
            (
                f"Arena Team {number}",
                [label_for(p) for p in group],
                [_final_items_text(p, timeline) for p in group],
            )
            for number, group in enumerate(
                (groups[key] for key in sorted(groups)), start=1
            )
        ]
        return InventoryColumns(arena_teams=arena_teams)

    def team(team_id: int) -> tuple[list[str], list[str]]:
        """Build the team rows for one side."""
        entries = sorted(
            (p for p in participants if p.get("teamId") == team_id),
            key=lambda p: role_sort_key(
                POSITION_LABELS.get(str(p.get("teamPosition") or ""))
            ),
        )
        return (
            [label_for(p) for p in entries],
            [_final_items_text(p, timeline) for p in entries],
        )

    blue_names, blue_items = team(BLUE_TEAM_ID)
    red_names, red_items = team(RED_TEAM_ID)
    return InventoryColumns(
        blue_names=blue_names,
        blue_items=blue_items,
        red_names=red_names,
        red_items=red_items,
    )


def add_inventory_columns(embed: discord.Embed, columns: InventoryColumns) -> None:
    """Add a name / final-items column pair per side, two fields to a row.

    Mirrors :func:`add_rating_columns`'s inline-field convention so tabular
    data always renders the same way in this bot.
    """
    if columns.arena_teams:
        for label, names, items in columns.arena_teams:
            embed.add_field(name=label, value="\n".join(names) or "—", inline=True)
            embed.add_field(name="Items", value="\n".join(items) or "—", inline=True)
        return

    embed.add_field(
        name="Blue Team", value="\n".join(columns.blue_names) or "—", inline=True
    )
    embed.add_field(
        name="Items", value="\n".join(columns.blue_items) or "—", inline=True
    )
    embed.add_field(name="​", value="​", inline=True)
    embed.add_field(
        name="Red Team", value="\n".join(columns.red_names) or "—", inline=True
    )
    embed.add_field(
        name="Items", value="\n".join(columns.red_items) or "—", inline=True
    )
    embed.add_field(name="​", value="​", inline=True)


def add_rating_columns(embed: discord.Embed, columns: RatingColumns) -> None:
    """Add a name / score / K-D-A column triple per side, three fields to a row.

    Mirrors :func:`add_team_columns`'s inline-field convention so tabular data
    always renders the same way in this bot: one field per column, Discord
    aligning the rows automatically rather than hand-padded single-line text.
    """
    if columns.arena_teams:
        for label, names, scores, kda in columns.arena_teams:
            embed.add_field(name=label, value="\n".join(names) or "—", inline=True)
            embed.add_field(name="Score", value="\n".join(scores) or "—", inline=True)
            embed.add_field(name="K/D/A", value="\n".join(kda) or "—", inline=True)
        return

    embed.add_field(
        name="Blue Team", value="\n".join(columns.blue_names) or "—", inline=True
    )
    embed.add_field(
        name="Score", value="\n".join(columns.blue_scores) or "—", inline=True
    )
    embed.add_field(
        name="K/D/A", value="\n".join(columns.blue_kda) or "—", inline=True
    )
    embed.add_field(
        name="Red Team", value="\n".join(columns.red_names) or "—", inline=True
    )
    embed.add_field(
        name="Score", value="\n".join(columns.red_scores) or "—", inline=True
    )
    embed.add_field(
        name="K/D/A", value="\n".join(columns.red_kda) or "—", inline=True
    )


def _columns_from_rows(
    rows: Sequence[_Row],
    *,
    rank_header: str = "Rank:",
    arena: bool = False,
    arena_team_size: int | None = None,
) -> TeamColumns:
    """Handle from rows."""

    def team(team_id: int) -> tuple[list[str], list[str], float | None]:
        """Build the team rows for one side."""
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

    if arena:
        team_groups: list[list[_Row]] = []
        distinct_team_ids = {row.team_id for row in rows}
        expected_teams = (
            (len(rows) + arena_team_size - 1) // arena_team_size
            if arena_team_size
            else len(distinct_team_ids)
        )
        # Spectator data for some Arena lobbies reports one shared teamId for
        # every participant. Riot's participant order is team-contiguous in
        # that payload, so recover the six 3-player teams from that order.
        if (
            arena_team_size
            and len(rows) >= arena_team_size
            and len(distinct_team_ids) < expected_teams
        ):
            team_groups = [
                list(rows[index : index + arena_team_size])
                for index in range(0, len(rows), arena_team_size)
            ]
        else:
            for team_id in dict.fromkeys(row.team_id for row in rows):
                team_groups.append([row for row in rows if row.team_id == team_id])

        arena_teams = []
        for number, group in enumerate(team_groups, start=1):
            names = [row.name for row in group]
            ranks = [row.rank for row in group]
            average = average_value([row.value for row in group if row.value is not None])
            arena_teams.append((f"Arena Team {number}", names, ranks, average_rank_text(average)))
        return TeamColumns(arena_teams=arena_teams, rank_header=rank_header,
                           debug_lines=[row.debug for row in rows if row.debug])

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
                    str(
                        participants[index].get("teamPosition")
                        or participants[index].get("individualPosition")
                        or ""
                    )
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


def _resolve_concurrently(
    items: Sequence[Any], resolve: Callable[[Any], Any]
) -> list[Any]:
    """Run a blocking per-player lookup across all players at once.

    These are independent, network-bound, and rate-limited centrally, so
    fanning them out turns ten serial round trips into roughly one.
    """
    if not items:
        return []
    LOGGER.debug("Resolving %d player lookups concurrently", len(items))
    with ThreadPoolExecutor(max_workers=min(_LOOKUP_WORKERS, len(items))) as pool:
        return list(pool.map(resolve, items))


@dataclass(frozen=True)
class _PlayerLookup:
    riot_id: str | None
    rank: RankSnapshot | None

    rank_unavailable: bool


def _lookup_player(
    puuid: str | None, server: str | None, queue_id: int
) -> _PlayerLookup:
    """Handle player."""
    if not puuid or not server:
        return _PlayerLookup(None, None, True)
    client = get_client()
    riot_id = client.riot_id(puuid, server)
    ranks = fetch_ranks(puuid, server)
    if ranks is None:
        return _PlayerLookup(riot_id, None, True)
    return _PlayerLookup(riot_id, ranks.get(queue_id, RankSnapshot()), False)


def _lookup_mastery(
    puuid: str | None, server: str | None, champion_id: int | None
) -> int | None:
    """Points the player has on the champion they're currently playing, if known."""
    if not puuid or not server or champion_id is None:
        return None
    cache_key = (puuid, server, champion_id)

    def fetch() -> int | None:
        LOGGER.debug(
            "Fetching mastery for puuid=%s server=%s champion_id=%s",
            puuid, server, champion_id,
        )
        try:
            mastery = get_client().champion_mastery(puuid, server, champion_id)
        except RiotAPIError:
            return None
        return mastery.get("championPoints", 0) if mastery else 0

    return _MASTERY_CACHE.get_or_set(cache_key, fetch)


def build_match_columns(
    participants: Sequence[dict[str, Any]],
    *,
    server: str | None,
    queue_id: int | None = None,
    highlight_puuids: Iterable[str] = (),
    name_style: NameStyle = NameStyle.SUMMONER,
    show_rank_names: bool = False,
    show_mastery: bool = False,
) -> TeamColumns:
    """Columns for a finished match, ordered Top/Jungle/Mid/Bottom/Support.

    Names are bolded for any player tracked in data.json — not just the ones
    this announcement is about — so a tracked player who happened to be in the
    lobby still stands out. ``queue_id`` selects which ranked queue's standing
    is shown: a Flex match shows Flex rank.

    ``show_rank_names`` swaps the name column's label for each player's rank
    text, for a compact toggle between "who's playing" and "what rank are
    they" views. Each label is separated from its KDA by a centered dot.
    ``show_mastery`` replaces the label with end-of-game champion mastery
    points, using the shared live-game cache before making any new lookup.
    """
    if not participants:
        return TeamColumns()

    LOGGER.debug(
        "Building match columns for %d participants (queue_id=%s, show_mastery=%s)",
        len(participants), queue_id, show_mastery,
    )
    rank_queue = rank_queue_for_match(queue_id)
    arena = queue_id in ARENA_QUEUE_IDS
    highlighted = set(highlight_puuids) | set(tracked_puuids())
    catalog = ddragon.catalog()

    champion_ids = [p.get("championName", "Unknown champion") for p in participants]
    positions = _positions_by_index(
        participants, champion_ids, use_reported_positions=True
    )

    lookups = _resolve_concurrently(
        [(p.get("puuid"), server) for p in participants],
        lambda pair: _lookup_player(pair[0], pair[1], rank_queue),
    )
    all_riot_ids = [lookup.riot_id or "-" for lookup in lookups]
    game_name_counts = _game_name_counts(all_riot_ids)
    masteries = (
        _resolve_concurrently(
            [(p.get("puuid"), server, p.get("championId")) for p in participants],
            lambda item: _lookup_mastery(item[0], item[1], item[2]),
        )
        if show_mastery
        else [None] * len(participants)
    )

    rows: list[_Row] = []
    for index, participant in enumerate(participants):
        champion = catalog.by_key(participant.get("championId")) if catalog else None
        champion_name = champion.name if champion else champion_ids[index]
        icon = emoji_lookup.champion_emoji(champion, name=champion_ids[index])
        lookup = lookups[index]

        rank_label = rank_text(lookup.rank, with_winrate=True) or "Unranked"

        if show_mastery:
            points = masteries[index]
            label = f"{points:,} pts" if points is not None else "-"
        elif show_rank_names:
            label = rank_label
        elif name_style is NameStyle.CHAMPION:
            label = champion_name
        else:
            label = _summoner_label(lookup.riot_id or "-", game_name_counts)

        entry = emoji_lookup.prefixed(icon, label)
        if not show_rank_names:
            entry = f"{entry} · ({kda_text(participant)})"
        if participant.get("puuid") in highlighted:
            entry = f"**{entry}**"

        rows.append(
            _Row(
                team_id=participant.get("teamId", BLUE_TEAM_ID),
                position=positions[index],
                name=entry,
                rank=rank_label,
                value=lookup.rank.value if lookup.rank else None,
            )
        )

    return _columns_from_rows(rows, arena=arena, arena_team_size=ARENA_TEAM_SIZES.get(queue_id))


def build_lobby_columns(
    game: dict[str, Any],
    server: str,
    *,
    queue_id: int | None = None,
    name_style: NameStyle = NameStyle.SUMMONER,
    show_rank_names: bool = False,
    show_mastery: bool = False,
) -> TeamColumns:
    """Columns for a live game from the Spectator API.

    The lobby's own queue picks which ranked standing is shown — a Flex game
    shows Flex rank — and the column header names that queue, so a Flex rank
    isn't misread as the player's Solo/Duo one.

    A player's rank reads ``"-"`` when the request failed outright and
    ``"Unranked"`` when it succeeded but they have no entry, so a Riot outage
    doesn't look like a lobby full of unranked players. Ranked players also
    get their win rate.

    ``show_rank_names`` swaps the name column's label for each player's rank
    text instead of their summoner name or champion, for a compact toggle
    between "who's playing" and "what rank are they" views. ``show_mastery``
    takes priority over both, replacing whichever label would otherwise show
    with the player's mastery points on their live-game champion.
    """
    participants = game.get("participants", []) or []
    if not participants:
        return TeamColumns()

    LOGGER.debug(
        "Building lobby columns for %d participants (queue=%s, show_mastery=%s)",
        len(participants), game.get("gameQueueConfigId"), show_mastery,
    )
    lobby_queue_id = game.get("gameQueueConfigId")
    rank_queue = queue_id or rank_queue_for_match(lobby_queue_id)
    arena = lobby_queue_id in ARENA_QUEUE_IDS

    catalog = ddragon.catalog()
    champions = [
        catalog.by_key(p.get("championId")) if catalog else None for p in participants
    ]
    champion_ids = [
        champion.internal_id if champion else f"Champion {p.get('championId')}"
        for champion, p in zip(champions, participants)
    ]
    positions = _positions_by_index(
        participants, champion_ids, use_reported_positions=False
    )

    lookups = _resolve_concurrently(
        [p.get("puuid") for p in participants],
        lambda puuid: _lookup_player(puuid, server, rank_queue),
    )
    all_riot_ids = [lookup.riot_id or "-" for lookup in lookups]
    game_name_counts = _game_name_counts(all_riot_ids)
    masteries = (
        _resolve_concurrently(
            [(p.get("puuid"), p.get("championId")) for p in participants],
            lambda pair: _lookup_mastery(pair[0], server, pair[1]),
        )
        if show_mastery
        else [None] * len(participants)
    )

    highlighted = tracked_puuids()
    tags = ddragon.champion_tags_by_internal_id()
    rows: list[_Row] = []

    for index, participant in enumerate(participants):
        champion = champions[index]
        champion_name = champion.name if champion else champion_ids[index]
        icon = emoji_lookup.champion_emoji(champion, name=champion_name)
        lookup = lookups[index]

        if lookup.rank_unavailable:
            rank_label = "-"
        else:
            rank_label = rank_text(lookup.rank, with_winrate=True) or "Unranked"

        if show_mastery:
            points = masteries[index]
            label = f"{points:,} pts" if points is not None else "-"
        elif show_rank_names:
            label = rank_label
        elif name_style is NameStyle.CHAMPION:
            label = champion_name
        else:
            label = _summoner_label(lookup.riot_id or "-", game_name_counts)
        label = emoji_lookup.prefixed(icon, label)
        if participant.get("puuid") in highlighted:
            label = f"**{label}**"

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

    return _columns_from_rows(
        rows,
        rank_header=rank_queue_label(rank_queue),
        arena=arena,
        arena_team_size=ARENA_TEAM_SIZES.get(lobby_queue_id),
    )


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

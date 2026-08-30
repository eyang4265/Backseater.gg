"""Pure aggregation for a player's cached champion history."""

from __future__ import annotations

import logging
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Mapping

from .ddragon import ItemMetadata, rune_name
from .config import REPO_ROOT
from .queues import FLEX_QUEUE_ID, RANKED_QUEUE_IDS, SOLO_QUEUE_ID

LOGGER = logging.getLogger(__name__)
_BOOTS_LOG_PATH = REPO_ROOT / "boots.log"
_BOOTS_LOG_LOCK = threading.Lock()
_LOGGED_NO_BOOT_MATCHES: set[str] = set()

ALL_GAMES = "All Games"
RANKED = "Ranked"
RANKED_SOLO_DUO = "Ranked Solo/Duo"
RANKED_FLEX = "Ranked Flex"
QUEUE_SCOPES = (ALL_GAMES, RANKED, RANKED_SOLO_DUO, RANKED_FLEX)
ALL_ROLES = "All Roles"
ROLE_VALUES = (ALL_ROLES, "Top", "Jungle", "Mid", "ADC", "Support")
ROLE_POSITION_IDS = {
    "Top": frozenset({"TOP"}),
    "Jungle": frozenset({"JUNGLE"}),
    "Mid": frozenset({"MIDDLE", "MID"}),
    "ADC": frozenset({"BOTTOM", "BOT", "ADC"}),
    "Support": frozenset({"UTILITY", "SUPPORT"}),
}

# Data Dragon normally supplies the ``Boots`` tag. Keep the common IDs as a
# fallback because historical matches must remain classifiable during a CDN
# outage or when an older item catalog is incomplete.
KNOWN_BOOT_IDS = frozenset({
    1001, 3005, 3006, 3008, 3009, 3010, 3013, 3020, 3047, 3111, 3117, 3158,
    3168, 3170, 3171, 3172, 3173, 3174, 3175, 3176,
})


@dataclass(frozen=True)
class ChoiceRecord:
    """The number of eligible games containing one choice."""

    identifier: int
    name: str
    games: int
    wins: int

    @property
    def losses(self) -> int:
        return self.games - self.wins

    @property
    def win_rate(self) -> float:
        return self.wins / self.games if self.games else 0.0


@dataclass(frozen=True)
class ChampionStatsReport:
    """A player's aggregate record and choice breakdown."""

    champion: str
    queue_scope: str
    games: int
    wins: int
    keystones: tuple[ChoiceRecord, ...]
    runes: tuple[ChoiceRecord, ...]
    items: tuple[ChoiceRecord, ...]
    boots: tuple[ChoiceRecord, ...]
    role: str = ALL_ROLES
    patch: str | None = None
    patches: tuple[str, ...] = ()
    since_patch: bool = False
    earliest_match_timestamp: int | None = None

    @property
    def losses(self) -> int:
        return self.games - self.wins

    @property
    def win_rate(self) -> float:
        return self.wins / self.games if self.games else 0.0


def queue_ids_for_scope(scope: str) -> frozenset[int] | None:
    """Translate a public queue scope to exact queue ids."""
    if scope == ALL_GAMES:
        return None
    if scope == RANKED:
        return frozenset(RANKED_QUEUE_IDS)
    if scope == RANKED_SOLO_DUO:
        return frozenset({SOLO_QUEUE_ID})
    if scope == RANKED_FLEX:
        return frozenset({FLEX_QUEUE_ID})
    raise ValueError(f"Unknown queue scope: {scope}")


def positions_for_role(role: str) -> frozenset[str] | None:
    """Translate a public role label to Match-V5 position values."""
    if role == ALL_ROLES:
        return None
    try:
        return ROLE_POSITION_IDS[role]
    except KeyError as error:
        raise ValueError(f"Unknown role: {role}") from error


def _patch_matches(
    game_version: Any,
    requested_patch: str | None,
    allowed_patches: frozenset[str] | None = None,
    since_patch: bool = False,
) -> bool:
    """Match full or major/minor patch filters against Match-V5 versions."""
    if not requested_patch:
        if not allowed_patches:
            return True
        actual = str(game_version or "").strip().lstrip("vV")
        match = re.match(r"^(\d+\.\d+)", actual)
        return bool(match and match.group(1) in allowed_patches)
    wanted = str(requested_patch).strip().lstrip("vV")
    actual = str(game_version or "").strip().lstrip("vV")
    if since_patch:
        wanted_match = re.match(r"^(\d+\.\d+)", wanted)
        actual_match = re.match(r"^(\d+\.\d+)", actual)
        if not wanted_match or not actual_match:
            return False
        return _patch_sort_key(actual_match.group(1)) >= _patch_sort_key(wanted_match.group(1))
    return bool(wanted and (actual == wanted or actual.startswith(f"{wanted}.")))


def _patch_sort_key(value: str) -> tuple[int, ...]:
    """Sort patch labels numerically rather than lexicographically."""
    try:
        return tuple(int(part) for part in value.split("."))
    except ValueError:
        return (0,)


def _sort(records: Mapping[int, tuple[str, int, int]]) -> tuple[ChoiceRecord, ...]:
    return tuple(
        ChoiceRecord(identifier, name, games, wins)
        for identifier, (name, games, wins) in sorted(
            records.items(),
            key=lambda row: (-(row[1][2] / row[1][1] if row[1][1] else 0), -row[1][1], row[1][0].casefold(), row[0]),
        )
    )


def _add(bucket: dict[int, list[Any]], identifier: int, name: str, win: bool) -> None:
    row = bucket.setdefault(identifier, [name, 0, 0])
    row[1] += 1
    row[2] += int(win)


def _record_no_boot_game(info: Mapping[str, Any], participant: Mapping[str, Any], match_id: str) -> None:
    """Append one confirmed no-boot game's age, score line, and duration."""
    if not match_id:
        return
    with _BOOTS_LOG_LOCK:
        if match_id in _LOGGED_NO_BOOT_MATCHES:
            return
        try:
            if _BOOTS_LOG_PATH.exists() and any(
                f"| {match_id} |" in line
                for line in _BOOTS_LOG_PATH.read_text(encoding="utf-8").splitlines()
            ):
                _LOGGED_NO_BOOT_MATCHES.add(match_id)
                return
        except OSError:
            pass
        end_ms = info.get("gameEndTimestamp")
        if end_ms:
            try:
                ended = datetime.fromtimestamp(float(end_ms) / 1000, tz=timezone.utc)
                age_seconds = max(0, int((datetime.now(timezone.utc) - ended).total_seconds()))
                if age_seconds < 3600:
                    age = f"{age_seconds // 60}m ago"
                elif age_seconds < 86400:
                    age = f"{age_seconds // 3600}h {(age_seconds % 3600) // 60}m ago"
                else:
                    age = f"{age_seconds // 86400}d ago"
                ended_text = ended.isoformat()
            except (TypeError, ValueError, OverflowError, OSError):
                age, ended_text = "unknown ago", "unknown"
        else:
            age, ended_text = "unknown ago", "unknown"
        duration = int(float(info.get("gameDuration") or 0))
        line = (
            f"{datetime.now(timezone.utc).isoformat()} | {match_id} | {age} | "
            f"ended={ended_text} | {participant.get('championName', 'Unknown')} "
            f"{participant.get('teamPosition') or participant.get('individualPosition') or 'UNKNOWN'} | "
            f"score={participant.get('kills', 0)}/{participant.get('deaths', 0)}/{participant.get('assists', 0)} | "
            f"result={'W' if participant.get('win') else 'L'} | "
            f"length={duration // 60}:{duration % 60:02d}\n"
        )
        try:
            with _BOOTS_LOG_PATH.open("a", encoding="utf-8") as log_file:
                log_file.write(line)
            _LOGGED_NO_BOOT_MATCHES.add(match_id)
        except OSError as error:
            LOGGER.warning("Could not write no-boot record to %s: %s", _BOOTS_LOG_PATH, error)


def _is_boot_item(identifier: int, item: ItemMetadata | None) -> bool:
    """Classify a completed boot even when historical tags are incomplete.

    Data Dragon's ``Boots`` tag is authoritative when present, but older or
    partially cached catalogs have omitted that tag for named boot upgrades.
    The name check is deliberately limited to the vocabulary used by League's
    boot items so ordinary items such as Manamune are not misclassified.
    """
    if identifier in KNOWN_BOOT_IDS:
        return True
    if item is None:
        return False
    tags = {tag.casefold() for tag in item.tags}
    if "boots" in tags or "boot" in tags:
        return True
    name = item.name.casefold()
    return any(word in name for word in ("boots", "shoes", "greaves", "treads"))


def _is_completed_item(item: ItemMetadata | None) -> bool:
    """Classify a non-boot final item across Data Dragon catalog revisions.

    Current Data Dragon normally marks legendary items with depth 3. Some
    patch catalogs have supplied a different depth (or omitted it) while
    still retaining the item's component list. The ``into`` list prevents
    that fallback from promoting an intermediate component into a final
    item: an item with an upgrade path is not a completed item.
    """
    if item is None:
        return False
    if item.depth == 3 or (item.depth is not None and item.depth > 3):
        return True
    return bool(item.from_ids) and not item.into_ids and item.depth != 1


def _last_timeline_boot(
    timeline: Mapping[str, Any] | None,
    participant_id: Any,
    metadata: Mapping[int, ItemMetadata] | None = None,
) -> tuple[int, str] | None:
    """Return the latest still-held boot purchase for one participant."""
    if not timeline or participant_id is None:
        return None
    try:
        wanted_id = int(participant_id)
    except (TypeError, ValueError):
        return None
    active: dict[int, tuple[int, str]] = {}
    for frame in (timeline.get("info", {}).get("frames", []) or []):
        for event in (frame.get("events", []) or []):
            try:
                if int(event.get("participantId")) != wanted_id:
                    continue
            except (TypeError, ValueError):
                continue
            event_type = event.get("type")
            item_id = event.get("itemId")
            try:
                identifier = int(item_id)
            except (TypeError, ValueError):
                identifier = 0
            is_boot = _is_boot_item(identifier, (metadata or {}).get(identifier))
            if event_type == "ITEM_PURCHASED" and is_boot:
                active[identifier] = (int(event.get("timestamp", 0)), str(identifier))
            elif event_type == "ITEM_SOLD":
                active.pop(identifier, None)
            elif event_type == "ITEM_DESTROYED":
                # In the 2026 bot-lane role quest, Riot removes the boots
                # from the normal six-slot inventory and places them in the
                # dedicated role-quest slot. The timeline reports that move
                # as ITEM_DESTROYED, so preserve the purchase for ADC stats.
                if not is_boot:
                    active.pop(identifier, None)
            elif event_type == "ITEM_UNDO":
                for key in (event.get("beforeId"), event.get("itemId")):
                    try:
                        active.pop(int(key), None)
                    except (TypeError, ValueError):
                        pass
                try:
                    after_id = int(event.get("afterId"))
                except (TypeError, ValueError):
                    after_id = 0
                if _is_boot_item(after_id, (metadata or {}).get(after_id)):
                    active[after_id] = (int(event.get("timestamp", 0)), str(after_id))
    if not active:
        return None
    return max(active.values(), key=lambda row: row[0])


def aggregate(
    payloads: list[dict[str, Any]],
    puuid: str,
    champion: str,
    queue_scope: str = ALL_GAMES,
    item_metadata: Mapping[int, ItemMetadata] | None = None,
    role: str = ALL_ROLES,
    timelines: Mapping[str, Mapping[str, Any]] | None = None,
    patch: str | None = None,
    patches: frozenset[str] | None = None,
    since_patch: bool = False,
) -> ChampionStatsReport:
    """Aggregate exact participant observations from cached Match-V5 payloads.

    Malformed payloads, duplicate match ids, non-matching participants, remakes,
    and games of 15 minutes or less are ignored. When supplied, ``patch``
    matches either a full version or its major/minor prefix. Unfinished non-boot inventory
    components are excluded when item metadata is available. Confirmed no-boot
    games are recorded once in ``boots.log`` with their age, score, and length.
    Item metadata is injectable so this function remains deterministic and
    network-free in tests.
    """
    LOGGER.debug(
        "Aggregating champion stats: puuid=%s champion=%s queue_scope=%s payloads=%d",
        puuid, champion, queue_scope, len(payloads),
    )
    allowed_queues = queue_ids_for_scope(queue_scope)
    allowed_positions = positions_for_role(role)
    champion_key = champion.casefold()
    metadata = item_metadata or {}
    keystones: dict[int, list[Any]] = {}
    runes: dict[int, list[Any]] = {}
    items: dict[int, list[Any]] = {}
    boots: dict[int, list[Any]] = {}
    seen_matches: set[str] = set()
    games = wins = 0
    earliest_match_timestamp: int | None = None

    for payload in payloads:
        if not isinstance(payload, dict):
            continue
        info = payload.get("info")
        if not isinstance(info, dict):
            continue
        try:
            duration_seconds = float(info.get("gameDuration") or 0)
        except (TypeError, ValueError):
            continue
        if duration_seconds <= 15 * 60:
            continue
        if not _patch_matches(info.get("gameVersion"), patch, patches, since_patch):
            continue
        queue_id = info.get("queueId")
        if allowed_queues is not None and queue_id not in allowed_queues:
            continue
        match_id = str(payload.get("metadata", {}).get("matchId") or "")
        if match_id and match_id in seen_matches:
            continue
        participants = info.get("participants")
        if not isinstance(participants, list):
            continue
        participant = next(
            (row for row in participants if isinstance(row, dict) and row.get("puuid") == puuid),
            None,
        )
        if participant is None or str(participant.get("championName", "")).casefold() != champion_key:
            continue
        if allowed_positions is not None:
            position = str(
                participant.get("teamPosition")
                or participant.get("individualPosition")
                or ""
            ).upper()
            if position not in allowed_positions:
                continue
        if participant.get("gameEndedInEarlySurrender"):
            continue
        if match_id:
            seen_matches.add(match_id)
        try:
            match_timestamp = int(
                info.get("gameEndTimestamp") or info.get("gameStartTimestamp") or 0
            )
        except (TypeError, ValueError):
            match_timestamp = 0
        if match_timestamp and (earliest_match_timestamp is None or match_timestamp < earliest_match_timestamp):
            earliest_match_timestamp = match_timestamp
        win = bool(participant.get("win"))
        games += 1
        wins += int(win)
        seen_keystones: set[int] = set()
        seen_runes: set[int] = set()
        seen_items: set[int] = set()
        seen_boots: set[int] = set()

        perks = participant.get("perks") or {}
        styles = perks.get("styles") if isinstance(perks, dict) else None
        if isinstance(styles, list):
            primary = styles[0] if styles and isinstance(styles[0], dict) else {}
            selections = primary.get("selections") if isinstance(primary, dict) else None
            if isinstance(selections, list) and selections:
                first = selections[0]
                if isinstance(first, dict) and first.get("perk"):
                    try:
                        identifier = int(first["perk"])
                    except (TypeError, ValueError):
                        identifier = 0
                    if identifier and identifier not in seen_keystones:
                        seen_keystones.add(identifier)
                        _add(keystones, identifier, rune_name(identifier) or f"Rune {identifier}", win)
            for style in styles:
                selections = style.get("selections") if isinstance(style, dict) else None
                if not isinstance(selections, list):
                    continue
                for selection in selections[1:] if style is primary else selections:
                    if not isinstance(selection, dict) or not selection.get("perk"):
                        continue
                    try:
                        identifier = int(selection["perk"])
                    except (TypeError, ValueError):
                        continue
                    if identifier and identifier not in seen_runes:
                        seen_runes.add(identifier)
                        _add(runes, identifier, rune_name(identifier) or f"Rune {identifier}", win)

        final_item_ids: list[int] = []
        for raw_id in (participant.get(f"item{slot}") for slot in range(6)):
            try:
                identifier = int(raw_id or 0)
            except (TypeError, ValueError):
                continue
            if not identifier:
                continue
            final_item_ids.append(identifier)
            item = metadata.get(identifier)
            name = item.name if item else (
                f"Boots ({identifier})" if identifier in KNOWN_BOOT_IDS else f"Item {identifier}"
            )
            tags = set(item.tags) if item else set()
            if "Trinket" in tags or "Consumable" in tags:
                continue
            is_boot = _is_boot_item(identifier, item)
            if is_boot:
                bucket = boots
                seen = seen_boots
            elif not _is_completed_item(item):
                continue
            else:
                bucket = items
                seen = seen_items
            # One game is one observation even if malformed payloads contain duplicates.
            if identifier not in seen:
                seen.add(identifier)
                _add(bucket, identifier, name, win)

        position = str(participant.get("teamPosition") or participant.get("individualPosition") or "").upper()
        has_final_boot = any(
            _is_boot_item(identifier, metadata.get(identifier)) for identifier in final_item_ids
        )
        timeline = timelines.get(match_id) if timelines and match_id else None
        recovered = None
        if position in {"BOTTOM", "BOT", "ADC"} and not has_final_boot:
            recovered = _last_timeline_boot(
                timeline, participant.get("participantId"), metadata
            )
            if recovered:
                timestamp, raw_identifier = recovered
                identifier = int(raw_identifier)
                record = metadata.get(identifier)
                _add(boots, identifier, record.name if record else f"Boots ({identifier})", win)
                LOGGER.debug(
                    "Recovered champstats ADC boot %s from timeline at %sms for %s",
                    identifier, timestamp, match_id,
                )
        if not has_final_boot:
            if position in {"BOTTOM", "BOT", "ADC"}:
                if timeline is not None and recovered is None:
                    _add(boots, 0, "No Boots", win)
                    _record_no_boot_game(info, participant, match_id)
            else:
                _add(boots, 0, "No Boots", win)
                _record_no_boot_game(info, participant, match_id)

    LOGGER.info(
        "Aggregated %d games (%d wins) for champion=%s queue_scope=%s",
        games, wins, champion, queue_scope,
    )
    return ChampionStatsReport(
        champion=champion,
        queue_scope=queue_scope,
        games=games,
        wins=wins,
        keystones=_sort(keystones),
        runes=_sort(runes),
        items=_sort(items),
        boots=_sort(boots),
        role=role,
        patch=patch.strip().lstrip("vV") if patch and patch.strip() else None,
        patches=tuple(sorted(set(patches or ()), key=_patch_sort_key, reverse=True)),
        since_patch=since_patch,
        earliest_match_timestamp=earliest_match_timestamp,
    )

"""Pure aggregation for a player's champion matchup win rates or 15-minute lane leads."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from .champstats import ALL_GAMES, ALL_ROLES, ROLE_POSITION_IDS, queue_ids_for_scope
from .timeline import MatchTimeline, opponent_participant_id


_LANING_CHECKPOINT_MS = 15 * 60_000


@dataclass(frozen=True)
class CounterRecord:
    """The record for one enemy champion faced by the selected champion."""

    champion: str
    games: int
    wins: int

    @property
    def losses(self) -> int:
        return self.games - self.wins

    @property
    def win_rate(self) -> float:
        return self.wins / self.games if self.games else 0.0


@dataclass(frozen=True)
class CounterStatsReport:
    """A selected champion's record against every enemy champion."""

    champion: str
    queue_scope: str
    role: str
    games: int
    wins: int
    counters: tuple[CounterRecord, ...]
    enemy_role: str = ALL_ROLES
    usage_filter: bool = False
    laning: bool = False

    @property
    def losses(self) -> int:
        return self.games - self.wins

    @property
    def win_rate(self) -> float:
        return self.wins / self.games if self.games else 0.0


def aggregate(
    payloads: list[dict[str, Any]],
    puuid: str,
    champion: str,
    queue_scope: str = ALL_GAMES,
    role: str = ALL_ROLES,
    *,
    enemy_role: str = ALL_ROLES,
    min_usage_rate: float = 0.0,
    timelines: dict[str, dict[str, Any]] | None = None,
    laning: bool = False,
) -> CounterStatsReport:
    """Aggregate one row per enemy champion in each eligible completed game.

    The selected player's win is credited against all five opposing
    participants, so the result answers "how did this champion perform into
    each enemy champion?" rather than only reporting lane opponents. ``role``
    filters the selected player's role; ``enemy_role`` optionally filters the
    opposing participants' roles. ``min_usage_rate`` removes matchups at or
    below that share of eligible games. When ``laning`` is true, a game is a
    win when the selected player has more total gold than their direct role
    opponent at 15:00; ties and unavailable checkpoints are excluded.
    """
    allowed_queues = queue_ids_for_scope(queue_scope)
    allowed_player_positions = ROLE_POSITION_IDS.get(role) if role != ALL_ROLES else None
    allowed_enemy_positions = ROLE_POSITION_IDS.get(enemy_role) if enemy_role != ALL_ROLES else None
    champion_key = champion.casefold()
    seen_matches: set[str] = set()
    records: dict[str, list[Any]] = defaultdict(lambda: [0, 0, ""])
    games = wins = 0

    for payload in payloads:
        if not isinstance(payload, dict):
            continue
        info = payload.get("info")
        if not isinstance(info, dict) or (allowed_queues is not None and info.get("queueId") not in allowed_queues):
            continue
        match_id = str(payload.get("metadata", {}).get("matchId") or "")
        if match_id and match_id in seen_matches:
            continue
        participants = info.get("participants")
        if not isinstance(participants, list):
            continue
        player = next((row for row in participants if isinstance(row, dict) and row.get("puuid") == puuid), None)
        if player is None or str(player.get("championName", "")).casefold() != champion_key:
            continue
        if allowed_player_positions is not None:
            position = str(player.get("teamPosition") or player.get("individualPosition") or "").upper()
            if position not in allowed_player_positions:
                continue
        if player.get("gameEndedInEarlySurrender"):
            continue
        player_team = player.get("teamId")
        enemy_names = {
            str(row.get("championName") or "Unknown")
            for row in participants
            if (
                isinstance(row, dict)
                and row.get("teamId") != player_team
                and row.get("championName")
                and (
                    allowed_enemy_positions is None
                    or str(row.get("teamPosition") or row.get("individualPosition") or "").upper()
                    in allowed_enemy_positions
                )
            )
        }
        if not enemy_names:
            continue
        if laning:
            timeline = (timelines or {}).get(match_id)
            opponent_id = opponent_participant_id(payload, player)
            if timeline is None or opponent_id is None:
                continue
            match_timeline = MatchTimeline(timeline)
            mine = match_timeline.stats_at(
                player.get("participantId"), _LANING_CHECKPOINT_MS
            )
            theirs = match_timeline.stats_at(opponent_id, _LANING_CHECKPOINT_MS)
            if mine is None or theirs is None or mine["gold"] == theirs["gold"]:
                continue
            win = mine["gold"] > theirs["gold"]
        else:
            win = bool(player.get("win"))
        if match_id:
            seen_matches.add(match_id)
        games += 1
        wins += int(win)
        for enemy in enemy_names:
            key = enemy.casefold()
            row = records[key]
            row[0] += 1
            row[1] += int(win)
            row[2] = enemy

    counters = tuple(
        CounterRecord(name, row[0], row[1])
        for _key, row in sorted(
            records.items(),
            key=lambda item: (
                -(item[1][1] / item[1][0] if item[1][0] else 0),
                -item[1][0],
                item[0],
            ),
        )
        if not min_usage_rate or (row[0] / games if games else 0.0) > min_usage_rate
        for name in (row[2],)
    )
    return CounterStatsReport(
        champion, queue_scope, role, games, wins, counters, enemy_role, bool(min_usage_rate), laning
    )

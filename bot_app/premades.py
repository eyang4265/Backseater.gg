"""Inferred partymates admitted only during a qualifying same-team live game.

Riot's public match/spectator payloads do not provide a confirmed party roster.
Repeated teammates are evidence only; never label absent evidence as solo play
or combine overlapping pairs into a claimed multi-player party.
"""

import logging
from itertools import combinations
from typing import Any

from .match_cache import get_match_cache
from .queues import ARAM_MAYHEM_QUEUE_ID
from .store import Account, remember_partymates

LOGGER = logging.getLogger(__name__)


def participant_riot_id(participant: dict[str, Any]) -> str:
    """Preserve the full Riot ID from match or spectator identity fields."""
    if participant.get("riotId"):
        return str(participant["riotId"])
    if participant.get("riotIdGameName"):
        tag = participant.get("riotIdTagline")
        return str(participant["riotIdGameName"]) + (f"#{tag}" if tag else "")
    return str(participant.get("summonerName") or "")


def eligible_participants(info: dict[str, Any], queue: Any) -> dict[str, dict[str, Any]]:
    """Standard League team identities eligible for shared partymate detection."""
    if not isinstance(queue, int) or queue <= 0:
        return {}
    if info.get("gameMode") == "CHERRY" or 1700 <= queue < 1800:
        return {}
    if "CUSTOM" in str(info.get("gameType", "")).upper():
        return {}
    return {
        p["puuid"]: p for p in info.get("participants", []) or []
        if p.get("puuid") and p.get("teamId") in (100, 200) and not p.get("bot")
    }


def qualifying_partymate_puuids(participants, histories, *, tracked=None, minimum_shared=2) -> set[str]:
    """Shared games meeting the threshold and half of each ten-game history.

    An owner roster scan restricts pairs to those containing a successfully
    scanned tracked account. Announcement inference evaluates all lobby pairs.
    """
    detected = set()
    for first, second in combinations(participants, 2):
        if tracked is not None and first not in tracked and second not in tracked:
            continue
        if participants[first]["teamId"] != participants[second]["teamId"]:
            continue
        a, b = histories[first], histories[second]
        shared = sum(a[mid] == b[mid] and a[mid] in (100, 200) for mid in a.keys() & b.keys())
        if shared >= minimum_shared and shared * 2 >= max(len(a), len(b)):
            detected.update((first, second))
    return detected


def remember_game_partymates(game: dict[str, Any], *, live: bool = False) -> None:
    """Record live encounters and save qualifying partymates without API calls.

    Require two shared games and at least half of each player's last ten
    cached games in the same queue within seven days before this game began.
    The selected game never counts, so the third live game can register the
    pair after two completed games together. Completed views never admit players.
    Mayhem instead counts distinct observed live lobbies, including this one;
    three shared lobbies are required; completed results/notices never count.
    Exclude bots, opponents, unknown teams, custom games, and Arena (whose
    ordinary teamId is not a reliable subteam identity). Missing history or
    timing yields no detection. This adds no separate embed description.
    All qualifying pair members are merged into the private partymate registry
    when the payload identifies their platform; this does not register tracking.
    """
    if not live:
        return
    info = game
    queue = info.get("gameQueueConfigId")
    start = info.get("gameStartTime")
    if not isinstance(start, (int, float)) or start <= 0:
        return
    participants = eligible_participants(info, queue)
    if len(participants) < 2:
        return
    cache = get_match_cache()
    platform = info.get("platformId")
    game_id = info.get("gameId")
    live_histories = {}
    if platform and game_id:
        live_histories = cache.remember_live_team_games(
            f"{str(platform).upper()}_{game_id}", queue, int(start), participants,
        )
    if queue == ARAM_MAYHEM_QUEUE_ID:
        if not live_histories:
            return
        histories = live_histories
    else:
        histories = {
            puuid: cache.recent_team_matches(puuid, queue, int(start))
            for puuid in participants
        }

    detected = qualifying_partymate_puuids(
        participants, histories, minimum_shared=3 if queue == ARAM_MAYHEM_QUEUE_ID else 2,
    )
    if not detected:
        return
    server = info.get("platformId")
    if server:
        try:
            remember_partymates(
                Account("", puuid, str(server).upper(), participant_riot_id(participants[puuid]))
                for puuid in sorted(detected)
            )
        except OSError:
            LOGGER.warning("Could not save inferred partymates", exc_info=True)

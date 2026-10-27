"""Exact known-roster records from cached completed League games."""

from collections import Counter
from dataclasses import dataclass, field

from .queues import ARENA_QUEUE_IDS, QUEUE_NAMES, queue_name


@dataclass
class PartyRecord:
    """One exact roster in one queue; larger rosters never count as subsets."""

    roster: tuple[str, ...]
    queue_id: int
    games: int = 0
    wins: int = 0
    roles: dict[str, Counter] = field(default_factory=dict)
    lineups: Counter = field(default_factory=Counter)


def aggregate_parties(matches, puuid, known_puuids, game_mode=None, size=None):
    """Group three-to-five known teammates; shared teams do not prove a premade."""
    groups = {}
    seen = set()
    known = set(known_puuids) | {puuid}
    for match in matches:
        match_id = match.get("metadata", {}).get("matchId")
        if not match_id or match_id in seen:
            continue
        seen.add(match_id)
        info = match.get("info", {})
        queue = info.get("queueId")
        if queue not in QUEUE_NAMES or queue in ARENA_QUEUE_IDS:
            continue
        if game_mode and game_mode.casefold() not in queue_name(queue).casefold():
            continue
        participants = info.get("participants", [])
        if info.get("resultUnavailable") or any(p.get("gameEndedInEarlySurrender") for p in participants):
            continue
        player = next((p for p in participants if p.get("puuid") == puuid), None)
        if player is None or player.get("teamId") not in (100, 200):
            continue
        team = [p for p in participants if p.get("teamId") == player["teamId"]]
        if len(team) != 5 or any(p.get("bot") or not p.get("puuid") for p in team):
            continue
        roster = tuple(sorted(p["puuid"] for p in team if p["puuid"] in known))
        if not 3 <= len(roster) <= 5 or (size and len(roster) != size):
            continue
        if any(not isinstance(p.get("win"), bool) for p in team):
            continue
        row = groups.setdefault((roster, queue), PartyRecord(roster, queue))
        row.games += 1
        row.wins += int(player["win"])
        lineup = []
        for member in team:
            if member["puuid"] not in roster:
                continue
            role = member.get("teamPosition") or "Unknown"
            row.roles.setdefault(member["puuid"], Counter())[role] += 1
            champion = member.get("championName") or "Unknown"
            lineup.append((member["puuid"], "Wukong" if champion == "MonkeyKing" else champion))
        row.lineups[tuple(sorted(lineup))] += 1
    return sorted(groups.values(), key=lambda r: (-r.games, -r.wins / r.games, r.roster, r.queue_id))

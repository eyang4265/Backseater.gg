"""Observed champion and role group records from cached same-team games."""

from dataclasses import dataclass

from .queues import ARENA_QUEUE_IDS, QUEUE_NAMES, queue_name

ROLE_NAMES = {"TOP": "Top", "JUNGLE": "Jungle", "MIDDLE": "Mid", "BOTTOM": "ADC", "UTILITY": "Support"}


@dataclass
class SynergyRecord:
    """One exact known roster and its ordered picks in one queue."""

    roster: tuple[str, ...]
    queue_id: int
    picks: tuple[str, ...]
    games: int = 0
    wins: int = 0


def aggregate_synergies(matches, puuid, known_puuids, game_mode=None,
                        breakdown="Champions", min_games=3, teammate=None, size=None):
    """Count exact known groups of two to five, keeping identity and queue.

    Larger rosters never contribute smaller subsets. A teammate filter requires
    that identity within the whole group. Missing picks exclude that lineup.
    Observed records establish neither causal synergy nor premade membership.
    """
    groups, seen = {}, set()
    known = set(known_puuids) | {puuid}
    for match in matches:
        match_id = match.get("metadata", {}).get("matchId")
        if not match_id or match_id in seen:
            continue
        seen.add(match_id)
        info = match.get("info", {})
        queue = info.get("queueId")
        if queue not in QUEUE_NAMES or queue in ARENA_QUEUE_IDS or "Co-op" in queue_name(queue):
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
        if len(team) != 5 or len({p.get("puuid") for p in team}) != 5:
            continue
        if any(p.get("bot") or not p.get("puuid") or not isinstance(p.get("win"), bool)
               or p["win"] != player.get("win") for p in team):
            continue
        members = {p["puuid"]: p for p in team if p["puuid"] in known}
        roster = (puuid,) + tuple(sorted(p for p in members if p != puuid))
        if not 2 <= len(roster) <= 5 or (size and len(roster) != size):
            continue
        if teammate and teammate not in roster:
            continue
        if breakdown == "Roles":
            picks = tuple(ROLE_NAMES.get(members[p].get("teamPosition")) for p in roster)
        else:
            picks = tuple(members[p].get("championName") for p in roster)
            picks = tuple("Wukong" if pick == "MonkeyKing" else pick for pick in picks)
        if not all(picks):
            continue
        key = (roster, queue, picks)
        row = groups.setdefault(key, SynergyRecord(*key))
        row.games += 1
        row.wins += int(player["win"])
    return sorted((r for r in groups.values() if r.games >= min_games),
                  key=lambda r: (-r.wins / r.games, -r.games, r.roster,
                                 r.queue_id, r.picks))

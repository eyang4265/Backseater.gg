"""Tracked player–champion win-rate rankings from retained completed matches."""

from dataclasses import dataclass

from .queues import QUEUE_NAMES, RANKED_QUEUE_IDS

QUEUE_CHOICES = ("All Games", "Ranked", *sorted({
    name for name in QUEUE_NAMES.values() if not name.startswith(("Co-op", "Swarm"))
}))


@dataclass(frozen=True)
class BestPlayerRow:
    """One tracked account's champion record within the selected queue scope, combining roles."""

    puuid: str
    champion: str
    games: int
    wins: int

    @property
    def win_rate(self):
        """Return the unrounded win fraction used for ranking."""
        return self.wins / self.games


def aggregate_best_players(matches, tracked_puuids, min_games=10, queue="All Games"):
    """Filter queues before applying the inclusive minimum; deduplicate matches."""
    if queue not in QUEUE_CHOICES:
        raise ValueError(f"Unknown queue: {queue}")
    allowed = None if queue == "All Games" else (
        RANKED_QUEUE_IDS if queue == "Ranked" else
        {queue_id for queue_id, name in QUEUE_NAMES.items() if name == queue}
    )
    tracked = set(tracked_puuids)
    totals = {}
    seen = set()
    for match in matches:
        match_id = match.get("metadata", {}).get("matchId")
        if not match_id or match_id in seen:
            continue
        seen.add(match_id)
        info = match.get("info") or {}
        if allowed is not None and info.get("queueId") not in allowed:
            continue
        participants = info.get("participants") or []
        if info.get("resultUnavailable") or any(p.get("gameEndedInEarlySurrender") for p in participants):
            continue
        counted = set()
        for player in participants:
            puuid = player.get("puuid")
            champion = player.get("championName")
            if puuid not in tracked or puuid in counted or not champion or not isinstance(player.get("win"), bool):
                continue
            counted.add(puuid)
            champion = "Wukong" if champion == "MonkeyKing" else champion
            record = totals.setdefault((puuid, champion), [0, 0])
            record[0] += 1
            record[1] += int(player["win"])
    rows = [BestPlayerRow(puuid, champion, games, wins)
            for (puuid, champion), (games, wins) in totals.items() if games >= min_games]
    return sorted(rows, key=lambda row: (-row.win_rate, -row.games, row.champion.casefold(), row.puuid))

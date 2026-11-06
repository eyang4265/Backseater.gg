"""Worlds game winners from saved metadata and previously received series scores."""

from .config import DATA_DIR
from .store import read_json

CACHE_DIR = DATA_DIR / "worlds_game_details"


def load_game_details(series, game):
    """Reuse validated metadata or recover a winner from unambiguous saved scores."""
    saved = read_json(CACHE_DIR / (game["id"] + ".json"), None)
    if (isinstance(saved, dict) and saved.get("series_id") == series["id"]
            and saved.get("game_id") == game["id"] and saved.get("game_number") == game["number"]
            and saved.get("winner") in series["teams"]
            and set(saved.get("teams", [])) == set(series["teams"])):
        return saved
    if (game.get("number", 0) < 1 or game["number"] > sum(series["score"])
            or not any(g.get("id") == game["id"] and g.get("number") == game["number"]
                       for g in series.get("games", []))):
        return None
    # Recover only results established by saved cumulative scores. Team order can
    # reverse between feed responses, so all comparisons use team identities.
    from .worlds import STATE_PATH
    state = read_json(STATE_PATH, {"channels": {}})
    snapshots = [series]
    for channel in state.get("channels", {}).values():
        receipt = channel.get("series", {}).get(series["id"], {})
        snapshots.append(receipt.get("series_snapshot", {}))
        snapshots.extend(record.get("series", {}) for record in receipt.get("stats_games", {}).values())
    scores = {}
    winners = set()
    for snapshot in snapshots:
        if snapshot.get("id") != series["id"] or set(snapshot.get("teams", [])) != set(series["teams"]):
            continue
        score = snapshot.get("score", [])
        if len(score) != 2 or any(type(n) is not int or n < 0 for n in score):
            continue
        total = sum(score)
        mapped = dict(zip(snapshot["teams"], score))
        scores.setdefault(total, []).append(mapped)
        if total >= game["number"] and min(score) == 0 and max(score) > 0:
            winners.add(snapshot["teams"][score.index(max(score))])
        if (total == game["number"] and snapshot.get("state") == "completed"
                and max(score) == snapshot.get("best_of", 0) // 2 + 1):
            winners.add(snapshot["teams"][score.index(max(score))])
    before = scores.get(game["number"] - 1, [])
    if game["number"] == 1:
        before = [dict.fromkeys(series["teams"], 0)]
    for previous in before:
        for current in scores.get(game["number"], []):
            delta = [current[team] - previous[team] for team in series["teams"]]
            if sorted(delta) == [0, 1]:
                winners.add(series["teams"][delta.index(1)])
    if len(winners) == 1:
        return {"series_id": series["id"], "game_id": game["id"], "game_number": game["number"],
                "teams": list(series["teams"]), "winner": winners.pop(), "source": "saved_scores"}
    return None

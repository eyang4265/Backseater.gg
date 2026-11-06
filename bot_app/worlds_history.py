"""Durable full-year Worlds series archive for the manual game-stats selector."""

from datetime import datetime, timezone
import json
import logging
import threading
from zoneinfo import ZoneInfo

from .config import DATA_DIR
from .lolesports import EsportsUnavailable, fetch_worlds_year
from .store import read_json, write_json
from .worlds import fetch_series, parse_series

LOGGER = logging.getLogger(__name__)
STATE_PATH = DATA_DIR / "worlds_history.json"
_LOCK = threading.Lock()
REFRESH_SECONDS = 3600


def fetch_stats_series(*, now=None):
    """Merge this year's complete saved history with fresh scores; reuse it after restart."""
    now = now or datetime.now(timezone.utc)
    year = now.astimezone(ZoneInfo("America/Los_Angeles")).year

    def eligible(series):
        return (datetime.fromtimestamp(series["start"], timezone.utc).year == year
                and series.get("known_teams", True) and sum(series["score"]) > 0)

    with _LOCK:
        saved = read_json(STATE_PATH, {})
        valid = isinstance(saved, dict) and saved.get("schema") == 1 and saved.get("year") == year
        archive = saved.get("series", []) if valid else []
        refreshed = saved.get("refreshed", 0) if valid else 0
        attempted = saved.get("attempted", refreshed) if valid else 0
        if not isinstance(archive, list):
            archive, refreshed = [], 0
        try:
            recent = fetch_series(include_unknown=True)
        except (EsportsUnavailable, ValueError):
            recent = []
        if not refreshed or now.timestamp() - attempted >= REFRESH_SECONDS:
            attempted = now.timestamp()
            try:
                payload = fetch_worlds_year(year)
                # An empty year is valid; parse_series deliberately rejects empty pages.
                history = parse_series(payload, include_unknown=True) if json.loads(payload)["events"] else []
                archive = archive + history
                refreshed = now.timestamp()
            except (EsportsUnavailable, ValueError):
                if not refreshed:
                    raise EsportsUnavailable("Worlds history unavailable")
                LOGGER.warning("Using saved Worlds year history after refresh failure", exc_info=True)
        merged = {}
        for series in archive + recent:
            if not eligible(series):
                continue
            previous = merged.get(series["id"])
            if previous is None or sum(series["score"]) >= sum(previous["score"]):
                merged[series["id"]] = series
        result = sorted(merged.values(), key=lambda s: (s["start"], s["id"]), reverse=True)
        updated = {"schema": 1, "year": year, "refreshed": refreshed, "attempted": attempted, "series": result}
        if updated != saved:
            write_json(STATE_PATH, updated)
        return result

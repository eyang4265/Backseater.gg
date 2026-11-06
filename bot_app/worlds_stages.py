"""Verified entering Swiss brackets and explicit knockout round labels."""

from datetime import datetime, timezone
import logging
import re
import threading
import time

from .config import DATA_DIR
from .lolesports import EsportsUnavailable, fetch_worlds_year
from .store import read_json

LOGGER = logging.getLogger(__name__)
_LOCK = threading.Lock()
_HISTORY = {}


def stage_label(series):
    """Name the series' entering Swiss W-L Bracket or published knockout round."""
    stage = series["stage"].strip()
    if stage.lower() == "swiss":
        record = series.get("swiss_record")
        if record is None:
            match = re.fullmatch(r"Worlds Swiss Stage ([0-2])-([0-2])", series.get("live_title", ""))
            if match:
                record = [int(n) for n in match.groups()]
        return f"Swiss · {record[0]}-{record[1]} Bracket" if record is not None else "Swiss"
    return {"quarterfinals": "Quarterfinal", "quarterfinal": "Quarterfinal",
            "semifinals": "Semifinal", "semifinal": "Semifinal",
            "finals": "Final", "final": "Final"}.get(stage.lower(), stage)


def label_swiss_brackets(series_list, history=()):
    """Attach verified pre-series records, counting completed series rather than games."""
    from .worlds_bracket import bracket_state

    merged = {}
    for series in list(history) + list(series_list):
        if not series.get("known_teams", True):
            continue
        previous = merged.get(series["id"])
        priority = {"unstarted": 0, "inProgress": 1, "completed": 2}
        if previous is None or (sum(series["score"]), priority[series["state"]]) >= (sum(previous["score"]), priority[previous["state"]]):
            merged[series["id"]] = series
    try:
        groups = bracket_state(list(merged.values()))["groups"]
    except ValueError:
        return
    records = {series["id"]: (wins, losses)
               for (_, (wins, losses)), group in groups.items() for series in group}
    for series in series_list:
        if series["id"] in records:
            wins, losses = records[series["id"]]
            series["swiss_record"] = [wins, losses]
            series["live_title"] = f"Worlds Swiss Stage {wins}-{losses}"


def enrich_stage_labels(series_list):
    """Fill missing Swiss records from saved/full-year history, cached hourly.

    History failures leave the schedule usable without guessing a bracket.
    Raw parsing stays network-free; callers fetch/enrich after acknowledgement.
    """
    from .worlds import parse_series

    missing = [s for s in series_list if s["stage"].lower() == "swiss"]
    if not missing:
        return
    year = datetime.fromtimestamp(max(s["start"] for s in missing), timezone.utc).year
    with _LOCK:
        cached = _HISTORY.get(year)
        if cached is None or time.monotonic() >= cached[0]:
            saved = read_json(DATA_DIR / "worlds_history.json", {})
            history = saved.get("series", []) if saved.get("year") == year and saved.get("schema") == 1 else []
            if not history or time.time() - saved.get("refreshed", 0) >= 3600:
                try:
                    history = parse_series(fetch_worlds_year(year), include_unknown=True)
                except (EsportsUnavailable, ValueError):
                    LOGGER.warning("Swiss bracket history unavailable; using saved records")
            _HISTORY[year] = (time.monotonic() + 3600, history)
        else:
            history = cached[1]
    for series in missing:
        series.pop("swiss_record", None)
        series.pop("live_title", None)
    if history:
        label_swiss_brackets(series_list, history)

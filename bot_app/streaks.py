"""Verified three-game League streak markers for announcement summaries."""

from __future__ import annotations

import logging
from typing import Any

from .riot import RiotAPIError, TTLCache, get_client

LOGGER = logging.getLogger(__name__)
_MARKERS = TTLCache(ttl_seconds=300)


def _outcome(match: dict[str, Any], puuid: str) -> bool | None:
    """Require an explicit finished result; unknown results/remakes break streaks."""
    info = match.get("info", {})
    if not info.get("gameEndTimestamp") or info.get("resultUnavailable"):
        return None
    participant = next((p for p in info.get("participants", [])
                        if p.get("puuid") == puuid), {})
    if participant.get("gameEndedInEarlySurrender"):
        return None
    win = participant.get("win")
    return win if isinstance(win, bool) else None


def streak_marker(puuid: str, server: str | None, start_ms: int | None,
                  current_match: dict[str, Any] | None = None) -> str:
    """Return 🔥/🥶 only for three consecutive verified wins/losses across queues.

    Completed summaries include the selected result; live summaries use three
    prior completed results. Bound history before the selected game's start so
    historical views cannot use later games. Missing evidence omits the marker.
    RiotClient owns transport/cache/retries; cache markers for five minutes.
    """
    if not server or not isinstance(start_ms, (int, float)) or start_ms <= 0:
        return ""
    current = _outcome(current_match, puuid) if current_match is not None else None
    if current_match is not None and current is None:
        return ""

    def lookup() -> str:
        client = get_client()
        try:
            needed = 2 if current_match is not None else 3
            ids = client.match_ids(puuid, server, count=needed,
                                   end_time=int(start_ms / 1000) - 1)
            if len(ids) != needed or len(set(ids)) != needed:
                return ""
            outcomes = [] if current_match is None else [current]
            previous_start = start_ms
            for match_id in ids:
                match = client.match(match_id, server)
                info = match.get("info", {})
                started = info.get("gameStartTimestamp") or info.get("gameCreation")
                ended = info.get("gameEndTimestamp")
                if (not isinstance(started, (int, float)) or started >= previous_start
                        or not isinstance(ended, (int, float)) or ended > start_ms):
                    return ""
                result = _outcome(match, puuid)
                if result is None or (outcomes and result != outcomes[0]):
                    return ""
                outcomes.append(result)
                previous_start = started
            return " 🔥" if outcomes[0] else " 🥶"
        except RiotAPIError as error:
            LOGGER.debug("Streak unavailable for %s: %s", puuid, error)
            return ""

    return _MARKERS.get_or_set((puuid, server, start_ms, current), lookup)

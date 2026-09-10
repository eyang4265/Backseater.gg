"""Queries over persisted LP history; no Riot requests required."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .store import PlayerState

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class LpSummary:
    net: int
    wins: int
    losses: int
    unattributed: int
    biggest_gain: int | None
    biggest_loss: int | None

    @property
    def games(self) -> int:
        """Handle games."""
        return self.wins + self.losses


def since_local_midnight(
    state: PlayerState,
    queue_id: int,
    timezone_name: str,
    *,
    now: datetime | None = None,
) -> list[dict]:
    """Return queue history recorded since the current local day began."""
    return since_local_days(state, queue_id, timezone_name, days=1, now=now)


def since_local_days(
    state: PlayerState,
    queue_id: int,
    timezone_name: str,
    *,
    days: int,
    now: datetime | None = None,
) -> list[dict]:
    """Return history from today and the preceding local calendar days."""
    if days < 1:
        raise ValueError("days must be at least 1")
    zone = ZoneInfo(timezone_name)
    current = now.astimezone(zone) if now else datetime.now(zone)
    local_midnight = current.replace(hour=0, minute=0, second=0, microsecond=0)
    cutoff = (local_midnight - timedelta(days=days - 1)).timestamp()
    entries = [
        entry
        for entry in state.history.get(queue_id, [])
        if entry.get("t", 0) >= cutoff
    ]
    LOGGER.debug(
        "LP history for %s local day(s) (%s, queue_id=%s): %s entries",
        days,
        timezone_name,
        queue_id,
        len(entries),
    )
    return entries


def summarize(entries: list[dict]) -> LpSummary:
    """Summarize summarize."""
    attributed_deltas = [
        (entry, int(entry["d"])) for entry in entries if entry.get("d") is not None
    ]
    gains = [delta for _, delta in attributed_deltas if delta > 0]
    losses = [delta for _, delta in attributed_deltas if delta < 0]
    LOGGER.debug(
        "Summarizing %s LP entries (%s attributed)", len(entries), len(attributed_deltas)
    )
    return LpSummary(
        net=sum(delta for _, delta in attributed_deltas),
        wins=sum(entry.get("w") is True for entry, _ in attributed_deltas),
        losses=sum(entry.get("w") is False for entry, _ in attributed_deltas),
        unattributed=sum(entry.get("d") is None for entry in entries),
        biggest_gain=max(gains, default=None),
        biggest_loss=min(losses, default=None),
    )


def summary_text(summary: LpSummary) -> str:
    """Handle text."""
    sign = "+" if summary.net >= 0 else ""
    lines = [
        f"**Net LP:** {sign}{summary.net}",
        f"**Record:** {summary.wins}W–{summary.losses}L ({summary.games} attributed games)",
    ]
    if summary.biggest_gain is not None:
        lines.append(f"**Biggest gain:** +{summary.biggest_gain} LP")
    if summary.biggest_loss is not None:
        lines.append(f"**Biggest loss:** {summary.biggest_loss} LP")
    return "\n".join(lines)

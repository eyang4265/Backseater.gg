"""Queries over persisted LP history; no Riot requests required."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from .store import PlayerState


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
    """Handle local midnight."""
    zone = ZoneInfo(timezone_name)
    current = now.astimezone(zone) if now else datetime.now(zone)
    cutoff = current.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    return [
        entry
        for entry in state.history.get(queue_id, [])
        if entry.get("t", 0) >= cutoff
    ]


def summarize(entries: list[dict]) -> LpSummary:
    """Summarize summarize."""
    attributed = [entry for entry in entries if entry.get("d") is not None]
    gains = [int(entry["d"]) for entry in attributed if int(entry["d"]) > 0]
    losses = [int(entry["d"]) for entry in attributed if int(entry["d"]) < 0]
    return LpSummary(
        net=sum(int(entry["d"]) for entry in attributed),
        wins=sum(entry.get("w") is True for entry in attributed),
        losses=sum(entry.get("w") is False for entry in attributed),
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
    if summary.unattributed:
        lines.append(f"**Unattributed games/resyncs:** {summary.unattributed}")
    return "\n".join(lines)

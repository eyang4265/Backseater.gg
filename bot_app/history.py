"""Pure formatting helpers for compact League match histories."""

from __future__ import annotations

from typing import Any

from .queues import queue_name


def _format_duration(total_seconds: int) -> str:
    minutes, seconds = divmod(max(int(total_seconds), 0), 60)
    return f"{minutes}:{seconds:02d}"


def format_match_history_line(
    info: dict[str, Any],
    participant: dict[str, Any],
    *,
    champion_label: str,
    time_label: str,
) -> str:
    """One readable recent-game entry for a Discord embed description."""
    cs = participant.get("totalMinionsKilled", 0) + participant.get("neutralMinionsKilled", 0)
    outcome = "Victory" if participant.get("win") else "Defeat"
    kda = f"{participant.get('kills', 0)}/{participant.get('deaths', 0)}/{participant.get('assists', 0)}"
    return (
        f"{'🟦' if participant.get('win') else '🟥'} **{champion_label}** — {outcome}\n"
        f"{queue_name(info.get('queueId'))} · {kda} · {cs} CS · "
        f"{_format_duration(info.get('gameDuration', 0))} · {time_label}"
    )

"""Pure formatting helpers for compact League match histories."""

from __future__ import annotations

from typing import Any

from .queues import queue_name


def match_history_score(matches: list[dict[str, Any]], puuid: str) -> tuple[int, int]:
    """Return wins and losses for the supplied player's displayed matches."""
    wins = 0
    losses = 0
    for match in matches:
        participant = next(
            (
                entry
                for entry in match.get("info", {}).get("participants", [])
                if entry.get("puuid") == puuid
            ),
            None,
        )
        if participant is None:
            continue
        if participant.get("win"):
            wins += 1
        else:
            losses += 1
    return wins, losses


def _format_duration(total_seconds: int) -> str:
    """Format duration."""
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
    cs = participant.get("totalMinionsKilled", 0) + participant.get(
        "neutralMinionsKilled", 0
    )
    outcome = "Victory" if participant.get("win") else "Defeat"
    kda = f"{participant.get('kills', 0)}/{participant.get('deaths', 0)}/{participant.get('assists', 0)}"
    return (
        f"{'🟦' if participant.get('win') else '🟥'} **{champion_label}** — {outcome}\n"
        f"{queue_name(info.get('queueId'))} · {kda} · {cs} CS · "
        f"{_format_duration(info.get('gameDuration', 0))} · {time_label}"
    )

"""Persistence helpers for public slash-command component views."""

from __future__ import annotations

import logging
from typing import Any

from .commands.champ import ChampionPositionView
from .commands.coachless import CoachlessView
from .opgg import ChampionStats
from .store import load_embed_button_states

LOGGER = logging.getLogger(__name__)


def _champion_stats(payload: dict[str, Any]) -> ChampionStats:
    """Restore serialized OP.GG stats while preserving tuple fields."""
    return ChampionStats(
        **{
            field: tuple(value) if isinstance(value, list) else value
            for field, value in payload.items()
        }
    )


def register_persistent_command_views(bot: Any) -> int:
    """Restore persistent ``/champ`` and ``/coachless`` component views."""
    restored = 0
    for state in load_embed_button_states():
        payload = state.get("payload", {})
        try:
            if state["kind"] == "champ":
                view = ChampionPositionView(
                    str(payload["champion_name"]),
                    str(payload["server"]),
                    str(payload["internal_id"]),
                    {
                        position: _champion_stats(stats)
                        for position, stats in payload["stats_by_position"].items()
                    },
                    payload.get("selected_position"),
                )
            elif state["kind"] == "coachless":
                view = CoachlessView(
                    str(payload["champion_name"]),
                    int(payload["champion_id"]),
                    str(payload["champion_slug"]),
                    str(payload["role"]),
                    str(payload.get("selected", "runes")),
                )
            else:
                continue
            bot.add_view(view, message_id=state["message_id"])
            restored += 1
        except (KeyError, TypeError, ValueError):
            LOGGER.warning("Skipping malformed persistent command view state")
    return restored

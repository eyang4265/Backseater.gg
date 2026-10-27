"""Persistence helpers for public slash-command component views."""

from __future__ import annotations

import logging
from typing import Any

import discord

from .commands.champ import ChampionPositionView
from .commands.champstats import VIEW_KIND as CHAMPSTATS_VIEW_KIND, ChampStatsView
from .commands.coachless import CoachlessView
from .commands.counterstats import VIEW_KIND as COUNTERSTATS_VIEW_KIND, CounterStatsView
from .commands.trends import TrendsGameLengthView
from .champstats import ChampionStatsReport, ChoiceRecord
from .opgg import ChampionStats
from .daily_summary import RecapPaginator
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


def _champstats_report(payload: dict[str, Any]) -> ChampionStatsReport:
    """Restore the nested immutable records in a champion-history report."""
    values = dict(payload)
    for field in ("keystones", "runes", "items", "boots"):
        values[field] = tuple(ChoiceRecord(**row) for row in values.get(field, ()))
    values["patches"] = tuple(values.get("patches", ()))
    return ChampionStatsReport(**values)


def register_persistent_command_views(bot: Any) -> int:
    """Restore persistent public command views after startup."""
    restored = 0
    states = load_embed_button_states()
    LOGGER.debug("Restoring persistent command views from %d stored states", len(states))
    for state in states:
        payload = state.get("payload", {})
        try:
            if state["kind"] == "daily_recap":
                view = RecapPaginator(
                    [discord.Embed.from_dict(embed) for embed in payload["embeds"]],
                    page=int(payload.get("page", 0)),
                )
            elif state["kind"] == "champ":
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
            elif state["kind"] == "trends":
                view = TrendsGameLengthView(
                    str(payload["role"]),
                    tuple(int(value) for value in payload.get(
                        "game_lengths", (0, 25, 30, 35, 40)
                    )),
                    int(payload.get("selected", 0)),
                )
            elif state["kind"] == CHAMPSTATS_VIEW_KIND:
                view = ChampStatsView(
                    _champstats_report(payload["report"]),
                    str(payload["player_name"]),
                    int(payload["author_id"]),
                    bool(payload.get("apply_filter", False)),
                    str(payload.get("selected", "Runes")),
                    int(payload.get("page", 0)),
                )
            elif state["kind"] == COUNTERSTATS_VIEW_KIND:
                view = CounterStatsView(
                    None,
                    str(payload["puuid"]),
                    str(payload["champion"]),
                    str(payload["queue_scope"]),
                    str(payload["player_role"]),
                    bool(payload.get("usage_filter", False)),
                    str(payload["player_name"]),
                    int(payload["author_id"]),
                    laning=bool(payload.get("laning", False)),
                    server=str(payload.get("server", "NA1")),
                    enemy_role=str(payload.get("enemy_role", "All Roles")),
                    page=int(payload.get("page", 0)),
                )
                view.add_role_buttons()
            else:
                continue
            bot.add_view(view, message_id=state["message_id"])
            restored += 1
        except (KeyError, TypeError, ValueError):
            LOGGER.warning("Skipping malformed persistent command view state")
    LOGGER.info("Restored %d persistent command views", restored)
    return restored

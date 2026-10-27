"""Slash-command registration.

Each module exposes a cog and a ``setup(bot)``; :func:`register_all` installs
them. Registration is an explicit call rather than an import side effect, so
nothing depends on modules being imported in a particular order.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from . import (
    add,
    admin,
    ai,
    champ,
    bestplayer,
    championpool,
    champstats,
    coachless,
    counterstats,
    duo,
    emoji,
    dailyrecap,
    flake,
    flakerank,
    guilds,
    info,
    leaguecommands,
    mastery,
    msg,
    patchnotes,
    party,
    synergy,
    player,
    purge,
    rankings,
    registry,
    tftadd,
    tftcommands,
    tftmatch,
    tftmatchhistory,
    tftupdate,
    trends,
    updatepartymate,
)
from .match import setup_jungleproximity, setup_laning, setup_match, setup_timeline

if TYPE_CHECKING:
    import discord

LOGGER = logging.getLogger(__name__)

_SETUPS = (
    player.setup,
    registry.setup,
    add.setup,
    rankings.setup,
    guilds.setup,
    champ.setup,
    bestplayer.setup,
    championpool.setup,
    champstats.setup,
    counterstats.setup,
    coachless.setup,
    duo.setup,
    emoji.setup,
    party.setup,
    synergy.setup,
    dailyrecap.setup,
    flake.setup,
    flakerank.setup,
    mastery.setup,
    setup_timeline,
    setup_jungleproximity,
    setup_laning,
    setup_match,
    leaguecommands.setup,
    tftadd.setup,
    tftcommands.setup,
    tftmatch.setup,
    tftmatchhistory.setup,
    tftupdate.setup,
    trends.setup,
    info.setup,
    patchnotes.setup,
    admin.setup,
    ai.setup,
    msg.setup,
    purge.setup,
    updatepartymate.setup,
)


def register_all(bot: "discord.Bot") -> None:
    """Install every command cog on the client."""
    for setup in _SETUPS:
        setup(bot)
    LOGGER.info(
        "Registered %d command modules",
        len(_SETUPS),
        extra={"category": "STARTUP"},
    )


__all__ = ["register_all"]

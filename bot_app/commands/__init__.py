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
    champstats,
    coachless,
    counterstats,
    duo,
    guilds,
    info,
    leaguecommands,
    mastery,
    msg,
    player,
    rankings,
    registry,
    tftadd,
    tftcommands,
    tftmatch,
    tftmatchhistory,
    tftupdate,
)
from .meetup import setup_meetup
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
    champstats.setup,
    counterstats.setup,
    coachless.setup,
    duo.setup,
    mastery.setup,
    setup_meetup,
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
    info.setup,
    admin.setup,
    ai.setup,
    msg.setup,
)


def register_all(bot: "discord.Bot") -> None:
    """Install every command cog on the client."""
    for setup in _SETUPS:
        setup(bot)
    LOGGER.info("Registered %d command modules", len(_SETUPS))


__all__ = ["register_all"]

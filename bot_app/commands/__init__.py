"""Slash-command registration.

Each module exposes a cog and a ``setup(bot)``; :func:`register_all` installs
them. Registration is an explicit call rather than an import side effect, so
nothing depends on modules being imported in a particular order.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from . import admin, ai, info, mastery, player
from .match import setup_recap, setup_timeline

if TYPE_CHECKING:  # pragma: no cover - typing only
    import discord

LOGGER = logging.getLogger(__name__)

_SETUPS = (
    player.setup,
    mastery.setup,
    setup_timeline,
    setup_recap,
    info.setup,
    admin.setup,
    ai.setup,
)


def register_all(bot: "discord.Bot") -> None:
    """Install every command cog on the client."""
    for setup in _SETUPS:
        setup(bot)
    LOGGER.info("Registered %d command modules", len(_SETUPS))


__all__ = ["register_all"]

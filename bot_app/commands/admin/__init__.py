"""Owner-only command group."""

import discord

from .ownercommands import OwnerCommands
from .ownercommands import setup as _setup_ownercommands
from .selftest import AdminCommands
from .selftest import setup as _setup_selftest

__all__ = ["AdminCommands", "OwnerCommands", "setup"]


def setup(bot: discord.Bot) -> None:
    """Register every owner-only command module with the bot."""
    _setup_selftest(bot)
    _setup_ownercommands(bot)

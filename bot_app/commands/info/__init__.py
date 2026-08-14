"""Public information command group."""

import discord

from .commands import CommandDirectory, setup as setup_commands
from .rotation import RiotInfoCommands, setup as setup_riot_info


__all__ = ["CommandDirectory", "RiotInfoCommands", "setup"]


def setup(bot: discord.Bot) -> None:
    setup_commands(bot)
    setup_riot_info(bot)

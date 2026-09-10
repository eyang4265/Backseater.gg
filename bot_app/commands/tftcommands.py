"""Public ``/tftcommands`` directory generated from registered commands."""

from __future__ import annotations

import logging

import discord
from discord.ext import commands

from .command_directory import build_game_command_directory, public_commands
from .shared import GUILD_IDS, log_command

LOGGER = logging.getLogger(__name__)


class TftCommandDirectory(commands.Cog):
    """List every registered public command in the TFT namespace."""

    def __init__(self, bot: discord.Bot) -> None:
        """Initialize the command directory."""
        self.bot = bot

    @discord.slash_command(
        guild_ids=GUILD_IDS,
        description="List every public Teamfight Tactics command",
    )
    async def tftcommands(self, ctx: discord.ApplicationContext) -> None:
        """Send the live TFT command directory."""
        log_command(ctx)
        embed = build_game_command_directory(self.bot, tft=True)
        await ctx.respond(embed=embed)
        LOGGER.info(
            "Sent TFT command directory (%d commands)",
            len(public_commands(self.bot, tft=True)),
        )


def setup(bot: discord.Bot) -> None:
    """Register the TFT command directory."""
    bot.add_cog(TftCommandDirectory(bot))

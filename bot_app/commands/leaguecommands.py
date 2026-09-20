"""Public ``/leaguecommands`` directory generated from registered commands."""

from __future__ import annotations

import logging

import discord
from discord.ext import commands

from .command_directory import build_game_command_directory, public_commands
from .shared import GUILD_IDS, log_command

LOGGER = logging.getLogger(__name__)


class LeagueCommandDirectory(commands.Cog):
    """List League commands, excluding unrelated meetup and flake tools."""

    def __init__(self, bot: discord.Bot) -> None:
        """Initialize the command directory."""
        self.bot = bot

    @discord.slash_command(
        guild_ids=GUILD_IDS,
        description="List League and general bot commands",
    )
    async def leaguecommands(self, ctx: discord.ApplicationContext) -> None:
        """Send the live League directory without meetup or flake entries."""
        log_command(ctx)
        embed = build_game_command_directory(self.bot, tft=False)
        await ctx.respond(embed=embed)
        LOGGER.info(
            "Sent League command directory (%d commands)",
            len(public_commands(self.bot, tft=False)),
        )


def setup(bot: discord.Bot) -> None:
    """Register the League command directory."""
    bot.add_cog(LeagueCommandDirectory(bot))

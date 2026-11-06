"""Public /stats command for individual Worlds games."""

import discord
from discord.ext import commands

from ..worlds_command import send_worlds_browser
from .shared import GUILD_IDS, log_command


class StatsCommands(commands.Cog):
    """Show this year’s Worlds game stats with archived series and game selection."""

    def __init__(self, bot):
        self.bot = bot

    @discord.slash_command(guild_ids=GUILD_IDS, description="Show player stats, winners, and damage charts for Worlds games")
    async def stats(self, ctx):
        """Open the latest played series game with a series dropdown and played-game buttons."""
        log_command(ctx)
        await send_worlds_browser(ctx, show_stats=True)


def setup(bot):
    """Register the public Worlds game-stat command."""
    bot.add_cog(StatsCommands(bot))

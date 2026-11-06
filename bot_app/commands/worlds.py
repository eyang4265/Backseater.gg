"""Public /worlds schedules and past match-day results."""

import discord
from discord.ext import commands

from ..worlds_command import send_worlds_browser
from .shared import GUILD_IDS, log_command


class WorldsCommands(commands.Cog):
    """Show schedules and results through the shared acknowledged date browser."""

    def __init__(self, bot):
        self.bot = bot

    @discord.slash_command(guild_ids=GUILD_IDS, description="Show Worlds schedules and past results")
    async def worlds(self, ctx):
        """Show live-updating date tabs; /stats provides the game-stat picker."""
        log_command(ctx)
        await send_worlds_browser(ctx, show_stats=False)


def setup(bot):
    """Register the public Worlds status command."""
    bot.add_cog(WorldsCommands(bot))

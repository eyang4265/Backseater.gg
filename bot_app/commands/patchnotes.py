"""Public /patchnotes command for recent bot changes."""

from __future__ import annotations

import asyncio
import logging

import discord
from discord.ext import commands

from ..render import make_embed
from ..services.patch_notes import PatchNotesUnavailable, recent_bot_changes
from .shared import GUILD_IDS, log_command

LOGGER = logging.getLogger(__name__)


class PatchNotesCommands(commands.Cog):
    """Show entries from the maintained bot patch notes file."""

    def __init__(self, bot: discord.Bot) -> None:
        self.bot = bot

    @discord.slash_command(
        guild_ids=GUILD_IDS,
        description="Show recent updates to this bot",
    )
    async def patchnotes(self, ctx: discord.ApplicationContext) -> None:
        """Read current patch notes after acknowledging the interaction."""
        log_command(ctx)
        await ctx.defer()
        try:
            changes = await asyncio.to_thread(recent_bot_changes)
        except PatchNotesUnavailable as error:
            LOGGER.info("Patch notes read failed: %s", error)
            await ctx.respond(embed=make_embed(str(error)))
            return
        lines = [
            f"**{change.date}** — {discord.utils.escape_mentions(discord.utils.escape_markdown(change.summary[:180]))}"
            for change in changes
        ]
        embed = make_embed("\n".join(lines), title="Bot Patch Notes")
        await ctx.respond(embed=embed)


def setup(bot: discord.Bot) -> None:
    """Register the patch notes command."""
    bot.add_cog(PatchNotesCommands(bot))

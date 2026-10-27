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


def patch_notes_pages(changes) -> tuple[str, ...]:
    """Render full notes, splitting at entry/word boundaries within embed limits."""
    pages = []
    current = ""
    for change in changes:
        summary = discord.utils.escape_mentions(discord.utils.escape_markdown(change.summary))
        entry = f"**{change.date}** — {summary}"
        candidate = f"{current}\n\n{entry}" if current else entry
        if len(candidate) <= 4000:
            current = candidate
            continue
        if current:
            pages.append(current)
            current = ""
        while len(entry) > 4000:
            boundary = entry.rfind(" ", 0, 4001)
            if boundary <= 0:
                boundary = 4000
            pages.append(entry[:boundary])
            entry = entry[boundary:]
        current = entry
    if current:
        pages.append(current)
    return tuple(pages)


class PatchNotesCommands(commands.Cog):
    """Show full maintained notes across as many safe embeds as needed."""

    def __init__(self, bot: discord.Bot) -> None:
        self.bot = bot

    @discord.slash_command(
        guild_ids=GUILD_IDS,
        description="Show recent updates to this bot",
    )
    async def patchnotes(self, ctx: discord.ApplicationContext) -> None:
        """Read and send full patch notes after acknowledging the interaction."""
        log_command(ctx)
        await ctx.defer()
        try:
            changes = await asyncio.to_thread(recent_bot_changes)
        except PatchNotesUnavailable as error:
            LOGGER.info("Patch notes read failed: %s", error)
            await ctx.respond(embed=make_embed(str(error)))
            return
        pages = patch_notes_pages(changes)
        for index, description in enumerate(pages, start=1):
            title = "Bot Patch Notes" if len(pages) == 1 else f"Bot Patch Notes ({index}/{len(pages)})"
            await ctx.respond(embed=make_embed(description, title=title))


def setup(bot: discord.Bot) -> None:
    """Register the patch notes command."""
    bot.add_cog(PatchNotesCommands(bot))

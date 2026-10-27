"""Owner-only ``/purge`` command for deleting the bot's recent messages."""

from __future__ import annotations

import logging

import discord
from discord.ext import commands

from .shared import GUILD_IDS, log_command

LOGGER = logging.getLogger(__name__)


class PurgeCommands(commands.Cog):
    """Delete a requested number of recent bot-authored channel messages."""

    def __init__(self, bot: discord.Bot) -> None:
        """Initialize the command cog."""
        self.bot = bot

    @discord.slash_command(
        guild_ids=GUILD_IDS,
        name="purge",
        description="Delete recent messages sent by the bot (owner only)",
    )
    @commands.is_owner()
    @discord.option(
        "number",
        int,
        description="Number of recent bot messages to delete",
        min_value=1,
        max_value=1000,
    )
    async def purge(self, ctx: discord.ApplicationContext, number: int) -> None:
        """Delete the latest ``number`` bot-authored messages in this channel."""
        await ctx.defer(ephemeral=True)
        log_command(ctx, number=number)

        bot_user = self.bot.user
        if bot_user is None:
            await ctx.edit(content="The bot user is not available yet.")
            return

        deleted = 0
        try:
            async for message in ctx.channel.history(limit=None, oldest_first=False):
                if message.author.id != bot_user.id:
                    continue
                try:
                    await message.delete()
                except discord.NotFound:
                    continue
                deleted += 1
                if deleted >= number:
                    break
        except discord.Forbidden:
            LOGGER.warning("/purge lacks message permissions in channel %s", ctx.channel.id)
            await ctx.edit(
                content=(
                    f"Deleted {deleted} bot message(s), then lost permission to "
                    "read or delete messages in this channel."
                )
            )
            return
        except discord.HTTPException as error:
            LOGGER.warning("/purge failed in channel %s: %s", ctx.channel.id, error)
            await ctx.edit(
                content=f"Deleted {deleted} bot message(s) before Discord rejected the request."
            )
            return

        LOGGER.info(
            "Purged %d/%d bot messages from channel %s",
            deleted,
            number,
            ctx.channel.id,
        )
        if deleted == number:
            content = f"Deleted the latest {deleted} bot message(s)."
        else:
            content = f"Deleted {deleted} bot message(s); only {deleted} were found."
        await ctx.edit(content=content)


def setup(bot: discord.Bot) -> None:
    """Register the owner-only purge command."""
    bot.add_cog(PurgeCommands(bot))

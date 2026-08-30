"""Owner-only command for sending a message as the bot."""

from __future__ import annotations

import logging

import discord
from discord.ext import commands

from .shared import GUILD_IDS, log_command

LOGGER = logging.getLogger(__name__)


class MsgCommands(commands.Cog):
    def __init__(self, bot: discord.Bot) -> None:
        """Initialize the instance."""
        self.bot = bot

    @discord.slash_command(
        guild_ids=GUILD_IDS,
        name="msg",
        description="Send a message as the bot (owner only)",
    )
    @commands.is_owner()
    @discord.option("message", str, description="What should the bot say?", max_length=2000)
    @discord.option(
        "channel",
        discord.TextChannel,
        description="Channel to send in (defaults to this channel)",
        required=False,
    )
    async def msg(self, ctx: discord.ApplicationContext, message: str, channel=None):
        """Send ``message`` to ``channel`` (or the invoking channel) as the bot."""
        log_command(ctx, channel=channel, message_length=len(message))
        target = channel or ctx.channel
        try:
            await target.send(message)
        except discord.Forbidden:
            LOGGER.warning("/msg denied permission to send in channel %s", target.id)
            await ctx.respond(
                f"I don't have permission to send messages in {target.mention}.",
                ephemeral=True,
            )
            return
        except discord.HTTPException as error:
            LOGGER.warning("/msg rejected by Discord for channel %s: %s", target.id, error)
            await ctx.respond(
                "Discord rejected that message. Check the bot logs.", ephemeral=True
            )
            return

        LOGGER.info("Sent owner message to channel %s", target.id)
        await ctx.respond(f"Sent to {target.mention}.", ephemeral=True)


def setup(bot: discord.Bot) -> None:
    """Register this command module with the bot."""
    bot.add_cog(MsgCommands(bot))

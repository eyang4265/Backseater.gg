"""Per-guild announcement channel configuration."""

from __future__ import annotations

import asyncio

import discord
from discord.ext import commands

from ..render import make_embed
from ..store import update_guild_channels
from .shared import GUILD_IDS, log_command


def set_guild_channel(guild_id: int | str, channel_id: int) -> None:
    """Set guild channel."""
    update_guild_channels(
        lambda channels: channels.__setitem__(str(guild_id), channel_id)
    )


def unset_guild_channel(guild_id: int | str) -> bool:
    """Handle guild channel."""
    removed = False

    def mutate(channels: dict[str, int]) -> None:
        """Apply the requested state mutation."""
        nonlocal removed
        removed = channels.pop(str(guild_id), None) is not None

    update_guild_channels(mutate)
    return removed


class GuildCommands(commands.Cog):
    def __init__(self, bot: discord.Bot) -> None:
        """Initialize the instance."""
        self.bot = bot

    @discord.slash_command(
        guild_ids=GUILD_IDS, description="Set this server's announcement channel"
    )
    @commands.has_permissions(manage_guild=True)
    @discord.option(
        "channel",
        discord.TextChannel,
        description="Announcement channel",
        required=False,
    )
    async def setchannel(self, ctx, channel=None):
        """Handle setchannel."""
        log_command(ctx, channel=channel)
        if ctx.guild is None:
            await ctx.respond(
                embed=make_embed("This command must be used in a server."),
                ephemeral=True,
            )
            return
        selected = channel or ctx.channel
        await asyncio.to_thread(set_guild_channel, ctx.guild.id, selected.id)
        await ctx.respond(
            embed=make_embed(f"Announcements will be posted in {selected.mention}."),
            ephemeral=True,
        )

    @discord.slash_command(
        guild_ids=GUILD_IDS, description="Remove this server's announcement channel"
    )
    @commands.has_permissions(manage_guild=True)
    async def unsetchannel(self, ctx):
        """Handle unsetchannel."""
        log_command(ctx)
        if ctx.guild is None:
            await ctx.respond(
                embed=make_embed("This command must be used in a server."),
                ephemeral=True,
            )
            return
        removed = await asyncio.to_thread(unset_guild_channel, ctx.guild.id)
        text = (
            "Announcement routing removed."
            if removed
            else "No announcement channel was configured."
        )
        await ctx.respond(embed=make_embed(text), ephemeral=True)


def setup(bot: discord.Bot) -> None:
    """Register this command module with the bot."""
    bot.add_cog(GuildCommands(bot))

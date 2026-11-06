"""Public per-server announcement channel command with selectable categories."""

import asyncio
import logging

import discord
from discord.ext import commands

from ..channel_settings import configure_channel, TYPE_LABELS, worlds_permissions_available, WORLDS_PERMISSION_ERROR
from ..render import make_embed
from .shared import GUILD_IDS, log_command
from .channel_options import CHANNEL_TYPES

LOGGER = logging.getLogger(__name__)


class SetChannelCommands(commands.Cog):
    """Configure tracked-player posts, Worlds posts, or both."""

    @discord.slash_command(guild_ids=GUILD_IDS, description="Set the channel for tracked players, Worlds, or both")
    @commands.has_permissions(manage_guild=True)
    @discord.option("channel", discord.TextChannel, description="Announcement channel", required=False)
    @discord.option("type", str, choices=CHANNEL_TYPES, default="players", description="Which announcements to configure")
    async def setchannel(self, ctx, channel=None, type="players"):
        """Defer before saving the selected type; an omitted channel means this channel."""
        await ctx.defer(ephemeral=True)
        log_command(ctx, channel=channel, type=type)
        if ctx.guild is None:
            await ctx.respond(embed=make_embed("Use `/setchannel` in a server."), ephemeral=True)
            return
        selected = channel or ctx.channel
        if type in ("worlds", "both") and not worlds_permissions_available(selected, ctx.guild.me):
            await ctx.respond(embed=make_embed(WORLDS_PERMISSION_ERROR), ephemeral=True)
            return
        await asyncio.to_thread(configure_channel, ctx.guild.id, selected.id, type)
        LOGGER.info("Guild %s announcement type=%s channel=%s", ctx.guild.id, type, selected.id)
        await ctx.respond(embed=make_embed(f"{TYPE_LABELS[type]} will be posted in {selected.mention}."), ephemeral=True)


def setup(bot):
    """Register the public player announcement channel command."""
    bot.add_cog(SetChannelCommands())

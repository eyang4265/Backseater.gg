"""Public /dailyrecap command displaying the latest saved automatic recap."""

import asyncio

import discord
from discord.ext import commands

from .shared import GUILD_IDS, log_command
from ..config import get_settings
from ..daily_summary import latest_recap
from ..paginator import Paginator
from ..render import make_embed
from ..store import load_guild_channels


class DailyRecapCommands(commands.Cog):
    """Replay saved recap pages from this channel or this server's announcement route."""

    def __init__(self, bot):
        self.bot = bot

    @discord.slash_command(guild_ids=GUILD_IDS, description="Show the last published daily recap for this server")
    async def dailyrecap(self, ctx):
        """Defer before state reads; replay completed recaps or a brief empty status."""
        log_command(ctx)
        await ctx.defer()
        if ctx.guild is None:
            await ctx.respond(embed=make_embed("Use `/dailyrecap` in a server."), ephemeral=True)
            return
        embeds = await asyncio.to_thread(latest_recap, ctx.channel.id)
        if not embeds:
            routes = await asyncio.to_thread(load_guild_channels)
            channel_id = routes.get(str(ctx.guild.id))
            if channel_id is None:
                fallback = self.bot.get_channel(get_settings().announcement_channel_id)
                if getattr(getattr(fallback, "guild", None), "id", None) == ctx.guild.id:
                    channel_id = fallback.id
            if channel_id is not None and channel_id != ctx.channel.id:
                embeds = await asyncio.to_thread(latest_recap, channel_id)
        if not embeds:
            await ctx.respond(embed=make_embed(
                "No daily recap published yet.",
                title="Daily Recap",
            ))
            return
        paginator = Paginator(embeds, author_id=ctx.author.id, page_size=1,
                              render_page=lambda items, _page, _pages: items[0])
        options = {"view": paginator} if paginator.max_page else {}
        paginator.message = await ctx.respond(embed=paginator.render(),
            allowed_mentions=discord.AllowedMentions.none(), **options)


def setup(bot):
    """Register the public daily recap command."""
    bot.add_cog(DailyRecapCommands(bot))

"""Public /bracket command rendering the current Worlds Swiss or knockout stage."""

import asyncio
import logging

import discord
from discord.ext import commands

from ..lolesports import EsportsUnavailable
from ..render import make_embed
from ..worlds import fetch_series
from ..worlds_bracket import build_bracket, bracket_kind
from .shared import GUILD_IDS, log_command

LOGGER = logging.getLogger(__name__)


class BracketCommands(commands.Cog):
    """Show the current bracket as a visible image without watch links."""

    def __init__(self, bot):
        self.bot = bot

    @discord.slash_command(guild_ids=GUILD_IDS, description="Show the current Worlds bracket")
    async def bracket(self, ctx):
        """Acknowledge before fetching data, downloading logos, or rendering the image."""
        log_command(ctx)
        await ctx.defer()
        try:
            series = await asyncio.to_thread(fetch_series, include_unknown=True)
            kind = bracket_kind(series)
            image = await asyncio.to_thread(build_bracket, series, kind=kind)
        except (EsportsUnavailable, ValueError) as error:
            LOGGER.warning("Worlds bracket unavailable: %s", error)
            await ctx.respond(embed=make_embed("Worlds bracket unavailable. Try again shortly.", title="Worlds"))
            return
        embed = discord.Embed(title="Worlds · Bracket", color=0xC8AA6E)
        embed.set_image(url="attachment://worlds-bracket.png")
        await ctx.respond(embed=embed, file=discord.File(image, filename="worlds-bracket.png"),
                          allowed_mentions=discord.AllowedMentions.none())


def setup(bot):
    """Register the public bracket command."""
    bot.add_cog(BracketCommands(bot))

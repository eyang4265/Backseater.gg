"""Jungle proximity analysis command."""

from __future__ import annotations

import asyncio
import logging

import discord
from discord.ext import commands

from ...jungle_proximity_render import build_jungle_proximity_embed
from ...render import make_embed
from ...services.riot_api import RiotAPIError, get_client
from ..shared import GUILD_IDS, SERVERS, log_command, match_reference_index, not_found_embed, target_for

LOGGER = logging.getLogger(__name__)


class JungleProximityCommands(commands.Cog):
    """Register the /jungleproximity command."""

    def __init__(self, bot: discord.Bot) -> None:
        self.bot = bot

    @discord.slash_command(guild_ids=GUILD_IDS, description="Show both junglers' lane proximity for a match")
    @discord.option("server", description="Server", choices=SERVERS, required=False)
    @discord.option("username", description="League or Discord username (defaults to you)", required=False)
    @discord.option("match_id", description="Match ID or recent-game number (1=latest); blank uses latest", required=False)
    async def jungleproximity(self, ctx, server, username, match_id):
        """Show both team junglers' lane-proximity scores, matching /match's Jungle Proximity display."""
        log_command(ctx, server=server, username=username, match_id=match_id)
        await ctx.defer()
        target = await target_for(ctx, server, username)
        if target is None:
            await ctx.respond(embed=not_found_embed(username, server, ctx=ctx))
            return
        if match_id and match_id.isdigit() and match_reference_index(match_id) is None:
            await ctx.respond(embed=make_embed("Recent-game numbers must be between 1 and 20."))
            return
        try:
            index = match_reference_index(match_id)
            if index is not None:
                ids = await asyncio.to_thread(get_client().match_ids, target.puuid, target.server, count=index + 1)
                selected_id = ids[index] if index < len(ids) else None
            else:
                ids = [match_id] if match_id else await asyncio.to_thread(get_client().match_ids, target.puuid, target.server, count=1)
                selected_id = ids[0] if ids else None
            if selected_id is None:
                await ctx.respond(embed=make_embed("No matching game found."))
                return
            match = await asyncio.to_thread(get_client().match, selected_id, target.server)
            try:
                timeline = await asyncio.to_thread(get_client().match_timeline, selected_id, target.server)
            except RiotAPIError:
                LOGGER.debug("No timeline available for match %s; proceeding without it", selected_id)
                timeline = None
        except RiotAPIError as error:
            LOGGER.info("Jungle proximity fetch failed for %s: %s", target.riot_id, error)
            await ctx.respond(embed=make_embed(f"Could not fetch jungle proximity data: {error}"))
            return

        LOGGER.info("Rendering jungle proximity for match %s (%s)", selected_id, target.riot_id)
        embed, heatmap = await build_jungle_proximity_embed(match, timeline)
        if heatmap is not None:
            await ctx.respond(embed=embed, file=heatmap)
        else:
            await ctx.respond(embed=embed)


def setup(bot: discord.Bot) -> None:
    """Register this command module with the bot."""
    bot.add_cog(JungleProximityCommands(bot))

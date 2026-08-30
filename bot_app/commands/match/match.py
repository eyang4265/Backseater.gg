"""The ``/match`` command."""

from __future__ import annotations

import asyncio
import logging

import discord
from discord.ext import commands

from ...announce import (
    MatchAnnouncementView,
    TrackedPlayer,
    build_announcement_embed,
    format_match,
    remember_match_view_state,
)
from ...services.riot_api import RiotAPIError, get_client
from ..shared import (
    GUILD_IDS,
    SERVERS,
    log_command,
    make_embed,
    not_found_embed,
    match_reference_index,
    target_for,
)

LOGGER = logging.getLogger(__name__)


class MatchStatsCommands(commands.Cog):
    def __init__(self, bot: discord.Bot) -> None:
        """Initialize the instance."""
        self.bot = bot

    @discord.slash_command(
        guild_ids=GUILD_IDS,
        description="Show a completed match like the match announcement",
    )
    @discord.option("server", description="Server", choices=SERVERS, required=False)
    @discord.option("username", description="League or Discord username (defaults to you)", required=False)
    @discord.option(
        "match_id", description="Match ID or recent-game number (1=latest); blank uses latest", required=False
    )
    async def match(self, ctx, server, username, match_id):
        """Render a completed match with the same embed and buttons as announcements."""
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
                selected = ids[index] if index < len(ids) else None
            else:
                ids = [match_id] if match_id else await asyncio.to_thread(get_client().match_ids, target.puuid, target.server, count=1)
                selected = ids[0] if ids else None
            if selected is None:
                await ctx.respond(embed=make_embed("No matching game found."))
                return
            match_data = await asyncio.to_thread(get_client().match, selected, target.server)
        except RiotAPIError as error:
            LOGGER.info("Match fetch failed for %s: %s", target.riot_id, error)
            await ctx.respond(embed=make_embed(f"Could not fetch match data: {error}"))
            return
        LOGGER.debug("Formatting match %s for %s", selected, target.riot_id)
        announcement = format_match(
            match_data,
            [TrackedPlayer(puuid=target.puuid, riot_id=target.riot_id, server=target.server)],
            require_finished=False,
            require_ranked_queue=False,
        )
        if announcement is None:
            await ctx.respond(embed=make_embed("That player was not in this match."))
            return
        embed, chart = await build_announcement_embed(announcement)
        message = await ctx.respond(embed=embed, file=chart, view=MatchAnnouncementView(announcement))
        if message is not None:
            await remember_match_view_state(message, message.channel.id, announcement)
        LOGGER.info("Sent /match announcement for match %s (%s)", selected, target.riot_id)


def setup(bot: discord.Bot) -> None:
    """Register this command module with the bot."""
    bot.add_cog(MatchStatsCommands(bot))

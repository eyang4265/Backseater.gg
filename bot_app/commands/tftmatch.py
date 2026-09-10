"""The ``/tftmatch`` completed-match announcement command."""

from __future__ import annotations

import asyncio
import logging

import discord
from discord.ext import commands

from ..announce import (
    MatchAnnouncementView,
    TrackedPlayer,
    build_announcement_embed,
    format_match,
    remember_match_view_state,
)
from ..services.riot_api import RiotAPIError, get_client
from .shared import (
    GUILD_IDS,
    SERVERS,
    log_command,
    make_embed,
    match_reference_index,
    tft_not_found_embed,
    tft_target_for,
)

LOGGER = logging.getLogger(__name__)


class TftMatchCommands(commands.Cog):
    """Expose completed TFT matches through the shared announcement renderer."""

    def __init__(self, bot: discord.Bot) -> None:
        """Initialize the cog."""
        self.bot = bot

    @discord.slash_command(
        guild_ids=GUILD_IDS,
        description="Show a completed TFT match like an automatic announcement",
    )
    @discord.option("server", description="Server", choices=SERVERS, required=False)
    @discord.option(
        "username",
        description="TFT Riot ID or Discord username (defaults to you)",
        required=False,
    )
    @discord.option(
        "match_id",
        description="TFT match ID or recent-game number (1=latest); blank uses latest",
        required=False,
    )
    async def tftmatch(self, ctx, server, username, match_id):
        """Render a completed TFT match through the announcement pipeline."""
        log_command(ctx, server=server, username=username, match_id=match_id)
        await ctx.defer()
        try:
            target = await tft_target_for(ctx, server, username)
        except RiotAPIError as error:
            await ctx.respond(embed=make_embed(f"Could not resolve TFT account: {error}"))
            return
        if target is None:
            await ctx.respond(embed=tft_not_found_embed(username, server, ctx=ctx))
            return
        if match_id and match_id.isdigit() and match_reference_index(match_id) is None:
            await ctx.respond(
                embed=make_embed("Recent-game numbers must be between 1 and 20.")
            )
            return
        try:
            index = match_reference_index(match_id)
            if index is not None:
                ids = await asyncio.to_thread(
                    get_client().tft_match_ids,
                    target.puuid,
                    target.server,
                    count=index + 1,
                )
                selected = ids[index] if index < len(ids) else None
            else:
                ids = (
                    [match_id]
                    if match_id
                    else await asyncio.to_thread(
                        get_client().tft_match_ids,
                        target.puuid,
                        target.server,
                        count=1,
                    )
                )
                selected = ids[0] if ids else None
            if selected is None:
                await ctx.respond(embed=make_embed("No matching TFT game found."))
                return
            match_data = await asyncio.to_thread(
                get_client().tft_match, selected, target.server
            )
        except RiotAPIError as error:
            LOGGER.info("TFT match fetch failed for %s: %s", target.riot_id, error)
            await ctx.respond(embed=make_embed(f"Could not fetch TFT match data: {error}"))
            return

        announcement = format_match(
            match_data,
            [TrackedPlayer(target.puuid, target.riot_id, target.server)],
            require_finished=False,
            require_ranked_queue=False,
            game_type="tft",
        )
        if announcement is None:
            await ctx.respond(embed=make_embed("That player was not in this TFT match."))
            return
        embed, chart = await build_announcement_embed(announcement)
        # TFT announcements never carry a stat chart, and a deferred
        # interaction's followup.send raises AttributeError on file=None
        # (unlike channel.send), so only pass the attachment when there is one.
        respond_kwargs = {"embed": embed, "view": MatchAnnouncementView(announcement)}
        if chart is not None:
            respond_kwargs["file"] = chart
        message = await ctx.respond(**respond_kwargs)
        if message is not None:
            await remember_match_view_state(message, message.channel.id, announcement)
        LOGGER.info("Sent /tftmatch announcement for %s (%s)", selected, target.riot_id)


def setup(bot: discord.Bot) -> None:
    """Register the TFT match command."""
    bot.add_cog(TftMatchCommands(bot))

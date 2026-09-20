"""Laning-phase comparison command."""

from __future__ import annotations

import asyncio
import logging

import discord
from discord.ext import commands

from ...laning_render import build_laning_embed, laning_pages
from ...render import make_embed
from ...services.riot_api import RiotAPIError, get_client
from ..shared import (
    GUILD_IDS,
    MATCH_POSITION_DESCRIPTION,
    SERVERS,
    log_command,
    match_reference_index,
    not_found_embed,
    target_at_match_position,
    target_for,
)

LOGGER = logging.getLogger(__name__)


class LaningView(discord.ui.View):
    """Browse lane comparisons in groups of three checkpoints through game end."""

    def __init__(self, match: dict, timeline: dict, puuid: str) -> None:
        super().__init__(timeout=900)
        self.match = match
        self.timeline = timeline
        self.puuid = puuid
        self.pages = laning_pages(match, timeline)
        self.page = 0
        self._sync_buttons()

    def _sync_buttons(self) -> None:
        """Disable navigation at either end of the available checkpoints."""
        self.previous_button.disabled = self.page == 0
        self.next_button.disabled = self.page == len(self.pages) - 1

    async def _move(self, interaction: discord.Interaction, offset: int) -> None:
        """Acknowledge before rendering the next chart and replacing its attachment."""
        await interaction.response.defer()
        self.page = max(0, min(self.page + offset, len(self.pages) - 1))
        self._sync_buttons()
        embed, chart = await build_laning_embed(
            self.match, self.timeline, self.puuid, self.pages[self.page]
        )
        kwargs = {"embed": embed, "view": self, "attachments": []}
        if chart is not None:
            kwargs["file"] = chart
        await interaction.edit_original_response(**kwargs)

    @discord.ui.button(label="◀ Previous", style=discord.ButtonStyle.secondary)
    async def previous_button(self, button: discord.ui.Button, interaction: discord.Interaction) -> None:
        """Show the preceding checkpoint."""
        await self._move(interaction, -1)

    @discord.ui.button(label="Next ▶", style=discord.ButtonStyle.secondary)
    async def next_button(self, button: discord.ui.Button, interaction: discord.Interaction) -> None:
        """Show the next group of up to three checkpoints."""
        await self._move(interaction, 1)


class LaningCommands(commands.Cog):
    """Register the /laning command."""

    def __init__(self, bot: discord.Bot) -> None:
        self.bot = bot

    @discord.slash_command(
        guild_ids=GUILD_IDS,
        description="Compare lane Gold/XP/CS in groups of three through game end",
    )
    @discord.option("server", description="Server", choices=SERVERS, required=False)
    @discord.option("username", description="League or Discord username (defaults to you)", required=False)
    @discord.option("match_id", description="Match ID or recent-game number (1=latest); blank uses latest", required=False)
    @discord.option("position", int, description=MATCH_POSITION_DESCRIPTION, min_value=1, max_value=10, required=False)
    async def laning(self, ctx, server, username, match_id, position):
        """Compare the target, or a selected match slot, with their lane opponent."""
        log_command(ctx, server=server, username=username, match_id=match_id, position=position)
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
            if position is not None:
                selected_target = target_at_match_position(match, position, target.server)
                if selected_target is None:
                    await ctx.respond(embed=make_embed(f"Match `{selected_id}` has no player in position {position}."))
                    return
                target = selected_target
            try:
                timeline = await asyncio.to_thread(get_client().match_timeline, selected_id, target.server)
            except RiotAPIError:
                LOGGER.debug("No timeline available for match %s; proceeding without it", selected_id)
                timeline = None
        except RiotAPIError as error:
            LOGGER.info("Laning data fetch failed for %s: %s", target.riot_id, error)
            await ctx.respond(embed=make_embed(f"Could not fetch laning-phase data: {error}"))
            return

        LOGGER.info("Rendering laning comparison for match %s (%s)", selected_id, target.riot_id)
        embed, chart = await build_laning_embed(match, timeline, target.puuid)
        view = LaningView(match, timeline, target.puuid) if timeline and embed.fields else None
        if chart is not None:
            await ctx.respond(embed=embed, file=chart, view=view)
        else:
            await ctx.respond(embed=embed, view=view)


def setup(bot: discord.Bot) -> None:
    """Register this command module with the bot."""
    bot.add_cog(LaningCommands(bot))

"""Riot service commands: /rotation, /serverstatus."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Sequence

import discord
from discord.ext import commands

from ... import ddragon
from ...render import make_embed
from ...services.riot_api import RiotAPIError, get_client
from ...routing import DEFAULT_PLATFORM
from ..shared import GUILD_IDS, SERVERS, log_command

LOGGER = logging.getLogger(__name__)


def _english(items: Sequence[dict[str, Any]] | None) -> str | None:
    """Pull the en_US string out of a Riot status translations array."""
    for item in items or ():
        if item.get("locale") == "en_US":
            return item.get("content")
    return None


def _status_line(record: dict[str, Any]) -> str | None:
    """``"Title: latest update"`` for one incident or maintenance."""
    title = _english(record.get("titles"))
    updates = record.get("updates") or []
    body = _english(updates[0].get("translations")) if updates else None
    if not title:
        return None
    return f"**{title}:** {body}" if body else f"**{title}**"


class RiotInfoCommands(commands.Cog):
    def __init__(self, bot: discord.Bot) -> None:
        self.bot = bot

    @discord.slash_command(guild_ids=GUILD_IDS, description="Current Champion Rotation")
    async def rotation(self, ctx):
        """This week's free champion rotation, alphabetically."""
        log_command(ctx)
        await ctx.defer()

        try:
            champion_ids = await asyncio.to_thread(get_client().champion_rotation)
        except RiotAPIError as error:
            await ctx.respond(embed=make_embed(f"Could not fetch the rotation: {error}"))
            return

        catalog = ddragon.catalog()
        names = sorted(
            filter(
                None,
                (
                    (catalog.by_key(champion_id).name if catalog and catalog.by_key(champion_id) else None)
                    for champion_id in champion_ids
                ),
            )
        )
        await ctx.respond(
            embed=make_embed("\n".join(names) or "—", title="Current Champion Rotation")
        )

    @discord.slash_command(guild_ids=GUILD_IDS, description="Server Status")
    @discord.option("server", description="Server", choices=SERVERS, required=False)
    async def serverstatus(self, ctx, server):
        """Any active maintenance or incidents for a server."""
        server = server or DEFAULT_PLATFORM
        log_command(ctx, server=server)
        await ctx.defer()

        try:
            status = await asyncio.to_thread(get_client().platform_status, server)
        except RiotAPIError as error:
            await ctx.respond(embed=make_embed(f"Could not fetch server status: {error}"))
            return

        lines = [
            line
            for record in [*status.get("maintenances", []), *status.get("incidents", [])]
            if (line := _status_line(record))
        ]
        await ctx.respond(
            embed=make_embed(
                "\n".join(lines) or "No recent issues or events to report on this server.",
                title=f"Server Status: {server}",
            )
        )


def setup(bot: discord.Bot) -> None:
    bot.add_cog(RiotInfoCommands(bot))

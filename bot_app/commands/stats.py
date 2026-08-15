"""Aggregate commands backed by the local completed-match cache."""

from __future__ import annotations

import asyncio

import discord
from discord.ext import commands

from .. import ddragon
from ..match_cache import get_match_cache
from ..render import make_embed
from ..store import load_accounts
from .shared import GUILD_IDS, SERVERS, log_command, not_found_embed, target_for


class AggregateCommands(commands.Cog):
    def __init__(self, bot: discord.Bot) -> None:
        """Initialize the instance."""
        self.bot = bot

    @discord.slash_command(
        guild_ids=GUILD_IDS, description="Champion stats from cached matches"
    )
    @discord.option("server", description="Server", choices=SERVERS, required=False)
    @discord.option("summoner", description="Game Name", required=False)
    @discord.option("tag", description="Tagline", required=False)
    @discord.option("user", description="User (defaults to you)", required=False)
    @discord.option("champion", description="Champion", required=False)
    async def championstats(self, ctx, server, summoner, tag, user, champion=None):
        """Handle championstats."""
        log_command(
            ctx, server=server, summoner=summoner, tag=tag, user=user, champion=champion
        )
        await ctx.defer()
        target = await target_for(ctx, server, summoner, tag, user, include_icon=False)
        if target is None:
            await ctx.respond(embed=not_found_embed(summoner, tag, server, user=user))
            return
        rows = await asyncio.to_thread(get_match_cache().champion_stats, target.puuid)
        catalog = await asyncio.to_thread(ddragon.catalog)
        if champion:
            found = catalog.by_query(champion) if catalog else None
            wanted = found.internal_id.casefold() if found else champion.casefold()
            rows = [row for row in rows if row.champion.casefold() == wanted]
        if not rows:
            await ctx.respond(
                embed=make_embed(
                    "No completed cached matches found for that selection."
                )
            )
            return
        lines = []
        for row in rows[:15]:
            found = catalog.by_internal_id(row.champion) if catalog else None
            champion_name = found.name if found else row.champion
            kda = (row.kills + row.assists) / max(row.deaths, 1)
            lines.append(
                f"**{champion_name}** — {row.wins}W {row.games - row.wins}L "
                f"({row.wins / row.games:.0%}) · {kda:.2f} KDA · "
                f"{row.cs / row.games:.0f} CS · {row.damage / row.games:,.0f} dmg"
            )
        await ctx.respond(
            embed=make_embed(
                "\n".join(lines), title=f"Champion Stats — {target.riot_id}"
            )
        )


def setup(bot: discord.Bot) -> None:
    """Register this command module with the bot."""
    bot.add_cog(AggregateCommands(bot))

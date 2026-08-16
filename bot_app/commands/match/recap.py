"""Post-game recaps."""

from __future__ import annotations

import asyncio

import discord
from discord.ext import commands

from ...announce import (
    MatchAnnouncementView,
    TrackedPlayer,
    build_announcement_embed,
    format_match,
    remember_match_view_state,
)
from ...queues import queue_name
from ...services.riot_api import RiotAPIError, get_client
from ..shared import (
    GUILD_IDS,
    SERVERS,
    log_command,
    make_embed,
    not_found_embed,
    match_reference_index,
    set_player_author,
    target_for,
)


def _cs(p: dict) -> int:
    """Handle cs."""
    return p.get("totalMinionsKilled", 0) + p.get("neutralMinionsKilled", 0)


def _pct(value: float, total: float) -> str:
    """Handle pct."""
    return f"{value / total:.0%}" if total else "—"


class StatsCommands(commands.Cog):
    def __init__(self, bot: discord.Bot) -> None:
        """Initialize the instance."""
        self.bot = bot

    @discord.slash_command(
        guild_ids=GUILD_IDS,
        description="Post-game summary with key performance metrics",
    )
    @discord.option("server", description="Server", choices=SERVERS, required=False)
    @discord.option("summoner", description="Game Name", required=False)
    @discord.option("user", description="User (defaults to you)", required=False)
    @discord.option(
        "match_id", description="Match ID or recent-game number (1=latest); blank uses latest", required=False
    )
    async def recap(self, ctx, server, summoner, user, match_id):
        """Show a player's post-game performance summary for a selected match."""
        log_command(
            ctx, server=server, summoner=summoner, user=user, match_id=match_id
        )
        await ctx.defer()
        target = await target_for(ctx, server, summoner, user)
        if target is None:
            await ctx.respond(embed=not_found_embed(summoner, server, user=user))
            return
        if match_id and match_id.isdigit() and match_reference_index(match_id) is None:
            await ctx.respond(embed=make_embed("Recent-game numbers must be between 1 and 20."))
            return
        try:
            reference_index = match_reference_index(match_id)
            if reference_index is not None:
                ids = await asyncio.to_thread(
                    get_client().match_ids,
                    target.puuid,
                    target.server,
                    count=reference_index + 1,
                )
                ids = ids[reference_index : reference_index + 1]
            else:
                ids = [match_id] if match_id else await asyncio.to_thread(
                    get_client().match_ids, target.puuid, target.server, count=1
                )
            if not ids:
                await ctx.respond(embed=make_embed("No matches found."))
                return
            match = await asyncio.to_thread(get_client().match, ids[0], target.server)
        except RiotAPIError as error:
            await ctx.respond(embed=make_embed(f"Could not fetch match data: {error}"))
            return
        ps = match.get("info", {}).get("participants", [])
        p = next((x for x in ps if x.get("puuid") == target.puuid), None)
        if p is None:
            await ctx.respond(embed=make_embed("That player was not in this match."))
            return
        team = [x for x in ps if x.get("teamId") == p.get("teamId")]
        enemy = [x for x in ps if x.get("teamId") != p.get("teamId")]
        minutes = max(match.get("info", {}).get("gameDuration", 0) / 60, 1)
        team_damage = sum(x.get("totalDamageDealtToChampions", 0) for x in team)
        gold_diff = sum(x.get("goldEarned", 0) for x in team) - sum(
            x.get("goldEarned", 0) for x in enemy
        )
        embed = discord.Embed(
            title=f"Game Recap — {queue_name(match.get('info', {}).get('queueId'))} — {ids[0]}",
            description=f"**{p.get('championName', 'Unknown')}** · {'Victory' if p.get('win') else 'Defeat'} · {p.get('kills', 0)}/{p.get('deaths', 0)}/{p.get('assists', 0)}",
            color=discord.Color.green() if p.get("win") else discord.Color.red(),
        )
        embed.add_field(
            name="Performance",
            value=f"**CS/min:** {_cs(p) / minutes:.1f}\n**Vision score:** {p.get('visionScore', 0)}\n**Damage share:** {_pct(p.get('totalDamageDealtToChampions', 0), team_damage)}\n**Gold difference:** {'+' if gold_diff >= 0 else ''}{gold_diff:,}",
            inline=False,
        )
        tips = []
        if (p.get("teamPosition") or "").upper() != "SUPPORT" and _cs(p) / minutes < 6:
            tips.append(
                "Aim for 6+ CS/min: prioritize uncontested waves before rotating."
            )
        if p.get("visionScore", 0) < minutes * 1.2:
            tips.append("Increase warding and sweeper uptime around objectives.")
        if p.get("totalDamageDealtToChampions", 0) / max(team_damage, 1) < 0.18:
            tips.append("Look for safer, more consistent damage windows in fights.")
        if not tips:
            tips.append("Keep the same farm, vision, and fight discipline next game.")
        embed.add_field(
            name="Actionable takeaways",
            value="\n".join(f"• {tip}" for tip in tips[:3]),
            inline=False,
        )
        set_player_author(embed, target)
        await ctx.respond(embed=embed)

    @discord.slash_command(
        guild_ids=GUILD_IDS,
        description="Show a completed match like the match announcement",
    )
    @discord.option("server", description="Server", choices=SERVERS, required=False)
    @discord.option("summoner", description="Game Name", required=False)
    @discord.option("user", description="User (defaults to you)", required=False)
    @discord.option(
        "match_id", description="Match ID or recent-game number (1=latest); blank uses latest", required=False
    )
    async def match(self, ctx, server, summoner, user, match_id):
        """Render a completed match with the same embed and buttons as announcements."""
        log_command(ctx, server=server, summoner=summoner, user=user, match_id=match_id)
        await ctx.defer()
        target = await target_for(ctx, server, summoner, user)
        if target is None:
            await ctx.respond(embed=not_found_embed(summoner, server, user=user))
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
            await ctx.respond(embed=make_embed(f"Could not fetch match data: {error}"))
            return
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


def setup(bot: discord.Bot) -> None:
    """Register this command module with the bot."""
    bot.add_cog(StatsCommands(bot))

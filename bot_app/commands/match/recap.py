"""Post-game recaps."""

from __future__ import annotations

import asyncio

import discord
from discord.ext import commands

from ...services.riot_api import RiotAPIError, get_client
from ...store import load_accounts
from ..shared import GUILD_IDS, SERVERS, Target, log_command, make_embed, not_found_embed, resolve_target, set_player_author


def _cs(p: dict) -> int:
    return p.get("totalMinionsKilled", 0) + p.get("neutralMinionsKilled", 0)


def _pct(value: float, total: float) -> str:
    return f"{value / total:.0%}" if total else "—"


class StatsCommands(commands.Cog):
    def __init__(self, bot: discord.Bot) -> None:
        self.bot = bot

    @discord.slash_command(guild_ids=GUILD_IDS, description="Post-game summary with key performance metrics")
    @discord.option("server", description="Server", choices=SERVERS, required=False)
    @discord.option("summoner", description="Game Name", required=False)
    @discord.option("tag", description="Tagline", required=False)
    @discord.option("user", description="User (defaults to you)", required=False)
    @discord.option("match_id", description="Match ID; blank uses your latest game", required=False)
    async def recap(self, ctx, server, summoner, tag, user, match_id):
        log_command(ctx, server=server, summoner=summoner, tag=tag, user=user, match_id=match_id)
        await ctx.defer()
        target = resolve_target(ctx, server, summoner, tag, user)
        if target is None:
            await ctx.respond(embed=not_found_embed(summoner, tag, server)); return
        try:
            ids = [match_id] if match_id else await asyncio.to_thread(get_client().match_ids, target.puuid, target.server, count=1)
            if not ids: await ctx.respond(embed=make_embed("No matches found.")); return
            match = await asyncio.to_thread(get_client().match, ids[0], target.server)
        except RiotAPIError as error:
            await ctx.respond(embed=make_embed(f"Could not fetch match data: {error}")); return
        ps = match.get("info", {}).get("participants", [])
        p = next((x for x in ps if x.get("puuid") == target.puuid), None)
        if p is None: await ctx.respond(embed=make_embed("That player was not in this match.")); return
        team = [x for x in ps if x.get("teamId") == p.get("teamId")]
        enemy = [x for x in ps if x.get("teamId") != p.get("teamId")]
        minutes = max(match.get("info", {}).get("gameDuration", 0) / 60, 1)
        team_damage = sum(x.get("totalDamageDealtToChampions", 0) for x in team)
        gold_diff = sum(x.get("goldEarned", 0) for x in team) - sum(x.get("goldEarned", 0) for x in enemy)
        embed = discord.Embed(title=f"Game Recap — {ids[0]}", description=f"**{p.get('championName', 'Unknown')}** · {'Victory' if p.get('win') else 'Defeat'} · {p.get('kills', 0)}/{p.get('deaths', 0)}/{p.get('assists', 0)}", color=discord.Color.green() if p.get("win") else discord.Color.red())
        embed.add_field(name="Performance", value=f"**CS/min:** {_cs(p) / minutes:.1f}\n**Vision score:** {p.get('visionScore', 0)}\n**Damage share:** {_pct(p.get('totalDamageDealtToChampions', 0), team_damage)}\n**Gold difference:** {'+' if gold_diff >= 0 else ''}{gold_diff:,}", inline=False)
        tips = []
        if p.get("teamPosition", "").upper() != "SUPPORT" and _cs(p) / minutes < 6:
            tips.append("Aim for 6+ CS/min: prioritize uncontested waves before rotating.")
        if p.get("visionScore", 0) < minutes * 1.2: tips.append("Increase warding and sweeper uptime around objectives.")
        if p.get("totalDamageDealtToChampions", 0) / max(team_damage, 1) < .18: tips.append("Look for safer, more consistent damage windows in fights.")
        if not tips: tips.append("Keep the same farm, vision, and fight discipline next game.")
        embed.add_field(name="Actionable takeaways", value="\n".join(f"• {tip}" for tip in tips[:3]), inline=False)
        set_player_author(embed, target)
        await ctx.respond(embed=embed)

def setup(bot: discord.Bot) -> None:
    bot.add_cog(StatsCommands(bot))

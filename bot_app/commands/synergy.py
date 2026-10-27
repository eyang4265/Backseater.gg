"""Public /synergy champion and role group reports."""

import asyncio

import discord
from discord.ext import commands

from .shared import GUILD_IDS, SERVERS, game_mode_choices, log_command, not_found_embed, target_for
from ..match_cache import get_match_cache
from ..paginator import Paginator
from ..queues import queue_name
from ..render import make_embed
from ..store import load_accounts, load_partymates, load_teammates
from ..synergy_records import aggregate_synergies


def synergy_embed(rows, names, player_name, breakdown, min_games, page=0, pages=1):
    """Render concise lineup results with up to five aligned player/pick rows."""
    embed = make_embed(
        f"**{breakdown} · Minimum {min_games} games**",
        title=f"Synergy — {player_name[:150]}",
    )
    if not rows:
        embed.description += "\nNo cached groups meet these filters."
    else:
        row = rows[0]
        def safe(value):
            return discord.utils.escape_markdown(discord.utils.escape_mentions(str(value)[:80]))
        embed.description += (f"\n**{len(row.roster)} players · {queue_name(row.queue_id)}**\n"
                              f"{row.wins}W–{row.games-row.wins}L · {row.games} games · {row.wins/row.games:.1%} WR")
        embed.add_field(name="Player", value="\n".join(
            safe(names.get(p, "Unknown player")) for p in row.roster), inline=True)
        embed.add_field(name="Champion" if breakdown == "Champions" else "Role",
                        value="\n".join(safe(pick) for pick in row.picks), inline=True)
    embed.set_footer(text=f"Lineup {page+1}/{pages}")
    return embed


class SynergyCommands(commands.Cog):
    """Report cached combinations without fetching missing match history."""

    @discord.slash_command(guild_ids=GUILD_IDS, description="Champion and role combinations for groups of two to five")
    @discord.option("server", description="Player's server", choices=SERVERS, required=False)
    @discord.option("username", description="League or Discord username (defaults to you)", required=False)
    @discord.option("teammate", description="Only groups including this League or Discord username", required=False)
    @discord.option("game_mode", description="Filter by game mode", autocomplete=discord.utils.basic_autocomplete(game_mode_choices), required=False)
    @discord.option("breakdown", description="Compare champion combinations or role combinations", choices=["Champions", "Roles"], required=False)
    @discord.option("min_games", int, description="Minimum games per lineup (default 3)", min_value=1, max_value=1000, required=False)
    @discord.option("size", int, description="Exact group size including you (2–5)", choices=[2, 3, 4, 5], required=False)
    async def synergy(self, ctx, server=None, username=None, teammate=None,
                      game_mode=None, breakdown="Champions", min_games=3, size=None):
        """Acknowledge before lookup; omit the view for empty or single-page reports."""
        log_command(ctx, server=server, username=username, teammate=teammate,
                    game_mode=game_mode, breakdown=breakdown, min_games=min_games, size=size)
        await ctx.defer()
        target = await target_for(ctx, server, username, include_icon=False)
        if target is None:
            await ctx.respond(embed=not_found_embed(username, server, ctx=ctx))
            return
        companion = None
        if teammate:
            companion = await target_for(ctx, server or target.server, teammate, include_icon=False)
            if companion is None:
                await ctx.respond(embed=not_found_embed(teammate, server or target.server, ctx=ctx))
                return
            if companion.puuid == target.puuid:
                await ctx.respond(embed=make_embed("Choose a teammate other than the selected player."))
                return
        tracked, teammates, partymates, matches = await asyncio.gather(
            asyncio.to_thread(load_accounts), asyncio.to_thread(load_teammates),
            asyncio.to_thread(load_partymates),
            asyncio.to_thread(get_match_cache().player_match_payloads, target.puuid),
        )
        names = {a.puuid: a.riot_id for registry in (partymates, teammates, tracked) for a in registry.values()}
        names[target.puuid] = target.riot_id
        if companion:
            names[companion.puuid] = companion.riot_id
        rows = await asyncio.to_thread(aggregate_synergies, matches, target.puuid, names,
                                       game_mode, breakdown, min_games,
                                       companion.puuid if companion else None, size)
        paginator = Paginator(rows, author_id=ctx.author.id, page_size=1,
            render_page=lambda items, page, pages: synergy_embed(
                items, names, target.riot_id, breakdown, min_games, page, pages))
        response = {"embed": paginator.render(), "allowed_mentions": discord.AllowedMentions.none()}
        if paginator.max_page:
            response["view"] = paginator
        paginator.message = await ctx.respond(**response)


def setup(bot):
    """Register the public synergy command."""
    bot.add_cog(SynergyCommands())

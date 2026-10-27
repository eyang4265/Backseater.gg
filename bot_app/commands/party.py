"""Public /party report for exact three-to-five-player known rosters."""

import asyncio

import discord
from discord.ext import commands

from .shared import GUILD_IDS, SERVERS, game_mode_choices, log_command, not_found_embed, target_for
from ..match_cache import get_match_cache
from ..party_records import aggregate_parties
from ..paginator import Paginator
from ..queues import queue_name
from ..render import make_embed
from ..store import load_accounts, load_partymates, load_teammates

ROLE_NAMES = {"TOP": "Top", "JUNGLE": "Jungle", "MIDDLE": "Mid", "BOTTOM": "ADC", "UTILITY": "Support"}


def party_embed(rows, names, player_name, page=0, pages=1):
    """Render concise group results with aligned, bounded player columns."""
    embed = make_embed(
        "", title=f"Party — {player_name}",
    )
    if not rows:
        embed.description += "\nNo completed games for these groups."
        return embed
    row = rows[0]
    lineup, count = row.lineups.most_common(1)[0]
    champions = dict(lineup)
    embed.description += (f"\n**{len(row.roster)} players · {queue_name(row.queue_id)}**\n"
                          f"{row.wins}W–{row.games-row.wins}L · {row.wins/row.games:.1%} WR · {row.games} games")
    embed.add_field(name="Player", value="\n".join(
        discord.utils.escape_markdown(names.get(p, "Unknown player")[:100]) for p in row.roster), inline=True)
    embed.add_field(name="Most-played role", value="\n".join(
        ROLE_NAMES.get(row.roles[p].most_common(1)[0][0], row.roles[p].most_common(1)[0][0])[:100]
        for p in row.roster), inline=True)
    embed.add_field(name=f"Most-used lineup ({count} games)", value="\n".join(
        discord.utils.escape_markdown(champions[p][:100]) for p in row.roster), inline=True)
    embed.set_footer(text=f"Group {page+1}/{pages}")
    return embed


class PartyCommands(commands.Cog):
    """Show cached records without an expensive history scan."""

    @discord.slash_command(guild_ids=GUILD_IDS, description="Records for your exact three-to-five-player groups")
    @discord.option("server", description="Player's server", choices=SERVERS, required=False)
    @discord.option("username", description="League or Discord username (defaults to you)", required=False)
    @discord.option("game_mode", description="Filter by game mode", autocomplete=discord.utils.basic_autocomplete(game_mode_choices), required=False)
    @discord.option("size", int, description="Exact number of known players", choices=[3, 4, 5], required=False)
    async def party(self, ctx, server=None, username=None, game_mode=None, size=None):
        """Acknowledge first, resolve the linked default, then query cached groups."""
        log_command(ctx, server=server, username=username, game_mode=game_mode, size=size)
        await ctx.defer()
        target = await target_for(ctx, server, username, include_icon=False)
        if target is None:
            await ctx.respond(embed=not_found_embed(username, server, ctx=ctx))
            return
        tracked, teammates, partymates, matches = await asyncio.gather(
            asyncio.to_thread(load_accounts), asyncio.to_thread(load_teammates),
            asyncio.to_thread(load_partymates),
            asyncio.to_thread(get_match_cache().player_match_payloads, target.puuid),
        )
        names = {a.puuid: a.riot_id for registry in (partymates, teammates, tracked) for a in registry.values()}
        names[target.puuid] = target.riot_id
        rows = await asyncio.to_thread(aggregate_parties, matches, target.puuid, names, game_mode, size)
        paginator = Paginator(rows, author_id=ctx.author.id, page_size=1,
                              render_page=lambda items, page, pages: party_embed(items, names, target.riot_id, page, pages))
        paginator.message = await ctx.respond(embed=paginator.render(),
            view=paginator if paginator.max_page else None, allowed_mentions=discord.AllowedMentions.none())


def setup(bot):
    """Register the public party command."""
    bot.add_cog(PartyCommands())

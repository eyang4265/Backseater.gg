"""The public ``/commands`` command directory."""

from __future__ import annotations

import discord
from discord.ext import commands

from ...render import make_embed
from ..shared import GUILD_IDS, log_command


_COMMANDS = (
    (
        "Help",
        "**`/commands`** — Show this list of public commands, descriptions, and usage.\n"
        "Usage: `/commands`.",
    ),
    (
        "Player commands",
        "**`/profile`** — Show level, server, ranks, and top champions.\n"
        "Usage: `/profile` or add `server`, `summoner`, and `tag`.\n\n"
        "**`/opgg`** — Get an OP.GG profile link.\n"
        "Usage: `/opgg` or add a player/server.\n\n"
        "**`/livegame`** — Show the current lobby, champions, ranks, and win rates.\n"
        "Usage: `/livegame` (uses your tracked account by default).\n\n"
        "**`/matchhistory`** — Show the 10 most recent games with results and stats.\n"
        "Usage: `/matchhistory` or add a player/server.\n\n"
        "**`/matchlist`** — Show recent match IDs.\n"
        "Usage: `/matchlist` or add a player/server.",
    ),
    (
        "Match and champion commands",
        "**`/recap`** — Show post-game performance metrics and takeaways.\n"
        "Usage: `/recap`; optionally provide `match_id` or a player.\n\n"
        "**`/timeline`** — Show timeline-derived kills, deaths, damage, and economy.\n"
        "Usage: `/timeline`; optionally provide `match_id` and `position`.\n\n"
        "**`/mastery`** — Show all champion mastery or details for one champion.\n"
        "Usage: `/mastery`; optionally provide `champion` or a player.",
    ),
    (
        "Riot information",
        "**`/rotation`** — Show this week's free champion rotation.\n\n"
        "**`/serverstatus`** — Show active maintenance and incidents.\n"
        "Usage: `/serverstatus`; optionally choose a `server`.",
    ),
)


class CommandDirectory(commands.Cog):
    """Show the slash commands available to everyone in the server."""

    def __init__(self, bot: discord.Bot) -> None:
        self.bot = bot

    @discord.slash_command(guild_ids=GUILD_IDS, description="List available bot commands")
    async def commands(self, ctx: discord.ApplicationContext) -> None:
        log_command(ctx)
        embed = make_embed(
            "Use the optional player fields to look up someone other than your tracked account.",
            title="Available Commands",
        )
        for category, command_list in _COMMANDS:
            embed.add_field(name=category, value=command_list, inline=False)
        await ctx.respond(embed=embed)


def setup(bot: discord.Bot) -> None:
    bot.add_cog(CommandDirectory(bot))

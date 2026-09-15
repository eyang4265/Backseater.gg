"""Owner-only command for assigning one Discord member a flake tier."""

from __future__ import annotations

import logging

import discord
from discord.ext import commands

from ..flake_ranks import FLAKE_TIERS, build_flake_rank_embed
from ..render import make_embed
from ..store import set_flake_rank
from .shared import GUILD_IDS, log_command

LOGGER = logging.getLogger(__name__)

class FlakeRankCommands(commands.Cog):
    """Assign Discord members to a server's flake tier list."""

    def __init__(self, bot: discord.Bot) -> None:
        """Initialize the command cog."""
        self.bot = bot

    @discord.slash_command(
        guild_ids=GUILD_IDS,
        description="Rank a Discord user on this server's flake list (owner only)",
    )
    @commands.is_owner()
    @discord.option(
        "user",
        discord.Member,
        description="Discord user to rank",
    )
    @discord.option(
        "category",
        str,
        description="Flake tier (S is most flaky; Unknown is unranked)",
        choices=list(FLAKE_TIERS),
    )
    async def flakerank(
        self,
        ctx: discord.ApplicationContext,
        user: discord.Member,
        category: str,
    ) -> None:
        """Assign one native Discord member and show the updated tier list."""
        log_command(ctx, user=user.id, category=category)
        if ctx.guild is None:
            await ctx.respond(
                embed=make_embed("This command must be used in a server."),
                ephemeral=True,
            )
            return
        rankings = set_flake_rank(ctx.guild.id, user.id, user.display_name, category)
        LOGGER.info(
            "/flakerank: assigned user %s to %s tier in guild %s",
            user.id,
            category,
            ctx.guild.id,
        )
        await ctx.respond(
            embed=build_flake_rank_embed(rankings, guild_name=ctx.guild.name),
            ephemeral=True,
        )


def setup(bot: discord.Bot) -> None:
    """Register the flake-ranking command module with the bot."""
    bot.add_cog(FlakeRankCommands(bot))

"""Public command for displaying a server's flake tier list."""

from __future__ import annotations

import discord
from discord.ext import commands

from ..flake_ranks import build_flake_rank_embed, ordered_flake_ranks
from ..paginator import Paginator
from ..render import make_embed
from ..store import load_flake_ranks
from .shared import GUILD_IDS, log_command


class FlakeCommands(commands.Cog):
    """Display the current server-specific flake rankings."""

    def __init__(self, bot: discord.Bot) -> None:
        """Initialize the command cog."""
        self.bot = bot

    @discord.slash_command(
        guild_ids=GUILD_IDS,
        description="Show this server's flake tier list",
    )
    async def flake(self, ctx: discord.ApplicationContext) -> None:
        """Show this server's saved flake tier list."""
        log_command(ctx)
        if ctx.guild is None:
            await ctx.respond(
                embed=make_embed("This command must be used in a server."),
                ephemeral=True,
            )
            return
        all_rankings = load_flake_ranks()
        ordered = ordered_flake_ranks(all_rankings.get(str(ctx.guild.id), {}))
        guild_name = ctx.guild.name

        def render_page(items, page, pages):
            """Render one Discord-safe slice of the server tier list."""
            return build_flake_rank_embed(
                dict(items), guild_name=guild_name, page=page, pages=pages
            )

        paginator = Paginator(
            ordered,
            author_id=ctx.author.id,
            render_page=render_page,
            page_size=40,
        )
        if paginator.max_page:
            paginator.message = await ctx.respond(
                embed=paginator.render(), view=paginator
            )
        else:
            paginator.message = await ctx.respond(embed=paginator.render())


def setup(bot: discord.Bot) -> None:
    """Register the flake tier-list display command."""
    bot.add_cog(FlakeCommands(bot))

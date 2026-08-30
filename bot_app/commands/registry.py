"""Public account registry commands."""

from __future__ import annotations

import asyncio
import logging

import discord
from discord.ext import commands

from ..paginator import Paginator
from ..render import make_embed
from ..store import Account, load_accounts
from .shared import GUILD_IDS, log_command

LOGGER = logging.getLogger(__name__)


def _accounts_embed(items, page: int, pages: int) -> discord.Embed:
    """Handle embed."""
    lines = [
        f"<@{account.discord_id}> — **{account.riot_id}** ({account.server})"
        for account in items
    ]
    embed = make_embed(
        "\n".join(lines) or "No accounts are currently tracked.",
        title="Tracked Accounts",
    )
    if pages > 1:
        embed.set_footer(text=f"Page {page + 1}/{pages}")
    return embed


class RegistryCommands(commands.Cog):
    def __init__(self, bot: discord.Bot) -> None:
        """Initialize the instance."""
        self.bot = bot

    @discord.slash_command(
        guild_ids=GUILD_IDS, description="List tracked Riot accounts"
    )
    @commands.is_owner()
    async def accounts(self, ctx):
        """Handle accounts."""
        log_command(ctx)
        await ctx.defer()
        loaded = await asyncio.to_thread(load_accounts)
        accounts: list[Account] = sorted(
            loaded.values(), key=lambda account: account.riot_id.casefold()
        )
        LOGGER.debug("/accounts listing %d tracked accounts", len(accounts))
        paginator = Paginator(
            accounts, author_id=ctx.author.id, render_page=_accounts_embed, page_size=10
        )
        view = paginator if paginator.max_page else None
        paginator.message = await ctx.respond(
            embed=paginator.render(),
            view=view,
            allowed_mentions=discord.AllowedMentions.none(),
        )


def setup(bot: discord.Bot) -> None:
    """Register this command module with the bot."""
    bot.add_cog(RegistryCommands(bot))

"""Public account registry commands."""

from __future__ import annotations

import asyncio

import discord
from discord.ext import commands

from ..account_registry import RegistryError, track_account
from ..config import get_settings
from ..paginator import Paginator
from ..render import make_embed
from ..routing import split_riot_id
from ..store import Account, load_accounts
from .shared import GUILD_IDS, SERVERS, log_command


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

    @discord.slash_command(guild_ids=GUILD_IDS, description="Track a Riot account")
    @discord.option("summoner", description="Game Name#Tag")
    @discord.option("server", description="Server", choices=SERVERS)
    @discord.option(
        "user", discord.User, description="User (owner only)", required=False
    )
    async def track(self, ctx, summoner, server, user=None):
        """Handle track."""
        log_command(ctx, summoner=summoner, server=server, user=user)
        name, tag = split_riot_id(summoner, None)
        if not tag:
            await ctx.respond(
                embed=make_embed("Give a Riot ID in the form `Name#Tag`."),
                ephemeral=True,
            )
            return
        target_user = user or ctx.author
        is_owner = ctx.author.id == get_settings().discord_owner_id
        if user is not None and user.id != ctx.author.id and not is_owner:
            await ctx.respond(
                embed=make_embed("Only the bot owner can link another user."),
                ephemeral=True,
            )
            return
        await ctx.defer(ephemeral=True)
        try:
            account = await asyncio.to_thread(
                track_account,
                target_user.id,
                name,
                tag,
                server,
                allow_reassign=is_owner,
                max_accounts=get_settings().max_tracked_accounts,
            )
        except RegistryError as error:
            await ctx.respond(embed=make_embed(str(error)), ephemeral=True)
            return
        await ctx.respond(
            embed=make_embed(
                f"Tracking **{account.riot_id}** on {account.server} for {target_user.mention}."
            ),
            ephemeral=True,
        )

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

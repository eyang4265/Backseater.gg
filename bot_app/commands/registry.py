"""Owner-only directory of tracked accounts, teammates, and inferred partymates."""

from __future__ import annotations

import asyncio
import logging

import discord
from discord.ext import commands

from ..paginator import Paginator
from ..render import make_embed
from ..store import Account, load_accounts, load_partymates, load_teammates
from .shared import GUILD_IDS, log_command

LOGGER = logging.getLogger(__name__)


def _is_unlinked(discord_id: str) -> bool:
    """True for a synthetic sentinel key (``000000000000000001`` style).

    Real Discord snowflakes never lead with a zero, so a zero-padded id is
    an account ``/add`` tracked with no ``user`` supplied.
    """
    return not discord_id or (discord_id.isdigit() and discord_id.startswith("0"))


def _account_line(account: Account) -> str:
    """One ⭐ tracked-account row, with a mention when linked."""
    who = "*(unlinked)*" if _is_unlinked(account.discord_id) else f"<@{account.discord_id}>"
    return f"⭐ {who} — **{account.riot_id}** ({account.server})"


def _teammate_line(account: Account) -> str:
    """One ⭐ teammate row, showing the tied Discord user when there is one."""
    tie = f" — <@{account.discord_id}>" if account.discord_id else ""
    return f"⭐ **{account.riot_id}** ({account.server}){tie}"


def _partymate_line(account: Account) -> str:
    """One inferred partymate row with Riot ID, server, and an optional user tie."""
    tie = f" — <@{account.discord_id}>" if account.discord_id else ""
    return f"👥 **{account.riot_id}** ({account.server}){tie}"


def _accounts_embed(items: list[str], page: int, pages: int) -> discord.Embed:
    """Render one page of tracked accounts, teammates, and inferred partymates."""
    embed = make_embed(
        "\n".join(items) or "No accounts are currently tracked.",
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
        guild_ids=GUILD_IDS, description="List tracked Riot accounts, teammates, and inferred partymates"
    )
    @commands.is_owner()
    async def accounts(self, ctx):
        """List all three registries in sorted, ephemeral pages after acknowledgement."""
        log_command(ctx)
        await ctx.defer(ephemeral=True)
        loaded, teammates, partymates = await asyncio.gather(
            asyncio.to_thread(load_accounts),
            asyncio.to_thread(load_teammates),
            asyncio.to_thread(load_partymates),
        )
        tracked = sorted(loaded.values(), key=lambda account: account.riot_id.casefold())
        mates = sorted(
            teammates.values(), key=lambda account: account.riot_id.casefold()
        )
        registered = {account.puuid for account in (*tracked, *mates)}
        party = sorted(
            (account for account in partymates.values() if account.puuid not in registered),
            key=lambda account: account.riot_id.casefold(),
        )
        LOGGER.debug(
            "/accounts listing %d tracked accounts, %d teammates, and %d partymates",
            len(tracked),
            len(mates),
            len(party),
        )
        lines = [_account_line(account) for account in tracked]
        if mates:
            lines.append(f"__Teammates ({len(mates)})__")
            lines.extend(_teammate_line(account) for account in mates)
        if party:
            lines.append(f"__Partymates ({len(party)}) — inferred__")
            lines.extend(_partymate_line(account) for account in party)
        paginator = Paginator(
            lines or [""],
            author_id=ctx.author.id,
            render_page=_accounts_embed,
            page_size=15,
        )
        view = paginator if paginator.max_page else None
        paginator.message = await ctx.respond(
            embed=paginator.render(),
            view=view,
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )


def setup(bot: discord.Bot) -> None:
    """Register this command module with the bot."""
    bot.add_cog(RegistryCommands(bot))

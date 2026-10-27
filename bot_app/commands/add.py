"""Owner-only /add registration with tracked, teammate, and partymate choices.

Tracked accounts register separate League/TFT identities. Teammates and
partymates save a League identity without polling, using ⭐ and 👥 markers.
With only a Discord user, switch their existing identity to the chosen type.
"""

from __future__ import annotations

import asyncio
import logging

import discord
from discord.ext import commands

from ..account_registry import RegistryError, track_account, untrack_account
from ..config import get_settings
from ..render import make_embed
from ..riot import RiotAPIError, get_client
from ..routing import DEFAULT_PLATFORM, lookup_platforms, split_riot_id
from ..store import (
    Account, league_state_transaction, load_accounts, load_partymates,
    load_teammates, load_tft_accounts, remember_partymates, save_partymates,
    update_teammates,
)
from ..tft_account_registry import track_tft_account, untrack_tft_account
from .shared import GUILD_IDS, log_command

LOGGER = logging.getLogger(__name__)


class _PartialRegistrationError(RegistryError):
    """League registration succeeded but TFT registration did not."""

    def __init__(self, league_account, error: RegistryError) -> None:
        self.league_account = league_account
        super().__init__(str(error))


def _track_both_accounts(discord_id, name, tag, max_accounts):
    """Register distinct League and TFT PUUIDs under one key.

    ``discord_id`` may be ``None``; ``track_account`` then settles a synthetic
    sentinel key, and the TFT half is linked under that same key so the two
    identities stay paired.
    """
    league_account = track_account(
        discord_id,
        name,
        tag,
        DEFAULT_PLATFORM,
        allow_reassign=True,
        max_accounts=max_accounts,
    )
    update_teammates(lambda teammates: teammates.pop(league_account.puuid, None))
    try:
        tft_account = track_tft_account(
            league_account.discord_id,
            name,
            tag,
            DEFAULT_PLATFORM,
            allow_reassign=True,
            max_accounts=max_accounts,
        )
    except RegistryError as error:
        raise _PartialRegistrationError(league_account, error) from error
    return league_account, tft_account


def _register_companion(discord_id: str, name: str, tag: str, account_type: str) -> Account:
    """Resolve and save a teammate or partymate without fetching matches/ranks."""
    client = get_client()
    candidates = lookup_platforms(DEFAULT_PLATFORM)
    puuid = None
    for candidate in candidates:
        puuid = client.puuid(name, tag, candidate)
        if puuid:
            break
    if not puuid:
        searched = ", ".join(candidates[:-1]) + f", or {candidates[-1]}"
        raise RegistryError(f"{name}#{tag} could not be found on {searched}.")
    home = client.home_platform(puuid, candidates) or candidates[0]
    riot_id = client.riot_id(puuid, home) or f"{name}#{tag}"
    account = Account(discord_id=discord_id, puuid=puuid, server=home, riot_id=riot_id)
    return _store_companion(account, account_type)


def _store_companion(account: Account, account_type: str) -> Account:
    """Move one League identity between companion registries, excluding tracking."""
    with league_state_transaction():
        tracked = (*load_accounts().values(), *load_tft_accounts().values())
        if any(item.puuid == account.puuid for item in tracked):
            raise RegistryError(
                "That account is tracked. Use `/add user:<user> "
                f"account_type:{account_type}` without a summoner to switch it."
            )
        existing = load_teammates().get(account.puuid) or load_partymates().get(account.puuid)
        if existing and not account.discord_id:
            account = Account(existing.discord_id, account.puuid, account.server, account.riot_id)
        if account_type == "teammate":
            update_teammates(lambda entries: entries.__setitem__(account.puuid, account))
        elif account_type == "partymate":
            update_teammates(lambda entries: entries.pop(account.puuid, None))
            remember_partymates([account])
        else:
            raise RegistryError("Choose teammate or partymate.")
        return account


def _remove_teammates_for(discord_id: str) -> None:
    """Drop every teammate entry tied to ``discord_id``."""
    def mutate(teammates: dict[str, Account]) -> None:
        for puuid, account in list(teammates.items()):
            if account.discord_id == discord_id:
                teammates.pop(puuid)

    update_teammates(mutate)


def _switch_to_companion(discord_id: str, account_type: str) -> Account:
    """Switch a linked tracked account, teammate, or partymate to a companion."""
    with league_state_transaction():
        account = load_accounts().get(discord_id)
        if account is not None:
            untrack_account(discord_id)
            untrack_tft_account(discord_id)
        else:
            account = next(
                (item for item in (*load_teammates().values(), *load_partymates().values())
                 if item.discord_id == discord_id), None,
            )
        if account is None:
            raise RegistryError(f"<@{discord_id}> has no saved account to switch over.")
        return _store_companion(account, account_type)


def _promote_from_companion(discord_id: str, max_accounts: int) -> tuple[Account, Account]:
    """Re-track a user's teammate or partymate as a full League/TFT account.

    Resolution and seeding go back through :func:`_track_both_accounts`, then
    the companion entry is dropped so the PUUID lives in exactly one registry.
    """
    entry = next(
        (
            account
            for account in (*load_teammates().values(), *load_partymates().values())
            if account.discord_id == discord_id
        ),
        None,
    )
    if entry is None:
        raise RegistryError(f"<@{discord_id}> has no teammate or partymate to switch over.")
    name, tag = split_riot_id(entry.riot_id, None)
    if not tag:
        raise RegistryError(
            f"The stored Riot id {entry.riot_id!r} is not in the form Name#Tag."
        )
    try:
        result = _track_both_accounts(int(discord_id), name, tag, max_accounts)
    except _PartialRegistrationError as error:
        _remove_teammates_for(discord_id)
        raise RegistryError(
            f"League account **{error.league_account.riot_id}** was tracked, but TFT "
            f"failed: {error}. Use `/tftadd` to retry the TFT identity."
        ) from error
    with league_state_transaction():
        _remove_teammates_for(discord_id)
        save_partymates({p: a for p, a in load_partymates().items() if a.discord_id != discord_id})
    return result


class AddCommand(commands.Cog):
    def __init__(self, bot: discord.Bot) -> None:
        """Initialize the instance."""
        self.bot = bot

    @discord.slash_command(
        guild_ids=GUILD_IDS,
        description="Add a tracked account, teammate, or partymate (owner only)",
    )
    @discord.option(
        "summoner",
        description="Riot ID (Name#Tag) — omit with a user to switch account type",
        default=None,
    )
    @discord.option(
        "user",
        discord.User,
        description="Discord user to link (optional — omit to track/add tied to nobody)",
        default=None,
    )
    @discord.option(
        "account_type",
        str,
        description="Tracked polls games; teammate and partymate only mark shared lobbies",
        choices=["tracked", "teammate", "partymate"],
        default="tracked",
    )
    @commands.is_owner()
    async def add(self, ctx, summoner, user, account_type):
        """Register or switch an account and report the result without behavior explanations."""
        log_command(ctx, summoner=summoner, user=user, account_type=account_type)
        if not summoner:
            if user is None:
                await ctx.respond(
                    embed=make_embed(
                        "Provide a Riot ID or Discord user."
                    ),
                    ephemeral=True,
                )
                return
            await ctx.defer(ephemeral=True)
            try:
                if account_type != "tracked":
                    mate = await asyncio.to_thread(_switch_to_companion, str(user.id), account_type)
                    body = (
                        f"Switched {user.mention}'s account "
                        f"**{mate.riot_id}** to a {account_type}."
                    )
                else:
                    league, tft = await asyncio.to_thread(
                        _promote_from_companion,
                        str(user.id),
                        get_settings().max_tracked_accounts,
                    )
                    body = (
                        f"Switched {user.mention}'s saved account back to a tracked "
                        f"account:\n**League:** {league.riot_id} on {league.server}\n"
                        f"**TFT:** {tft.riot_id} on {tft.server}"
                    )
            except (RegistryError, RiotAPIError, OSError) as error:
                LOGGER.info("/add switch failed for %s: %s", user.id, error)
                await ctx.respond(embed=make_embed(str(error)), ephemeral=True)
                return
            await ctx.respond(
                embed=make_embed(body),
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return
        name, tag = split_riot_id(summoner, None)
        if not name:
            await ctx.respond(
                embed=make_embed("Give a Riot ID in the form `Name#Tag`."),
                ephemeral=True,
            )
            return
        if not tag:
            tag = DEFAULT_PLATFORM
            LOGGER.debug("/add %r has no tagline; defaulting to #%s", summoner, tag)
        await ctx.defer(ephemeral=True)
        if account_type != "tracked":
            try:
                account = await asyncio.to_thread(
                    _register_companion,
                    str(user.id) if user is not None else "",
                    name,
                    tag,
                    account_type,
                )
            except (RegistryError, RiotAPIError, OSError) as error:
                LOGGER.info("/add %s failed for %s#%s: %s", account_type, name, tag, error)
                await ctx.respond(embed=make_embed(str(error)), ephemeral=True)
                return
            tie = f" for {user.mention}" if user is not None else ""
            await ctx.respond(
                embed=make_embed(
                    f"Added {account_type} **{account.riot_id}** on {account.server}{tie}."
                ),
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return
        try:
            league_account, tft_account = await asyncio.to_thread(
                _track_both_accounts,
                user.id if user is not None else None,
                name,
                tag,
                get_settings().max_tracked_accounts,
            )
        except _PartialRegistrationError as error:
            LOGGER.info("/add linked League but TFT failed for %s#%s: %s", name, tag, error)
            await ctx.respond(
                embed=make_embed(
                    f"League account **{error.league_account.riot_id}** was linked, "
                    f"but TFT failed: {error}. Use `/tftadd` to retry the TFT identity."
                ),
                ephemeral=True,
            )
            return
        except RegistryError as error:
            LOGGER.info("/add failed for %s#%s: %s", name, tag, error)
            await ctx.respond(embed=make_embed(str(error)), ephemeral=True)
            return
        LOGGER.info(
            "Tracking League %s and TFT %s for %s",
            league_account.riot_id,
            tft_account.riot_id,
            f"Discord user {user.id}" if user is not None else "no Discord user",
        )
        heading = f"Tracking for {user.mention}:" if user is not None else "Tracking:"
        await ctx.respond(
            embed=make_embed(
                f"{heading}\n"
                f"**League:** {league_account.riot_id} on {league_account.server}\n"
                f"**TFT:** {tft_account.riot_id} on {tft_account.server}"
            ),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )


def setup(bot: discord.Bot) -> None:
    """Register this command module with the bot."""
    bot.add_cog(AddCommand(bot))

"""Owner-only /add command: quick account registration defaulting to NA1.

With ``teammate: True`` the same command instead registers a *teammate* — a
bare PUUID that the pollers never fetch and that never triggers an
announcement of its own.  A teammate exists only so that a completed-match
announcement or ``/match`` for a real ``data.json`` account also bolds a
teammate who happened to share that lobby.

Called with a ``user`` but no ``summoner``, ``/add`` switches that user's
existing record between the two registries: ``teammate: True`` demotes their
tracked League/TFT account to a teammate, ``teammate: False`` re-tracks their
teammate PUUID as a full account.  The PUUID only ever lives in one registry.
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
from ..store import Account, load_accounts, load_teammates, update_teammates
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


def _register_teammate(discord_id: str, name: str, tag: str) -> Account:
    """Resolve a Riot ID to a stored teammate without seeding anything.

    Sweeps NA/EUW/KR and settles the hosting platform with ``home_platform``
    exactly as ``track_account`` does, but fetches no matches or ranks — a
    teammate is never polled.
    """
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
    update_teammates(lambda teammates: teammates.__setitem__(puuid, account))
    LOGGER.info("Registered teammate %s on %s for discord_id=%s", riot_id, home, discord_id)
    return account


def _remove_teammates_for(discord_id: str) -> None:
    """Drop every teammate entry tied to ``discord_id``."""
    def mutate(teammates: dict[str, Account]) -> None:
        for puuid, account in list(teammates.items()):
            if account.discord_id == discord_id:
                teammates.pop(puuid)

    update_teammates(mutate)


def _demote_to_teammate(discord_id: str) -> Account:
    """Move a user's tracked League/TFT account into the teammate registry.

    The PUUID keeps its Riot id, server, and Discord tie but stops being
    polled — it is only bolded when it shares a tracked lobby.
    """
    account = load_accounts().get(discord_id)
    if account is None:
        raise RegistryError(f"<@{discord_id}> has no tracked account to switch over.")
    teammate = Account(discord_id, account.puuid, account.server, account.riot_id)
    update_teammates(lambda teammates: teammates.__setitem__(account.puuid, teammate))
    untrack_account(discord_id)
    untrack_tft_account(discord_id)
    LOGGER.info("Switched tracked account %s to a teammate for %s", account.riot_id, discord_id)
    return teammate


def _promote_from_teammate(discord_id: str, max_accounts: int) -> tuple[Account, Account]:
    """Re-track a user's teammate PUUID as a full League/TFT account.

    Resolution and seeding go back through :func:`_track_both_accounts`, then
    the teammate entry is dropped so the PUUID lives in exactly one registry.
    """
    entry = next(
        (
            account
            for account in load_teammates().values()
            if account.discord_id == discord_id
        ),
        None,
    )
    if entry is None:
        raise RegistryError(f"<@{discord_id}> has no teammate to switch over.")
    name, tag = split_riot_id(entry.riot_id, None)
    if not tag:
        raise RegistryError(
            f"The stored teammate id {entry.riot_id!r} is not in the form Name#Tag."
        )
    try:
        result = _track_both_accounts(int(discord_id), name, tag, max_accounts)
    except _PartialRegistrationError as error:
        _remove_teammates_for(discord_id)
        raise RegistryError(
            f"League account **{error.league_account.riot_id}** was tracked, but TFT "
            f"failed: {error}. Use `/tftadd` to retry the TFT identity."
        ) from error
    _remove_teammates_for(discord_id)
    return result


class AddCommand(commands.Cog):
    def __init__(self, bot: discord.Bot) -> None:
        """Initialize the instance."""
        self.bot = bot

    @discord.slash_command(
        guild_ids=GUILD_IDS,
        description="Track separate League and TFT accounts (defaults to NA1)",
    )
    @discord.option(
        "summoner",
        description="Riot ID (Name#Tag) — omit with a user to switch tracked <-> teammate",
        default=None,
    )
    @discord.option(
        "user",
        discord.User,
        description="Discord user to link (optional — omit to track/add tied to nobody)",
        default=None,
    )
    @discord.option(
        "teammate",
        bool,
        description="Register a bolded-only teammate PUUID instead of tracking",
        default=False,
    )
    @commands.is_owner()
    async def add(self, ctx, summoner, user, teammate):
        """Handle add."""
        log_command(ctx, summoner=summoner, user=user, teammate=teammate)
        if not summoner:
            if user is None:
                await ctx.respond(
                    embed=make_embed(
                        "Give a Riot ID, or a `user` to switch between a tracked "
                        "account and a teammate."
                    ),
                    ephemeral=True,
                )
                return
            await ctx.defer(ephemeral=True)
            try:
                if teammate:
                    mate = await asyncio.to_thread(_demote_to_teammate, str(user.id))
                    body = (
                        f"Switched {user.mention}'s tracked account "
                        f"**{mate.riot_id}** to a teammate — no longer polled, only "
                        "bolded when it shares a tracked lobby."
                    )
                else:
                    league, tft = await asyncio.to_thread(
                        _promote_from_teammate,
                        str(user.id),
                        get_settings().max_tracked_accounts,
                    )
                    body = (
                        f"Switched {user.mention}'s teammate back to a tracked "
                        f"account:\n**League:** {league.riot_id} on {league.server}\n"
                        f"**TFT:** {tft.riot_id} on {tft.server}"
                    )
            except (RegistryError, RiotAPIError) as error:
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
        if teammate:
            try:
                account = await asyncio.to_thread(
                    _register_teammate,
                    str(user.id) if user is not None else "",
                    name,
                    tag,
                )
            except (RegistryError, RiotAPIError) as error:
                LOGGER.info("/add teammate failed for %s#%s: %s", name, tag, error)
                await ctx.respond(embed=make_embed(str(error)), ephemeral=True)
                return
            tie = f" for {user.mention}" if user is not None else ""
            await ctx.respond(
                embed=make_embed(
                    f"Teammate **{account.riot_id}** on {account.server}{tie} will be "
                    "bolded in match announcements and `/match` when they share a "
                    "tracked lobby."
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

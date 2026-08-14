"""Shared plumbing for slash-command handlers."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import discord

from ..config import get_settings
from ..render import make_embed, profile_author_icon
from ..services.riot_api import get_client
from ..routing import DEFAULT_PLATFORM, SERVERS, opgg_url, split_riot_id
from ..store import load_accounts

LOGGER = logging.getLogger(__name__)

GUILD_IDS = list(get_settings().guild_ids)


def log_command(ctx: Any, **fields: Any) -> None:
    """Record who ran what, with the options they passed."""
    supplied = " | ".join(f"{key}={value}" for key, value in fields.items() if value is not None)
    LOGGER.info(
        "/%s by %s in %s%s", ctx.command, ctx.author, ctx.channel, f" | {supplied}" if supplied else ""
    )


@dataclass(frozen=True)
class Target:
    """The player a command should act on."""

    puuid: str
    server: str

    @property
    def riot_id(self) -> str:
        return get_client().riot_id(self.puuid, self.server) or "Unknown player"

    @property
    def opgg_url(self) -> str | None:
        return opgg_url(self.server, self.riot_id)


def _puuid_for_mention(mention: str | None) -> str | None:
    """Resolve a ``user`` option to a tracked puuid.

    The option is free text, so it can arrive as a raw id or as a
    ``<@id>`` / ``<@!id>`` mention.
    """
    if not mention:
        return None
    identifier = str(mention).strip().lstrip("<@!").rstrip(">")
    account = load_accounts().get(identifier)
    return account.puuid if account else None


def resolve_target(
    ctx: Any,
    server: str | None,
    summoner: str | None,
    tag: str | None,
    user: str | None = None,
) -> Target | None:
    """Work out which account a command refers to.

    With no lookup options given at all, this falls back to the mentioned
    user's tracked account, or the caller's own. Otherwise the riot id is
    resolved directly. A tracked account's stored server always wins over the
    supplied one, since that's the server the account actually lives on.
    """
    summoner, tag = split_riot_id(summoner, tag)

    # Discord callers sometimes put a platform code in the tag field. Treat
    # that as the server selector and use the platform's default tagline.
    if server is None and tag and tag.upper() in SERVERS:
        server, tag = tag.upper(), None

    if summoner is None:
        accounts = load_accounts()
        puuid = _puuid_for_mention(user)
        if puuid is None:
            account = accounts.get(str(ctx.author.id))
            puuid = account.puuid if account else None
        if puuid is None:
            return None
        stored = next((a.server for a in accounts.values() if a.puuid == puuid), None)
        return Target(puuid=puuid, server=stored or server or DEFAULT_PLATFORM)

    lookup_server = server or DEFAULT_PLATFORM
    # A missing tag most often means the platform's default tagline. When no
    # server was selected, try the common alternatives before giving up.
    lookup_servers = [lookup_server] if server else [DEFAULT_PLATFORM, "EUW1", "KR"]
    puuid = None
    matched_server = lookup_server
    for candidate in lookup_servers:
        puuid = get_client().puuid(summoner or "", tag or candidate, candidate)
        if puuid is not None:
            matched_server = candidate
            break
    if puuid is None:
        return None

    stored = next((a.server for a in load_accounts().values() if a.puuid == puuid), None)
    # A tracked account's stored server takes priority over a command option.
    return Target(puuid=puuid, server=stored or matched_server)


def not_found_embed(summoner: str | None, tag: str | None, server: str | None) -> discord.Embed:
    who = f"{summoner}#{tag}" if summoner else "That account"
    if server is None and tag and tag.upper() in SERVERS:
        server, tag = tag.upper(), None
    servers = server or f"{DEFAULT_PLATFORM}, EUW1, and KR"
    return make_embed(f"{who} could not be found on {servers}.")


def set_player_author(embed: discord.Embed, target: Target, *, name: str | None = None) -> None:
    """Put the player's riot id, profile icon, and op.gg link in the embed header."""
    riot_id = target.riot_id
    embed.set_author(
        name=name or riot_id,
        icon_url=profile_author_icon(target.puuid, target.server),
        url=opgg_url(target.server, riot_id),
    )

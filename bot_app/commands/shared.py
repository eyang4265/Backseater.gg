"""Shared plumbing for slash-command handlers."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass
from typing import Any

import discord

from ..config import ConfigError, get_settings
from ..render import make_embed, profile_author_icon
from ..services.riot_api import get_client
from ..routing import DEFAULT_PLATFORM, SERVERS, opgg_url, split_riot_id
from ..store import load_accounts, remember_embed_button_state

LOGGER = logging.getLogger(__name__)

try:
    GUILD_IDS = list(get_settings().guild_ids) or None
except ConfigError:
    GUILD_IDS = None


def log_command(ctx: Any, **fields: Any) -> None:
    """Record a slash-command invocation once with consistent context."""
    if getattr(ctx, "_debug_command_logged", False):
        return
    supplied = " | ".join(
        f"{key}={value}" for key, value in fields.items() if value is not None
    )
    command = getattr(ctx, "command", "unknown")
    author = getattr(ctx, "author", "unknown")
    channel = getattr(ctx, "channel", "unknown")
    LOGGER.info(
        "/%s by %s in %s%s",
        command,
        author,
        channel,
        f" | {supplied}" if supplied else "",
    )
    setattr(ctx, "_debug_command_logged", True)
    setattr(ctx, "_debug_command_started_at", time.perf_counter())


def command_option_fields(ctx: Any) -> dict[str, Any]:
    """Extract supplied slash-command option values for the global logger."""
    selected = getattr(ctx, "selected_options", None) or ()
    return {
        option["name"]: option["value"]
        for option in selected
        if isinstance(option, dict) and "name" in option and "value" in option
    }


def log_command_completion(ctx: Any) -> None:
    """Write a DEBUG completion line for every successfully parsed command."""
    started = getattr(ctx, "_debug_command_started_at", None)
    elapsed_ms = (time.perf_counter() - started) * 1000 if started else 0
    LOGGER.debug("/%s completed in %.0fms", getattr(ctx, "command", "unknown"), elapsed_ms)


def log_command_error(ctx: Any, error: Exception) -> None:
    """Write an exception with the same invocation context as normal commands."""
    LOGGER.error(
        "/%s by %s in %s failed: %s",
        getattr(ctx, "command", "unknown"),
        getattr(ctx, "author", "unknown"),
        getattr(ctx, "channel", "unknown"),
        error,
        exc_info=(type(error), error, error.__traceback__),
    )


async def remember_view_state(message: Any, kind: str, payload: dict[str, Any]) -> None:
    """Persist one message's component-view state, if the message id is usable.

    Discord's REST-fallback responses can hand back a message with a
    non-integer/placeholder id; those aren't safe to key persistence on.
    """
    if message is not None and isinstance(message.id, int) and isinstance(message.channel.id, int):
        await asyncio.to_thread(
            remember_embed_button_state, message.id, message.channel.id, kind, payload
        )


def match_reference_index(reference: str | None) -> int | None:
    """Return the zero-based recent-match index for numeric references."""
    if reference is None or not reference.isdigit():
        return None
    number = int(reference)
    return number - 1 if 1 <= number <= 20 else None


@dataclass(frozen=True)
class Target:
    """The player a command should act on."""

    puuid: str
    server: str
    riot_id: str = "Unknown player"
    icon_url: str | None = None

    @property
    def opgg_url(self) -> str | None:
        """Handle url."""
        return opgg_url(self.server, self.riot_id)


def _discord_user_id(user: Any) -> str | None:
    """Normalize a typed user, raw numeric id, or exact Discord mention."""
    if user is None:
        return None
    identifier = getattr(user, "id", None)
    if identifier is not None:
        return str(identifier)
    text = str(user).strip()
    if text.isdigit():
        return text
    match = re.fullmatch(r"<@!?(\d+)>", text)
    return match.group(1) if match else None


def resolve_target(
    ctx: Any,
    server: str | None,
    summoner: str | None,
    user: str | None = None,
    *,
    include_icon: bool = True,
) -> Target | None:
    """Work out which account a command refers to.

    With no lookup options given at all, this falls back to the mentioned
    user's tracked account, or the caller's own. Otherwise the riot id is
    resolved directly, with a ``Name#Tag`` combined summoner splitting into
    its own tagline. A tracked account's stored server always wins over the
    supplied one, since that's the server the account actually lives on.
    """
    summoner, tag = split_riot_id(summoner, None)

    if server is None and tag and tag.upper() in SERVERS:
        server, tag = tag.upper(), None

    if summoner is None:
        accounts = load_accounts()
        if user:
            identifier = _discord_user_id(user)
        else:
            identifier = str(ctx.author.id)
        account = accounts.get(identifier) if identifier else None
        if account is None:
            return None
        return Target(
            puuid=account.puuid,
            server=account.server,
            riot_id=account.riot_id,
            icon_url=(
                profile_author_icon(account.puuid, account.server)
                if include_icon
                else None
            ),
        )

    lookup_server = server or DEFAULT_PLATFORM

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

    stored = next(
        (a.server for a in load_accounts().values() if a.puuid == puuid), None
    )

    return _resolved_target(puuid, stored or matched_server, include_icon=include_icon)


def _resolved_target(puuid: str, server: str, *, include_icon: bool = True) -> Target:
    """Handle target."""
    client = get_client()
    riot_id = client.riot_id(puuid, server) or "Unknown player"
    return Target(
        puuid=puuid,
        server=server,
        riot_id=riot_id,
        icon_url=profile_author_icon(puuid, server) if include_icon else None,
    )


async def target_for(
    ctx: Any,
    server: str | None,
    summoner: str | None,
    user: str | None = None,
    *,
    include_icon: bool = True,
) -> Target | None:
    """Resolve command options without blocking Discord's event loop."""
    return await asyncio.to_thread(
        resolve_target,
        ctx,
        server,
        summoner,
        user,
        include_icon=include_icon,
    )


def not_found_embed(
    summoner: str | None,
    server: str | None,
    *,
    user: Any = None,
) -> discord.Embed:
    """Handle found embed."""
    if summoner is None and user is not None:
        return make_embed("That Discord user has no tracked Riot account.")
    name, tag = split_riot_id(summoner, None)
    if server is None and tag and tag.upper() in SERVERS:
        server, tag = tag.upper(), None
    who = f"{name}#{tag}" if name and tag else (name or "That account")
    servers = server or f"{DEFAULT_PLATFORM}, EUW1, and KR"
    return make_embed(f"{who} could not be found on {servers}.")


def set_player_author(
    embed: discord.Embed, target: Target, *, name: str | None = None
) -> None:
    """Put the player's riot id, profile icon, and op.gg link in the embed header."""
    riot_id = target.riot_id
    embed.set_author(
        name=name or riot_id,
        icon_url=target.icon_url,
        url=opgg_url(target.server, riot_id),
    )

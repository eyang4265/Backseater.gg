"""Shared plumbing for slash-command handlers."""

from __future__ import annotations

import asyncio
import functools
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

import discord

from ..config import ConfigError, get_settings
from ..queues import current_queue_names
from ..render import make_embed, profile_author_icon
from ..services.riot_api import get_client
from ..routing import (
    SERVERS,
    lookup_platforms,
    opgg_url,
    split_riot_id,
)
from ..store import load_accounts, remember_embed_button_state

LOGGER = logging.getLogger(__name__)
_RIOT_COMMAND_EXECUTOR = ThreadPoolExecutor(
    max_workers=8, thread_name_prefix="riot-command"
)

try:
    GUILD_IDS = list(get_settings().guild_ids) or None
except ConfigError:
    GUILD_IDS = None


def game_mode_choices(_ctx: discord.AutocompleteContext) -> tuple[str, ...]:
    """Game mode suggestions, refreshed at bot startup to drop retired modes."""
    return current_queue_names()


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


_USER_OPTION_TYPE = 6


def supplied_options(ctx: Any) -> tuple[tuple[str, str], ...]:
    """Every option the caller actually filled in, rendered for display.

    User-typed options arrive as a raw snowflake, so they are rendered as a
    mention; an embed shows the name without notifying anyone.
    """
    selected = getattr(ctx, "selected_options", None) or ()
    rendered: list[tuple[str, str]] = []
    for option in selected:
        if not isinstance(option, dict) or "name" not in option:
            continue
        value = option.get("value")
        if value is None or value == "":
            continue
        if option.get("type") == _USER_OPTION_TYPE and str(value).isdigit():
            rendered.append((str(option["name"]), f"<@{value}>"))
        else:
            rendered.append((str(option["name"]), f"`{value}`"))
    return tuple(rendered)


def supplied_options_text(ctx: Any) -> str:
    """The "you entered" trailer echoing a failed lookup's own inputs back.

    A lookup that fails is exactly when the caller needs to see what the bot
    thought they asked for — a typo'd tag, a stale ``user``, or an option they
    forgot they left set from the previous invocation.
    """
    if ctx is None:
        return ""
    options = supplied_options(ctx)
    if not options:
        return "\n\n**You entered:** nothing — defaulted to your own linked account."
    listed = " · ".join(f"{name}: {value}" for name, value in options)
    return f"\n\n**You entered:** {listed}"


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


def _linked_target(identifier: str | None, *, include_icon: bool = True) -> Target | None:
    """Resolve a stored account by Discord user id, if one is tracked."""
    accounts = load_accounts()
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


def _summoner_target(
    server: str | None,
    summoner: str | None,
    tag: str | None,
    *,
    include_icon: bool = True,
) -> Target | None:
    """Resolve a riot id directly, sweeping every platform for its owner."""
    lookup_servers = lookup_platforms(server)
    client = get_client()
    puuid = None
    for candidate in lookup_servers:
        puuid = client.puuid(summoner or "", tag or candidate, candidate)
        if puuid is not None:
            LOGGER.debug("_summoner_target: found puuid for %s#%s via %s", summoner, tag or candidate, candidate)
            break
    if puuid is None:
        LOGGER.debug("_summoner_target: no puuid found for %s across %s", summoner, lookup_servers)
        return None

    matched_server = client.home_platform(puuid, lookup_servers) or lookup_servers[0]

    stored = next(
        (a.server for a in load_accounts().values() if a.puuid == puuid), None
    )
    if stored:
        LOGGER.debug("_summoner_target: using stored server %s over probed %s", stored, matched_server)

    return _resolved_target(puuid, stored or matched_server, include_icon=include_icon)


def is_discord_mention(username: str | None) -> bool:
    """Whether a raw option value is a Discord mention (``<@id>``/``<@!id>``)."""
    return bool(username) and username.strip().startswith("<@") and username.strip().endswith(">")


def resolve_username(
    ctx: Any,
    server: str | None,
    username: str | None,
    *,
    include_icon: bool = True,
) -> Target | None:
    """Work out which account a single combined ``username`` option refers to.

    With nothing supplied, this falls back to the caller's own linked
    account. A Discord mention (Discord renders a typed ``@name`` inside a
    string option as ``<@id>``) is looked up directly against tracked
    accounts. Anything else is treated as a riot id, with a ``Name#Tag``
    combined summoner splitting into its own tagline. A supplied server only
    decides which platform is tried first: NA, EUW, and KR are always swept
    afterwards, so a mis-specified server still finds the account. Which
    platform the account actually lives on is then settled by a summoner-v4
    probe, because the riot-id lookup is regionally routed but answers for
    every region and so would otherwise always report the first platform
    tried. A tracked account's stored server still wins over all of that.
    """
    if username is None:
        LOGGER.debug("resolve_username: no username supplied, using caller's own linked account")
        return _linked_target(str(ctx.author.id), include_icon=include_icon)

    if is_discord_mention(username):
        LOGGER.debug("resolve_username: %s is a Discord mention, looking up tracked account", username)
        return _linked_target(_discord_user_id(username), include_icon=include_icon)

    name, tag = split_riot_id(username, None)
    if server is None and tag and tag.upper() in SERVERS:
        LOGGER.debug("resolve_username: treating tag %s as a server, not a riot tagline", tag)
        server, tag = tag.upper(), None
    LOGGER.debug("resolve_username: resolving riot id name=%s tag=%s server=%s", name, tag, server)
    return _summoner_target(server, name, tag, include_icon=include_icon)


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
    username: str | None,
    *,
    include_icon: bool = True,
) -> Target | None:
    """Resolve a command's ``username`` option without blocking the event loop."""
    return await riot_to_thread(
        resolve_username,
        ctx,
        server,
        username,
        include_icon=include_icon,
    )


async def riot_to_thread(function, /, *args, **kwargs):
    """Run blocking command-side Riot work on its own bounded executor.

    Keeping rate-limiter sleepers out of asyncio's default executor prevents a
    burst of history/counter requests from delaying chart and persistence work.
    """
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        _RIOT_COMMAND_EXECUTOR, functools.partial(function, *args, **kwargs)
    )


def searched_servers(server: str | None) -> str:
    """Name the platforms a lookup swept, for user-facing "not found" messages."""
    searched = lookup_platforms(server)
    return ", ".join(searched[:-1]) + f", or {searched[-1]}"


def not_found_embed(
    username: str | None,
    server: str | None,
    *,
    ctx: Any = None,
) -> discord.Embed:
    """The shared "no such player" reply, echoing the caller's own options back.

    Pass ``ctx`` so the embed can list every option that was actually
    supplied; without it the message names only the platforms searched.
    """
    if username is None or is_discord_mention(username):
        LOGGER.info("Lookup failed: Discord user %s has no tracked Riot account", username)
        return make_embed(
            "That Discord user has no tracked Riot account."
            + supplied_options_text(ctx)
        )
    name, tag = split_riot_id(username, None)
    if server is None and tag and tag.upper() in SERVERS:
        server, tag = tag.upper(), None
    who = f"{name}#{tag}" if name and tag else (name or "That account")
    LOGGER.info("Lookup failed: %s not found on %s", who, searched_servers(server))
    return make_embed(
        f"{who} could not be found on {searched_servers(server)}."
        + supplied_options_text(ctx)
    )


def set_player_author(
    embed: discord.Embed, target: Target, *, name: str | None = None
) -> None:
    """Put the player's identity, icon, and OP.GG link in the embed."""
    riot_id = target.riot_id
    embed.set_author(
        name=name or riot_id,
        icon_url=target.icon_url,
        url=opgg_url(target.server, riot_id),
    )

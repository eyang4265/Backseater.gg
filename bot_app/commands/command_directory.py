"""Shared registered-command discovery for the League and TFT directories."""

from __future__ import annotations

from collections.abc import Iterable

import discord

from ..render import make_embed


def is_owner_only(command: discord.ApplicationCommand) -> bool:
    """Return whether a command carries the owner-only check."""
    return any(
        getattr(check, "__qualname__", "").startswith("is_owner")
        for check in getattr(command, "checks", [])
    )


def public_commands(
    bot: discord.Bot, *, tft: bool
) -> tuple[discord.ApplicationCommand, ...]:
    """Read one game's public commands directly from the registered tree.

    ``bot.application_commands`` yields a separate object per guild when a
    command is registered with more than one ``guild_ids`` entry, so collapse
    by qualified name to keep each command listed exactly once.
    """
    unique: dict[str, discord.ApplicationCommand] = {}
    for command in bot.application_commands:
        if is_owner_only(command):
            continue
        if command.qualified_name.startswith("tft") is not tft:
            continue
        unique.setdefault(command.qualified_name, command)
    return tuple(
        command for _, command in sorted(unique.items(), key=lambda item: item[0])
    )


def _chunks(lines: Iterable[str], limit: int = 1024) -> tuple[str, ...]:
    """Split command lines at boundaries before Discord's field limit."""
    chunks: list[str] = []
    current = ""
    for line in lines:
        candidate = f"{current}\n{line}" if current else line
        if current and len(candidate) > limit:
            chunks.append(current)
            current = line
        else:
            current = candidate
    if current:
        chunks.append(current)
    return tuple(chunks)


def build_game_command_directory(bot: discord.Bot, *, tft: bool) -> discord.Embed:
    """Build a directory whose contents follow live command registration."""
    game = "TFT" if tft else "League"
    commands = public_commands(bot, tft=tft)
    lines = (
        f"**`/{command.qualified_name}`** — {command.description}"
        for command in commands
    )
    embed = make_embed(
        "Generated from the bot's registered public commands, so this list "
        "updates with the command tree.",
        title=f"{game} Commands",
    )
    chunks = _chunks(lines)
    if not chunks:
        embed.add_field(name="Commands", value="No commands are registered.", inline=False)
    for index, chunk in enumerate(chunks):
        name = "Commands" if index == 0 else "Commands (continued)"
        embed.add_field(name=name, value=chunk, inline=False)
    return embed

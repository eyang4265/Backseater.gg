"""Shared registered-command discovery for the League and TFT directories."""

from __future__ import annotations

from collections.abc import Iterable

import discord

from ..render import make_embed


# Categories determine presentation only; the registered tree remains the source
# of membership and descriptions. Unknown commands appear under Other unless
# explicitly excluded from the League directory.
_LEAGUE_DIRECTORY_EXCLUSIONS = frozenset({"meetup", "flake"})
_LEAGUE_GROUPS = (
    ("Players & Accounts", ("profile", "opgg", "livegame", "accounts", "add")),
    (
        "Matches & Analysis",
        ("match", "matchhistory", "matchlist", "timeline", "laning", "jungleproximity"),
    ),
    (
        "Champions & Builds",
        ("mastery", "champ", "championpool", "champstats", "counterstats", "trends", "coachless"),
    ),
    ("Ranks & Records", ("today", "lpgraph", "leaderboard", "duo")),
    ("Community & Setup", ("setchannel", "unsetchannel")),
    (
        "Guides & Riot Info",
        ("help", "leaguecommands", "patchnotes", "rotation", "serverstatus"),
    ),
)
_TFT_GROUPS = (
    ("Accounts", ("tftadd", "tftupdate")),
    ("Matches", ("tftmatch", "tftmatchhistory")),
    ("Guide", ("tftcommands",)),
)


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
    """Group live public commands by purpose, with new names in Other."""
    game = "TFT" if tft else "League"
    commands = tuple(
        command for command in public_commands(bot, tft=tft)
        if tft or command.qualified_name not in _LEAGUE_DIRECTORY_EXCLUSIONS
    )
    by_name = {command.qualified_name: command for command in commands}
    embed = make_embed(
        "Find a command by topic.",
        title=f"{game} Commands",
    )
    if not commands:
        embed.add_field(name="Commands", value="No commands are registered.", inline=False)
        return embed

    def add_group(name: str, selected: Iterable[discord.ApplicationCommand]) -> None:
        """Split a topic only between commands to respect field-value limits."""
        lines = (
            f"**`/{command.qualified_name}`** — {command.description}"
            for command in selected
        )
        for index, chunk in enumerate(_chunks(lines)):
            embed.add_field(
                name=name if index == 0 else f"{name} (continued)",
                value=chunk,
                inline=False,
            )

    seen: set[str] = set()
    for category, names in _TFT_GROUPS if tft else _LEAGUE_GROUPS:
        selected = [by_name[name] for name in names if name in by_name]
        seen.update(command.qualified_name for command in selected)
        add_group(category, selected)
    add_group(
        "Other", (command for command in commands if command.qualified_name not in seen)
    )
    return embed

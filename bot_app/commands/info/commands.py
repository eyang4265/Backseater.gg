"""The compact public ``/help`` directory and per-command guide."""

from __future__ import annotations

import logging
import re

import discord
from discord.ext import commands

from ...render import make_embed
from ..shared import GUILD_IDS, log_command

LOGGER = logging.getLogger(__name__)


_COMMANDS = (
    (
        "Players",
        "**`/profile`** — Show level, ranks, and top champions.\n"
        "Usage: add `server` and `username` (accepts `Name#Tag`).\n\n"
        "**`/opgg`** — Get an OP.GG profile link.\n"
        "Usage: add a player/server.\n\n"
        "**`/livegame`** — Show the current lobby, champions, ranks, and win rates.\n"
        "Usage: uses your tracked account by default.\n\n"
        "**`/matchhistory`** — Show up to 10 recent games with results, stats, and a W/L score.\n"
        "Usage: optionally filter by `game_mode` or `champion` (filters search recent games for up to 10 matches), or add a player/server.\n\n"
        "**`/matchlist`** — Show recent match IDs.\n"
        "Usage: add a player/server.",
    ),
    (
        "Matches & Champions",
        "**`/match`** — Show a completed match like an automatic announcement.\n"
        "Usage: optionally provide `match_id` or a player.\n\n"
        "**`/timeline`** — Show timeline-derived kills, deaths, damage, and economy.\n"
        "Usage: optionally provide `match_id` and `position`.\n\n"
        "**`/jungleproximity`** — Show both team junglers' lane proximity for a match.\n"
        "Usage: optionally provide `match_id` or a player.\n\n"
        "**`/laning`** — Show a player's Gold/XP/CS side by side with their lane opponent's.\n"
        "Usage: optionally provide `match_id` or a player. Jungle has no lane opponent and is not supported.\n\n"
        "**`/mastery`** — Show all champion mastery or details for one champion.\n"
        "Usage: optionally provide `champion` or a player.\n\n"
        "**`/today`** — Show today's saved LP movement.\n"
        "Usage: optionally provide a player or `queue`.\n\n"
        "**`/lpgraph`** — Graph saved LP history.\n"
        "Usage: optionally provide a player, `queue`, or `days`.\n\n"
        "**`/leaderboard`** — Rank tracked accounts from saved snapshots.\n"
        "Usage: optionally choose a `queue`.\n\n"
        "**`/duo`** — Show the cached record for two tracked players.\n"
        "Usage: `teammate:<user>`; optionally provide the primary player or filter by `game_mode`.\n\n"
        "**`/champ`** — Show live OP.GG champion stats, skill order, rune emotes, and item build emotes.\n"
        "Usage: `champion:<name>` (uses global stats by default); optionally choose `server` and `position`, or use the position buttons.\n\n"
        "**`/champstats`** — Show your cached record on one champion with rune, final-item, and boot breakdowns.\n"
        "Usage: `champion:<name>`; optionally choose `queue`, `role`, `server`, `username`, or `filter`.\n\n"
        "**`/counterstats`** — Show a champion's win rate against every enemy champion.\n"
        "Usage: `champion:<name> role:<role>`; optionally choose `queue`, `filter`, `server`, or `username`, then use the five role buttons.\n\n"
        "**`/coachless`** — Show Coachless.gg rune and item WPA recommendations.\n"
        "Usage: `champion:<name> role:<role>`.",
    ),
    (
        "Riot & Server",
        "**`/rotation`** — Show this week's free champion rotation.\n\n"
        "**`/serverstatus`** — Show active maintenance and incidents.\n"
        "Usage: optionally choose a `server`.\n\n"
        "**`/setchannel`** — Route this server's announcements to a channel (Manage Server).\n"
        "Usage: optionally choose a `channel`.\n\n"
        "**`/unsetchannel`** — Remove this server's announcement route (Manage Server).",
    ),
)


_ENTRY_NAME_RE = re.compile(r"^\*\*`/(\w+)`\*\*")


def _build_command_lookup() -> dict[str, str]:
    """Derive a ``name -> entry text`` map from ``_COMMANDS``.

    Built from the same source used to render ``/help``, so ``/help
    command:<name>`` never drifts from the directory listing.
    """
    lookup: dict[str, str] = {}
    for _category, command_list in _COMMANDS:
        for entry in command_list.split("\n\n"):
            match = _ENTRY_NAME_RE.match(entry)
            if match:
                lookup[match.group(1)] = entry
    return lookup


_COMMAND_LOOKUP = _build_command_lookup()


def _entry_summary(entry: str) -> str:
    """Return an entry's one-line command name and purpose."""
    return entry.partition("\n")[0]


def _entry_details(entry: str) -> tuple[str, str | None]:
    """Return an entry's purpose and optional usage without repeating its name."""
    first_line, separator, remainder = entry.partition("\n")
    match = _ENTRY_NAME_RE.match(first_line)
    purpose = first_line[match.end() :].removeprefix(" — ") if match else first_line
    usage = remainder.removeprefix("Usage: ").rstrip(".") if separator else None
    return purpose, usage or None


def _field_chunks(entries: tuple[str, ...], limit: int = 1024) -> tuple[str, ...]:
    """Join lines into Discord-safe field values.

    A category can grow without producing an invalid embed when Discord's
    field-value limit is reached. Splits only happen between commands.
    """
    chunks: list[str] = []
    current: list[str] = []
    current_length = 0
    for entry in entries:
        entry_length = len(entry) + (1 if current else 0)
        if current and current_length + entry_length > limit:
            chunks.append("\n".join(current))
            current = []
            current_length = 0
        current.append(entry)
        current_length += len(entry) + (1 if len(current) > 1 else 0)
    if current:
        chunks.append("\n".join(current))
    return tuple(chunks)


class CommandDirectory(commands.Cog):
    """Show a compact command index or detailed help for one command."""

    def __init__(self, bot: discord.Bot) -> None:
        """Initialize the instance."""
        self.bot = bot

    async def _send_command_directory(self, ctx: discord.ApplicationContext) -> None:
        """Send a scannable directory with one concise line per command."""
        log_command(ctx)
        embed = make_embed(
            "Use `/help command:<name>` for usage and options. Player commands "
            "use your tracked account unless you choose someone else.",
            title="Command Guide",
        )
        for category, command_list in _COMMANDS:
            summaries = tuple(
                _entry_summary(entry) for entry in command_list.split("\n\n")
            )
            chunks = _field_chunks(summaries)
            for index, chunk in enumerate(chunks):
                label = category if index == 0 else f"{category} (continued)"
                embed.add_field(name=label, value=chunk, inline=False)
        LOGGER.info("Sent full command directory")
        await ctx.respond(embed=embed)

    async def _autocomplete_command(self, ctx: discord.AutocompleteContext) -> list[str]:
        """Suggest known command names for the ``command`` option."""
        typed = ctx.value.lstrip("/").lower() if ctx.value else ""
        return [name for name in _COMMAND_LOOKUP if name.startswith(typed)][:25]

    def _find_slash_command(self, name: str) -> discord.SlashCommand | None:
        """Look up the live registered command, so option details can never drift."""
        for application_command in self.bot.application_commands:
            if (
                isinstance(application_command, discord.SlashCommand)
                and application_command.qualified_name == name
            ):
                return application_command
        return None

    def _options_text(self, command: discord.SlashCommand) -> str | None:
        """Describe each option's purpose and whether it is required."""
        lines = []
        for option in command.options:
            requirement = "required" if option.required else "optional"
            lines.append(f"`{option.name}` ({requirement}) — {option.description}")
        return "\n".join(lines) if lines else None

    @discord.slash_command(
        guild_ids=GUILD_IDS, description="Browse commands or get help with one"
    )
    @discord.option(
        "command",
        str,
        description="Command to explain (leave blank to browse all commands)",
        required=False,
        autocomplete=_autocomplete_command,
    )
    async def help(self, ctx: discord.ApplicationContext, command: str | None) -> None:
        """Show the compact directory or a focused command guide."""
        if not command:
            await self._send_command_directory(ctx)
            return
        log_command(ctx)
        name = command.lstrip("/").lower()
        entry = _COMMAND_LOOKUP.get(name)
        if entry is None:
            LOGGER.debug("Help requested for unknown command %s", name)
            await ctx.respond(
                f"Unknown command `/{name}`. Use `/help` to see everything available.",
                ephemeral=True,
            )
            return
        purpose, usage = _entry_details(entry)
        embed = make_embed(purpose, title=f"/{name}")
        if usage:
            embed.add_field(name="Usage", value=usage, inline=False)
        slash_command = self._find_slash_command(name)
        options_text = self._options_text(slash_command) if slash_command else None
        if options_text:
            embed.add_field(name="Options", value=options_text, inline=False)
        LOGGER.info("Sent focused help for /%s", name)
        await ctx.respond(embed=embed)


def setup(bot: discord.Bot) -> None:
    """Register this command module with the bot."""
    bot.add_cog(CommandDirectory(bot))

"""The /ownercommands command: a directory of owner-only slash commands."""

from __future__ import annotations

import logging

import discord
from discord.ext import commands

from ...render import make_embed
from ..command_directory import is_owner_only
from ..shared import GUILD_IDS, log_command

LOGGER = logging.getLogger(__name__)


def owner_only_commands(bot: discord.Bot) -> tuple[discord.SlashCommand, ...]:
    """List each owner command once across guild-specific registrations."""
    unique: dict[str, discord.SlashCommand] = {}
    for command in bot.application_commands:
        if isinstance(command, discord.SlashCommand) and is_owner_only(command):
            unique.setdefault(command.qualified_name, command)
    return tuple(command for _, command in sorted(unique.items()))


class OwnerCommands(commands.Cog):
    """List every registered owner-only slash command, kept current by
    introspecting and deduplicating the bot's command tree."""

    def __init__(self, bot: discord.Bot) -> None:
        """Initialize the instance."""
        self.bot = bot

    @discord.slash_command(
        guild_ids=GUILD_IDS, description="List owner-only commands (owner only)"
    )
    @commands.is_owner()
    async def ownercommands(self, ctx: discord.ApplicationContext) -> None:
        """Handle ownercommands."""
        log_command(ctx)
        owner_only = owner_only_commands(self.bot)
        LOGGER.debug("Found %d owner-only commands", len(owner_only))
        lines = [
            f"**`/{command.qualified_name}`** — {command.description}"
            for command in owner_only
        ]
        embed = make_embed(
            "\n".join(lines) or "No owner-only commands are registered.",
            title="Owner Commands",
        )
        await ctx.respond(embed=embed, ephemeral=True)
        LOGGER.info("Sent owner-command directory (%d commands)", len(owner_only))


def setup(bot: discord.Bot) -> None:
    """Register this command module with the bot."""
    bot.add_cog(OwnerCommands(bot))

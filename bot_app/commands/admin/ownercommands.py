"""The /ownercommands command: a directory of owner-only slash commands."""

from __future__ import annotations

import logging

import discord
from discord.ext import commands

from ...render import make_embed
from ..shared import GUILD_IDS, log_command

LOGGER = logging.getLogger(__name__)


def _is_owner_only(command: discord.ApplicationCommand) -> bool:
    """True if a command carries the ``@commands.is_owner()`` check."""
    return any(
        getattr(check, "__qualname__", "").startswith("is_owner")
        for check in getattr(command, "checks", [])
    )


class OwnerCommands(commands.Cog):
    """List every registered owner-only slash command, kept current by
    introspecting the bot's command tree rather than a hand-maintained list."""

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
        owner_only = sorted(
            (
                command
                for command in self.bot.application_commands
                if isinstance(command, discord.SlashCommand)
                and _is_owner_only(command)
            ),
            key=lambda command: command.qualified_name,
        )
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

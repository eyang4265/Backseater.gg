"""Owner-only /updatepartymate checks live teams against the roster's past week."""

import asyncio
import logging

import discord
from discord.ext import commands

from ..partymate_update import update_partymates
from ..render import make_embed
from .shared import GUILD_IDS, log_command

LOGGER = logging.getLogger(__name__)


class UpdatePartymateCommand(commands.Cog):
    """Admit qualifying current live teammates with one roster scan at a time."""

    def __init__(self, bot: discord.Bot) -> None:
        self.bot = bot
        self._running = False

    @discord.slash_command(
        guild_ids=GUILD_IDS,
        description="Check live teammates against the past 7 days (owner only)",
    )
    @commands.is_owner()
    async def updatepartymate(self, ctx: discord.ApplicationContext) -> None:
        """Acknowledge privately before Riot work and report coverage and additions."""
        await ctx.defer(ephemeral=True)
        log_command(ctx)
        if self._running:
            await ctx.edit(embed=make_embed("A `/updatepartymate` scan is already running."))
            return
        self._running = True
        try:
            await ctx.edit(embed=make_embed("Scanning past 7 days and checking current live teams…"))
            result = await self._run_with_progress(ctx)
        except OSError:
            LOGGER.exception("Could not save /updatepartymate results")
            await ctx.edit(embed=make_embed("Could not save the partymates registry. Check the bot logs."))
            return
        finally:
            self._running = False
        body = (
            f"Added **{result.added}** partymates; **{result.total}** saved in total.\n"
            f"Scanned **{result.matches}** unique matches from **{result.accounts}** "
            "tracked League accounts in the past 7 days."
        )
        if result.incomplete_accounts or result.unavailable_matches:
            body += (
                f"\nIncomplete histories: **{result.incomplete_accounts}** accounts. "
                f"Unavailable matches: **{result.unavailable_matches}**."
            )
        if not result.accounts:
            body += "\nThere are no tracked League accounts to scan."
        await ctx.edit(embed=make_embed(body))
        LOGGER.info("/updatepartymate result: %s", result)

    async def _run_with_progress(self, ctx):
        """Run the blocking sweep in a worker, updating the private reply."""
        status = None

        def record(done, total, matches):
            nonlocal status
            status = (done, total, matches)

        task = asyncio.create_task(asyncio.to_thread(update_partymates, progress=record))
        shown = None
        while not task.done():
            finished, _ = await asyncio.wait({task}, timeout=5)
            if finished or status is None or status == shown:
                continue
            shown = status
            done, total, matches = status
            try:
                await ctx.edit(embed=make_embed(
                    f"Scanning past 7 days… **{done}/{total}** accounts, **{matches}** unique matches."
                ))
            except discord.DiscordException:
                LOGGER.debug("Could not edit partymate scan progress", exc_info=True)
        return await task


def setup(bot: discord.Bot) -> None:
    """Register the owner-only partymate update command."""
    bot.add_cog(UpdatePartymateCommand(bot))

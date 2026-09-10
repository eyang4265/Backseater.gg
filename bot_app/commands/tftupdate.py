"""Owner-only ``/tftupdate`` command for refreshing all TFT identities."""

from __future__ import annotations

import asyncio
import logging

import discord
from discord.ext import commands

from ..config import get_settings
from ..render import make_embed
from ..store import load_accounts
from ..tft_account_registry import update_all_tft_from_league
from .shared import GUILD_IDS, log_command

LOGGER = logging.getLogger(__name__)


class TftUpdateCommand(commands.Cog):
    """Refresh every separately stored TFT PUUID from League identities."""

    def __init__(self, bot: discord.Bot) -> None:
        """Initialize the cog."""
        self.bot = bot
        self._running = False

    @discord.slash_command(
        guild_ids=GUILD_IDS,
        description="Update every TFT PUUID from the linked League PUUIDs",
    )
    @commands.is_owner()
    async def tftupdate(self, ctx):
        """Resolve all League PUUIDs and refresh every TFT registration."""
        log_command(ctx)
        await ctx.defer(ephemeral=True)
        if self._running:
            # A second concurrent sweep doubles the Riot spend and makes both
            # runs take twice as long, which is what left earlier invocations
            # silent for ten minutes.
            await ctx.edit(
                embed=make_embed(
                    "A `/tftupdate` sweep is already running. "
                    "Wait for it to report back before starting another."
                )
            )
            return
        total = len(load_accounts())
        if not total:
            await ctx.edit(
                embed=make_embed("There are no tracked League accounts to update.")
            )
            return
        self._running = True
        try:
            updated, failures = await self._run_with_progress(ctx, total)
        finally:
            self._running = False
        lines = [f"Updated **{len(updated)} of {total}** TFT identities."]
        if failures:
            lines.append("\n**Failed:**")
            league_accounts = load_accounts()
            for discord_id, error in failures.items():
                riot_id = league_accounts.get(discord_id)
                label = riot_id.riot_id if riot_id else discord_id
                line = f"\n<@{discord_id}> ({label}): {error}"
                if len("".join(lines)) + len(line) > 3900:
                    remaining = len(failures) - (len(lines) - 2)
                    lines.append(f"\n…and {remaining} more failures.")
                    break
                lines.append(line)
        LOGGER.info(
            "/tftupdate refreshed %d of %d accounts (%d failed)",
            len(updated),
            total,
            len(failures),
        )
        await ctx.edit(embed=make_embed("".join(lines)))

    async def _run_with_progress(self, ctx, total: int):
        """Sweep the roster while keeping the deferred reply visibly alive.

        The sweep is long enough that a silent deferred reply is impossible to
        distinguish from a dead one, so the original response is edited as
        accounts land.  ``progress`` fires on the worker thread, so it only
        records a count; the editing happens here on the event loop.
        """
        done = 0

        def record(completed: int, _total: int) -> None:
            """Note progress from the worker thread."""
            nonlocal done
            done = completed

        task = asyncio.create_task(
            asyncio.to_thread(
                update_all_tft_from_league,
                max_accounts=get_settings().max_tracked_accounts,
                progress=record,
            )
        )
        shown = -1
        while not task.done():
            done_waiting, _ = await asyncio.wait({task}, timeout=5)
            if done_waiting or done == shown:
                continue
            shown = done
            try:
                await ctx.edit(
                    embed=make_embed(f"Updating TFT identities… **{done} of {total}**.")
                )
            except discord.DiscordException as error:
                LOGGER.debug("Could not edit /tftupdate progress: %s", error)
        return await task


def setup(bot: discord.Bot) -> None:
    """Register the TFT update command."""
    bot.add_cog(TftUpdateCommand(bot))

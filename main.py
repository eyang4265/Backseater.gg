"""Bot entry point: builds the Discord client and runs the background pollers."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

import discord
from discord.ext import commands, tasks

from bot_app.commands import register_all
from bot_app.config import Settings, get_settings
from bot_app.guest_tracker import guest_poll_and_announce
from bot_app.match_cache import get_match_cache
from bot_app.runtime import configure_bot
from bot_app.render import make_embed
from bot_app.commands.guilds import unset_guild_channel
from bot_app.tracker import (
    poll_and_announce,
    poll_live_games_and_announce,
    update_all_rank_snapshots,
)

LOGGER = logging.getLogger(__name__)


Poller = Callable[[discord.Bot], Awaitable[None]]


async def handle_application_command_error(ctx, error: Exception) -> bool:
    """Render expected permission failures; return whether it was handled."""
    if not isinstance(error, commands.MissingPermissions):
        return False
    await ctx.respond(
        embed=make_embed("You need the Manage Server permission to use this command."),
        ephemeral=True,
    )
    return True


def build_bot(settings: Settings) -> discord.Bot:
    """Build bot."""
    intents = discord.Intents.default()
    bot = discord.Bot(intents=intents, owner_id=settings.discord_owner_id)
    configure_bot(bot)
    register_all(bot)
    return bot


def make_poller(
    bot: discord.Bot,
    poll: Poller,
    *,
    interval_seconds: int,
    initial_delay_seconds: float = 0,
) -> tasks.Loop[None]:
    """A started-on-ready task loop for one poller coroutine."""

    @tasks.loop(seconds=interval_seconds)
    async def loop() -> None:
        """Run the recurring background operation."""
        try:
            await poll(bot)
        except Exception:
            LOGGER.exception("%s failed", poll.__name__)

    @loop.before_loop
    async def wait_until_ready() -> None:
        """Wait until the bot is ready before polling."""
        await bot.wait_until_ready()
        if initial_delay_seconds:
            await asyncio.sleep(initial_delay_seconds)

    return loop


def main() -> None:
    """Handle main."""
    settings = get_settings()
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )

    bot = build_bot(settings)
    pollers = [
        make_poller(
            bot, poll_and_announce, interval_seconds=settings.poll_interval_seconds
        ),
        make_poller(
            bot,
            poll_live_games_and_announce,
            interval_seconds=settings.poll_interval_seconds,
            initial_delay_seconds=settings.poll_interval_seconds / 3,
        ),
        make_poller(
            bot,
            guest_poll_and_announce,
            interval_seconds=settings.poll_interval_seconds,
            initial_delay_seconds=settings.poll_interval_seconds * 2 / 3,
        ),
    ]

    @bot.event
    async def on_ready() -> None:
        """Handle ready."""
        LOGGER.info(
            "Logged in as %s (%dms latency)", bot.user, round(bot.latency * 1000)
        )

        if not pollers[0].is_running():
            updated, failed = await asyncio.to_thread(update_all_rank_snapshots)
            LOGGER.info("Rank snapshots updated: %d | failed: %d", updated, failed)
            if settings.match_cache_enabled:
                pruned = await asyncio.to_thread(get_match_cache().prune)
                LOGGER.info("Pruned %d expired matches from the cache", pruned)

        for poller in pollers:
            if not poller.is_running():
                poller.start()

    @bot.event
    async def on_guild_remove(guild: discord.Guild) -> None:
        """Handle guild remove."""
        await asyncio.to_thread(unset_guild_channel, guild.id)

    @bot.event
    async def on_application_command_error(ctx, error: Exception) -> None:
        """Handle application command error."""
        if await handle_application_command_error(ctx, error):
            return
        LOGGER.error(
            "Unhandled application command error",
            exc_info=(type(error), error, error.__traceback__),
        )

    bot.run(settings.discord_token)


if __name__ == "__main__":
    main()

"""Bot entry point: builds the Discord client and runs the background pollers."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

import discord
from discord.ext import tasks

from bot_app.commands import register_all
from bot_app.config import Settings, get_settings
from bot_app.guest_tracker import guest_poll_and_announce
from bot_app.runtime import configure_bot
from bot_app.tracker import (
    poll_and_announce,
    poll_live_games_and_announce,
    update_all_rank_snapshots,
)

LOGGER = logging.getLogger(__name__)


Poller = Callable[[discord.Bot], Awaitable[None]]


def build_bot(settings: Settings) -> discord.Bot:
    intents = discord.Intents.default()
    intents.message_content = True
    bot = discord.Bot(intents=intents, owner_id=settings.discord_owner_id)
    configure_bot(bot)
    register_all(bot)
    return bot


def make_poller(
    bot: discord.Bot, poll: Poller, *, interval_seconds: int
) -> tasks.Loop[None]:
    """A started-on-ready task loop for one poller coroutine."""

    @tasks.loop(seconds=interval_seconds)
    async def loop() -> None:
        try:
            await poll(bot)
        except Exception:
            # A failed cycle must not stop the loop; the next tick retries.
            LOGGER.exception("%s failed", poll.__name__)

    @loop.before_loop
    async def wait_until_ready() -> None:
        await bot.wait_until_ready()

    return loop


def main() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )

    bot = build_bot(settings)
    pollers = [
        make_poller(bot, poll_and_announce, interval_seconds=settings.poll_interval_seconds),
        make_poller(
            bot,
            poll_live_games_and_announce,
            interval_seconds=settings.poll_interval_seconds,
        ),
        make_poller(
            bot,
            guest_poll_and_announce,
            interval_seconds=settings.poll_interval_seconds,
        ),
    ]

    @bot.event
    async def on_ready() -> None:
        LOGGER.info("Logged in as %s (%dms latency)", bot.user, round(bot.latency * 1000))

        if not pollers[0].is_running():
            updated, failed = await asyncio.to_thread(update_all_rank_snapshots)
            LOGGER.info("Rank snapshots updated: %d | failed: %d", updated, failed)

        for poller in pollers:
            if not poller.is_running():
                poller.start()

    bot.run(settings.discord_token)


if __name__ == "__main__":
    main()

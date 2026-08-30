"""Bot entry point: builds the Discord client and runs the background pollers."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import discord
from discord.ext import commands, tasks

from bot_app import ddragon
from bot_app.commands import register_all
from bot_app.commands.shared import (
    command_option_fields,
    log_command,
    log_command_completion,
    log_command_error,
)
from bot_app.config import Settings, get_settings
from bot_app.guest_tracker import guest_poll_and_announce
from bot_app.http_debug import install_rate_limit_debug_logging
from bot_app.match_cache import get_match_cache
from bot_app.queues import validate_current_queue_ids
from bot_app.runtime import configure_bot
from bot_app.singleton import acquire_singleton_lock
from bot_app.render import make_embed
from bot_app.commands.guilds import unset_guild_channel
from bot_app.announce import register_persistent_embed_views
from bot_app.command_views import register_persistent_command_views
from bot_app.tracker import (
    poll_and_announce,
    poll_live_games_and_announce,
    update_all_rank_snapshots,
)

LOGGER = logging.getLogger(__name__)


Poller = Callable[[discord.Bot], Awaitable[None]]


def mention_reply_content(latency_seconds: float) -> str:
    """Return the concise response sent when the bot is mentioned."""
    latency_ms = max(round(latency_seconds * 1000), 0)
    return f"🏓 Pong! **{latency_ms} ms**\nDo `/help` for the list of commands."


async def handle_application_command_error(ctx, error: Exception) -> bool:
    """Render expected permission failures; return whether it was handled."""
    if not isinstance(error, commands.MissingPermissions):
        return False
    await ctx.respond(
        embed=make_embed("You need the Manage Server permission to use this command."),
        ephemeral=True,
    )
    return True


@dataclass
class ConnectState:
    """Once-per-process flags for :func:`sync_and_restore_views`."""

    commands_synced: bool = False
    persistent_views_restored: bool = False


async def sync_and_restore_views(bot: discord.Bot, settings: Settings, state: ConnectState) -> None:
    """Sync slash commands and restore persistent views once per process.

    These two are independent: a failed command sync (a bad option
    definition, a Discord API hiccup) must not stop button state from being
    restored, since that would leave every existing embed's buttons dead
    until the next successful sync.
    """
    if bot.auto_sync_commands and not state.commands_synced:
        state.commands_synced = True
        if settings.sync_commands_enabled:
            try:
                await bot.sync_commands()
                LOGGER.info("Synchronized application commands")
            except Exception:
                LOGGER.exception("Could not synchronize application commands")
        else:
            LOGGER.warning("Skipping command sync (sync_commands_enabled is false)")
    if state.persistent_views_restored:
        return
    try:
        restored = register_persistent_embed_views(bot) + register_persistent_command_views(bot)
    except Exception:
        LOGGER.exception("Could not restore persistent embed views")
        return
    LOGGER.info("Registered %d persistent embed views", restored)
    state.persistent_views_restored = True


def build_bot(settings: Settings) -> discord.Bot:
    """Build bot."""
    intents = discord.Intents.default()
    bot = discord.Bot(intents=intents, owner_id=settings.discord_owner_id)
    configure_bot(bot)
    register_all(bot)

    @bot.before_invoke
    async def log_every_command(ctx) -> None:
        """Log every parsed slash command through the shared diagnostics path."""
        log_command(ctx, **command_option_fields(ctx))

    @bot.after_invoke
    async def log_every_command_completion(ctx) -> None:
        """Log completion timing after each parsed slash command invocation."""
        log_command_completion(ctx)

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
    log_format = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"
    logging.basicConfig(
        level=settings.log_level,
        format=log_format,
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler("bot.log", encoding="utf-8"),
        ],
    )
    # These libraries log their own request/event traffic at DEBUG (every HTTP
    # request line, every raw gateway payload); cap them so LOG_LEVEL=DEBUG
    # still surfaces this bot's own diagnostics without that firehose.
    for noisy_logger in ("urllib3", "discord.gateway"):
        logging.getLogger(noisy_logger).setLevel(logging.INFO)
    install_rate_limit_debug_logging()
    acquire_singleton_lock()

    stale_queue_ids = validate_current_queue_ids()
    if stale_queue_ids:
        LOGGER.warning(
            "CURRENT_QUEUE_IDS in bot_app/queues.py has ids with no matching"
            " QUEUE_NAMES entry: %s",
            stale_queue_ids,
        )

    bot = build_bot(settings)
    connect_state = ConnectState()
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
    async def on_connect() -> None:
        """Handle connect."""
        await sync_and_restore_views(bot, settings, connect_state)

    @bot.event
    async def on_message(message: discord.Message) -> None:
        """Reply with latency and command-directory guidance when mentioned."""
        if message.author.bot:
            return
        if bot.user is not None and bot.user.id in message.raw_mentions:
            await message.channel.send(mention_reply_content(bot.latency))

    @bot.event
    async def on_ready() -> None:
        """Handle ready."""
        LOGGER.info(
            "Logged in as %s (%dms latency)", bot.user, round(bot.latency * 1000)
        )

        if not pollers[0].is_running():
            await asyncio.to_thread(ddragon.recent_patch_prefixes, 6)
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
        log_command_error(ctx, error)

    bot.run(settings.discord_token)


if __name__ == "__main__":
    main()

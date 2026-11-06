"""Independent per-server Worlds routes with existing announcement-channel defaults."""

import asyncio
import logging
import threading

import discord

from .config import DATA_DIR, get_settings
from .channel_preferences import disabled_guilds
from .store import load_guild_channels, read_json, write_json

LOGGER = logging.getLogger(__name__)
STATE_PATH = DATA_DIR / "worlds_channels.json"
_LOCK = threading.Lock()


def load_worlds_channels():
    """Read valid guild-to-channel overrides from private runtime storage."""
    raw = read_json(STATE_PATH, {})
    if not isinstance(raw, dict):
        return {}
    return {str(guild): channel for guild, channel in raw.items()
            if type(channel) is int and channel > 0}


def set_worlds_channel(guild_id, channel_id):
    """Serialize route updates so simultaneous server changes cannot overwrite one another."""
    with _LOCK:
        channels = load_worlds_channels()
        channels[str(guild_id)] = channel_id
        write_json(STATE_PATH, channels)


def remove_worlds_channel(guild_id):
    """Remove a server's Worlds override for an opt-out or guild removal."""
    with _LOCK:
        channels = load_worlds_channels()
        channels.pop(str(guild_id), None)
        write_json(STATE_PATH, channels)


async def resolve_worlds_channels(bot):
    """Resolve overrides/defaults while honoring persistent server Worlds opt-outs."""
    general = await asyncio.to_thread(load_guild_channels)
    overrides = await asyncio.to_thread(load_worlds_channels)
    disabled = await asyncio.to_thread(disabled_guilds, "worlds")
    channels = {channel for guild, channel in general.items() if str(guild) not in overrides and str(guild) not in disabled}
    channels.update(channel for guild, channel in overrides.items() if str(guild) not in disabled)
    fallback = get_settings().announcement_channel_id
    if fallback and (overrides or disabled):
        try:
            channel = bot.get_channel(fallback) or await bot.fetch_channel(fallback)
            guild = getattr(channel, "guild", None)
            if guild is None or (str(guild.id) not in overrides and str(guild.id) not in disabled):
                channels.add(fallback)
        except discord.DiscordException:
            LOGGER.warning("Could not resolve fallback Worlds channel %s", fallback, exc_info=True)
    elif fallback:
        channels.add(fallback)
    return channels - {0}

"""Durable manual /worlds schedules refreshed on the shared adaptive cadence."""

import asyncio
from datetime import datetime, timezone
import logging

import discord

from .config import DATA_DIR
from .store import read_json, write_json

LOGGER = logging.getLogger(__name__)
STATE_PATH = DATA_DIR / "worlds_schedule_posts.json"
_LOCK = asyncio.Lock()
_VIEWS = {}
RETENTION_SECONDS = 14 * 86400


async def remember_schedule_post(view, channel_id, message_id, *, created=None):
    """Save the message and selected date/page for refresh and restart recovery."""
    async with _LOCK:
        state = await asyncio.to_thread(read_json, STATE_PATH, {})
        key = str(message_id)
        prior = state.get(key, {})
        state[key] = {"channel_id": channel_id, "message_id": message_id,
                      "created": prior.get("created", created or datetime.now(timezone.utc).timestamp()),
                      "selected": view.selected, "page": view.page}
        await asyncio.to_thread(write_json, STATE_PATH, state)
        view.post_id = message_id
        view.channel_id = channel_id
        _VIEWS[key] = view


async def poll_worlds_schedule_posts(bot, *, now=None):
    """Refresh retained manual posts, preserving selection and retrying failed edits.

    Use channel message edits rather than expiring interaction tokens. Missing
    messages are forgotten; feed failures leave the last successful score intact.
    """
    from .worlds import fetch_series
    from .worlds_views import WorldsScheduleView, schedule_tabs

    current = now or datetime.now(timezone.utc)
    async with _LOCK:
        state = await asyncio.to_thread(read_json, STATE_PATH, {})
    if not state:
        return
    series = await asyncio.to_thread(fetch_series, include_unknown=True)
    tabs = schedule_tabs(series, now=current)
    for key, record in list(state.items()):
        remove = current.timestamp() - record["created"] > RETENTION_SECONDS
        if not remove:
            view = _VIEWS.get(key)
            if view is None:
                view = WorldsScheduleView(tabs, show_stats=False, tracked=True)
                view.selected = min(record["selected"], len(tabs) - 1)
                view.page = min(record["page"], len(tabs[view.selected][1]) - 1)
                view.post_id, view.channel_id = record["message_id"], record["channel_id"]
                view._sync()
            async with view._lock:
                controls = [(item.custom_id, item.label, item.disabled) for item in view.children]
                view.refresh_tabs(tabs)
                controls_changed = controls != [(item.custom_id, item.label, item.disabled) for item in view.children]
                try:
                    channel = bot.get_channel(record["channel_id"]) or await bot.fetch_channel(record["channel_id"])
                    message = await channel.fetch_message(record["message_id"])
                    embed = view.render()
                    previous = message.embeds[0].to_dict() if message.embeds else None
                    # Rebind controls after restart even when the score is unchanged.
                    if key not in _VIEWS or controls_changed or previous != embed.to_dict():
                        await message.edit(embed=embed, view=view,
                                           allowed_mentions=discord.AllowedMentions.none())
                    view.message = message
                    _VIEWS[key] = view
                except discord.NotFound:
                    remove = True
                except discord.DiscordException:
                    LOGGER.exception("Could not refresh Worlds schedule message=%s; will retry", key)
        if remove:
            expired = _VIEWS.pop(key, None)
            if expired is not None:
                expired.stop()
            async with _LOCK:
                latest = await asyncio.to_thread(read_json, STATE_PATH, {})
                latest.pop(key, None)
                await asyncio.to_thread(write_json, STATE_PATH, latest)

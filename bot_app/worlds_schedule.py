"""4 a.m. Pacific Worlds schedules with durable, reaction-based series subscriptions."""

import asyncio
from datetime import datetime, timedelta, timezone
import logging
import hashlib
import json
from zoneinfo import ZoneInfo

import discord

from .config import DATA_DIR
from .paginator import Paginator
from .store import read_json, write_json
from .worlds_routing import resolve_worlds_channels

LOGGER = logging.getLogger(__name__)
STATE_PATH = DATA_DIR / "worlds_schedule.json"
PACIFIC = ZoneInfo("America/Los_Angeles")
SCHEDULE_LAYOUT_VERSION = 7
NUMBER_REACTIONS = ("1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟")
_LOCK = asyncio.Lock()


def schedule_time_reached(now):
    """Use local wall time for the daily 04:00 threshold, including DST changes."""
    return now.astimezone(PACIFIC).hour >= 4


def schedule_plan(series, now, *, mapping=None):
    """Build only today's shared schedule and its chronological reaction map."""
    from .worlds import status_embeds, schedule_message_embeds

    local = now.astimezone(PACIFIC)
    today = sorted((s for s in series if datetime.fromtimestamp(s["start"], PACIFIC).date() == local.date()),
                   key=lambda s: (s["start"], s["id"]))
    if len(today) > len(NUMBER_REACTIONS):
        raise ValueError("Worlds schedule exceeds supported numbered reactions")
    if mapping is None:
        mapping = {NUMBER_REACTIONS[i]: s["id"] for i, s in enumerate(today)}
    markers = {series_id: emoji for emoji, series_id in mapping.items()}
    embeds = schedule_message_embeds(status_embeds(today, now=now, series_markers=markers, include_next=False))
    if mapping:
        for embed in embeds:
            embed.description = ((embed.description or "") + "\nReact to a number for a series-start ping. Remove it to opt out.").strip()
    return embeds, mapping


async def ensure_schedule_post(channel, embeds, mapping, receipt, remember):
    """Send or refresh a numbered schedule, preserving its reactions and receipt."""
    fingerprint = hashlib.sha256(json.dumps([embed.to_dict() for embed in embeds],
                                           sort_keys=True).encode()).hexdigest()
    if receipt is None:
        if len(embeds) == 1:
            message = await channel.send(embed=embeds[0], allowed_mentions=discord.AllowedMentions.none())
        else:
            view = Paginator(embeds, author_id=0, page_size=1, timeout=1800,
                             render_page=lambda items, _page, _pages: items[0])
            message = await channel.send(embed=view.render(), view=view,
                                         allowed_mentions=discord.AllowedMentions.none())
            view.message = message
        receipt = {"message_id": message.id, "series": mapping, "added": [], "ready": False, "layout": SCHEDULE_LAYOUT_VERSION, "fingerprint": fingerprint}
        await remember(receipt)
    else:
        message = await channel.fetch_message(receipt["message_id"])
        if (receipt.get("layout") != SCHEDULE_LAYOUT_VERSION
                or receipt.get("fingerprint") != fingerprint):
            view = None
            if len(embeds) > 1:
                view = Paginator(embeds, author_id=0, page_size=1, timeout=1800,
                                 render_page=lambda items, _page, _pages: items[0])
                view.message = message
            await message.edit(embed=embeds[0], view=view, allowed_mentions=discord.AllowedMentions.none())
            receipt["layout"] = SCHEDULE_LAYOUT_VERSION
            receipt["fingerprint"] = fingerprint
            await remember(receipt)
    for emoji in receipt["series"]:
        if emoji in receipt["added"]:
            continue
        await message.add_reaction(emoji)
        receipt["added"].append(emoji)
        await remember(receipt)
    receipt["ready"] = True
    await remember(receipt)
    return receipt


async def poll_worlds_schedule(bot, *, now=None):
    """Post today's schedule after 04:00 and refresh on the adaptive cadence.

    Persist the series-to-number map and message id before adding reactions;
    failed reaction setup resumes without reposting the schedule. Discord stores
    subscriptions as reactions, so add/remove actions survive bot restarts.
    """
    from .worlds import fetch_series

    async with _LOCK:
        now = now or datetime.now(timezone.utc)
        local = now.astimezone(PACIFIC)
        if not schedule_time_reached(now):
            return
        day = local.date().isoformat()
        channel_ids = await resolve_worlds_channels(bot)
        state = await asyncio.to_thread(read_json, STATE_PATH, {"channels": {}})
        pending = sorted(channel_ids)
        if not pending:
            return
        series = await asyncio.to_thread(fetch_series, include_unknown=True)
        embeds, mapping = schedule_plan(series, now)
        cutoff = (local.date() - timedelta(days=14)).isoformat()
        for saved in state["channels"].values():
            for old_day in list(saved):
                if old_day < cutoff:
                    del saved[old_day]
        for channel_id in pending:
            try:
                channel = bot.get_channel(channel_id) or await bot.fetch_channel(channel_id)
                if channel is None:
                    continue
                saved = state["channels"].setdefault(str(channel_id), {})
                async def remember(receipt):
                    saved[day] = receipt
                    await asyncio.to_thread(write_json, STATE_PATH, state)

                post_embeds, post_mapping = embeds, mapping
                if saved.get(day):
                    post_embeds, post_mapping = schedule_plan(series, now, mapping=saved[day]["series"])
                await ensure_schedule_post(channel, post_embeds, post_mapping, saved.get(day), remember)
                LOGGER.info("Worlds daily schedule channel=%s day=%s series=%s", channel_id, day, len(mapping))
            except discord.NotFound:
                # A deleted daily post is not recreated, avoiding unwanted reposts.
                if day in state["channels"].get(str(channel_id), {}):
                    state["channels"][str(channel_id)][day]["ready"] = True
                    await asyncio.to_thread(write_json, STATE_PATH, state)
            except discord.DiscordException:
                LOGGER.exception("Worlds daily schedule failed channel=%s; will retry", channel_id)


async def series_subscribers(channel, series_id):
    """Read current non-bot reactors for this series; reaction removal opts out."""
    state = await asyncio.to_thread(read_json, STATE_PATH, {"channels": {}})
    users = set()
    for saved in state["channels"].get(str(channel.id), {}).values():
        numbers = {emoji for emoji, match in saved["series"].items() if match == series_id}
        if not numbers:
            continue
        try:
            message = await channel.fetch_message(saved["message_id"])
        except discord.NotFound:
            continue
        for reaction in message.reactions:
            if str(reaction.emoji) in numbers:
                async for user in reaction.users():
                    if not user.bot:
                        users.add(user.id)
    return sorted(users)


async def send_start_alert(channel, series, embed, receipt, save):
    """Send subscribed start alerts and persist every message for end-of-series cleanup."""
    if "start_subscribers" not in receipt:
        receipt["start_subscribers"] = await series_subscribers(channel, series["id"])
        await save()
    users = receipt["start_subscribers"]
    chunks = [users[index:index + 60] for index in range(0, len(users), 60)] or [[]]
    for index in range(receipt.get("start_batches", 0), len(chunks)):
        chunk = chunks[index]
        content = " ".join(f"<@{user_id}>" for user_id in chunk)
        options = {"embed": embed} if index == 0 else {}
        if index:
            content += "\nWorlds series is live."
        message = await channel.send(content=content or None, **options,
                           allowed_mentions=discord.AllowedMentions(users=[discord.Object(id=user_id) for user_id in chunk],
                                                                    roles=False, everyone=False, replied_user=False))
        receipt.setdefault("live_message_ids", []).append(message.id)
        receipt["start_batches"] = index + 1
        await save()

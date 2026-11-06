"""Durable cleanup of a series' bot-authored live and score posts after its final."""

from datetime import datetime, timezone
import logging
import re

import discord

LOGGER = logging.getLogger(__name__)


async def delete_series_live_notices(bot, channel, series, receipt, save):
    """Recover recent live/score post IDs, then delete them with retryable receipts."""
    from .worlds import announcement
    if series["state"] != "completed" or not receipt.get("final"):
        return
    if not receipt.get("live_notice_scan_done"):
        titles = {"Worlds · Series started", "Worlds · Series live", "Worlds · Live game",
                  "Worlds · Game result"}
        for number in range(1, series["best_of"] + 1):
            title = announcement(series, game_start=number).description.split("\n", 1)[0]
            titles.update((title, re.sub(r" \d+-\d+(?= · Game)", "", title),
                           f"Worlds · Game {number} live", f"Worlds · Game {number} result"))
        names = [[discord.utils.escape_markdown(str(labels[index]))
                  for labels in (series["names"], series.get("team_names", series["names"]))]
                 for index in range(2)]
        after = datetime.fromtimestamp(series["start"] - 3600, timezone.utc)
        async for message in channel.history(limit=None, after=after, before=datetime.fromtimestamp(series["start"] + 12 * 3600, timezone.utc)):
            if message.author.id != bot.user.id:
                continue
            for embed in message.embeds:
                if (embed.title in titles and all(any(re.search(r"(?<!\w)" + re.escape(name) + r"(?!\w)",
                                                                embed.description or "") for name in team)
                                                 for team in names)):
                    ids = receipt.setdefault("live_message_ids", [])
                    if message.id not in ids:
                        ids.append(message.id)
                    break
        receipt["live_notice_scan_done"] = True
        await save()
    for message_id in dict.fromkeys(receipt.get("live_message_ids", [])):
        try:
            await channel.get_partial_message(message_id).delete()
        except discord.NotFound:
            pass
        receipt["live_message_ids"] = [saved for saved in receipt["live_message_ids"] if saved != message_id]
        await save()
        LOGGER.info("Worlds live notice cleaned up series=%s message=%s", series["id"], message_id)

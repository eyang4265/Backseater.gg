"""Acknowledged Worlds command responses using shared schedule and game renderers."""

import asyncio
import logging

import discord

from .lolesports import EsportsUnavailable
from .render import make_embed
from .worlds import fetch_series
from .worlds_history import fetch_stats_series
from .worlds_views import schedule_tabs, WorldsScheduleView

LOGGER = logging.getLogger(__name__)


async def send_worlds_browser(ctx, *, show_stats):
    """Acknowledge before fetching; show schedules or open directly to the latest series game."""
    await ctx.defer()
    try:
        series = (await asyncio.to_thread(fetch_stats_series) if show_stats else
                  await asyncio.to_thread(fetch_series, include_unknown=True))
    except (EsportsUnavailable, ValueError):
        LOGGER.warning("Worlds status unavailable", exc_info=True)
        await ctx.respond(embed=make_embed("Worlds data unavailable. Try again shortly.", title="Worlds"))
        return
    if show_stats:
        from .worlds_stats_views import WorldsStatsView
        matches = sorted((match for match in series if sum(match["score"]) > 0
                          and match.get("known_teams", True)),
                         key=lambda match: (match["start"], match["id"]), reverse=True)
        if not matches:
            await ctx.respond(embed=make_embed("No Worlds game stats available.", title="Worlds"))
            return
        view = WorldsStatsView(matches[0], ctx.channel.id, series_list=matches)
        options = await view.render_game(1, guild=getattr(ctx, "guild", None), channel=ctx.channel)
        view.message = await ctx.respond(allowed_mentions=discord.AllowedMentions.none(), **options)
        return
    tabs = schedule_tabs(series)
    view = WorldsScheduleView(tabs, show_stats=False, tracked=True)
    view.message = await ctx.respond(embeds=[view.render()], view=view,
                                    allowed_mentions=discord.AllowedMentions.none())
    from .worlds_schedule_posts import remember_schedule_post
    message = view.message
    if isinstance(message, discord.Interaction):
        message = await message.original_response()
    if isinstance(getattr(message, "id", None), int):
        await remember_schedule_post(view, ctx.channel.id, message.id)

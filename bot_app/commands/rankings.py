"""LP history and stored-rank leaderboard commands."""

from __future__ import annotations

import asyncio
import logging
import time

import discord
from discord.ext import commands

from ..charts import LP_CHART_FILENAME, build_lp_chart
from ..config import get_settings
from ..leaderboard import LeaderboardRow, leaderboard_rows
from ..lp_history import since_local_midnight, summarize, summary_text
from ..paginator import Paginator
from ..queues import FLEX_QUEUE_ID, SOLO_QUEUE_ID
from ..render import make_embed, rank_text
from ..store import load_accounts, load_tracker_state
from ..tracker import fetch_all_rank_snapshots
from .shared import GUILD_IDS, SERVERS, log_command, not_found_embed, target_for

LOGGER = logging.getLogger(__name__)
LEADERBOARD_PAGE_SIZE = 10


def _queue_id(queue: str | None) -> int:
    """Handle id."""
    return FLEX_QUEUE_ID if (queue or "").lower() == "flex" else SOLO_QUEUE_ID


def _target_state(target):
    """Handle state."""
    account = next(
        (item for item in load_accounts().values() if item.puuid == target.puuid), None
    )
    return load_tracker_state().get(account.discord_id) if account else None


def _leaderboard_embed(
    items, page: int, pages: int, *, oldest_snapshot: int | None = None
) -> discord.Embed:
    """Handle embed."""
    lines = []
    for offset, row in enumerate(items, start=page * LEADERBOARD_PAGE_SIZE + 1):
        standing = rank_text(row.rank) or "Unranked"
        record = f" · {row.rank.wins}W {row.rank.losses}L" if row.rank else ""
        lines.append(f"**{offset}. {row.account.riot_id}** — {standing}{record}")
    embed = make_embed(
        "\n".join(lines) or "No accounts are currently tracked.",
        title="Ranked Leaderboard",
    )
    footer = f"Page {page + 1}/{pages}" if pages > 1 else ""
    if oldest_snapshot:
        age_hours = max(int((time.time() - oldest_snapshot) / 3600), 0)
        footer += (" · " if footer else "") + f"Oldest snapshot: {age_hours}h ago"
    if footer:
        embed.set_footer(text=footer)
    return embed


class RankingCommands(commands.Cog):
    def __init__(self, bot: discord.Bot) -> None:
        """Initialize the instance."""
        self.bot = bot

    async def _history_target(self, ctx, server, username):
        """Handle target."""
        target = await target_for(ctx, server, username, include_icon=False)
        if target is None:
            await ctx.respond(embed=not_found_embed(username, server, ctx=ctx))
            return None, None
        state = await asyncio.to_thread(_target_state, target)
        if state is None:
            LOGGER.info("LP history unavailable: %s is not a tracked account", target.riot_id)
            await ctx.respond(
                embed=make_embed("LP history is only available for tracked accounts.")
            )
            return None, None
        return target, state

    @discord.slash_command(
        guild_ids=GUILD_IDS, description="Show today's stored LP changes"
    )
    @discord.option("server", description="Server", choices=SERVERS, required=False)
    @discord.option("username", description="League or Discord username (defaults to you)", required=False)
    @discord.option(
        "queue", description="Ranked queue", choices=["solo", "flex"], required=False
    )
    async def today(self, ctx, server, username, queue="solo"):
        """Handle today."""
        log_command(
            ctx, server=server, username=username, queue=queue
        )
        await ctx.defer()
        target, state = await self._history_target(ctx, server, username)
        if state is None:
            return
        entries = since_local_midnight(state, _queue_id(queue), get_settings().timezone)
        LOGGER.debug("/today: %d entries since local midnight for %s (queue=%s)", len(entries), target.riot_id, queue)
        if not entries:
            await ctx.respond(
                embed=make_embed(
                    "No LP history recorded today. It starts filling after the next ranked game."
                )
            )
            return
        embed = make_embed(summary_text(summarize(entries)), title=f"Today — {target.riot_id}")
        await ctx.respond(embed=embed)

    @discord.slash_command(guild_ids=GUILD_IDS, description="Graph stored LP history")
    @discord.option("server", description="Server", choices=SERVERS, required=False)
    @discord.option("username", description="League or Discord username (defaults to you)", required=False)
    @discord.option(
        "queue", description="Ranked queue", choices=["solo", "flex"], required=False
    )
    @discord.option(
        "days",
        int,
        description="Days to show",
        min_value=1,
        max_value=365,
        required=False,
    )
    async def lpgraph(self, ctx, server, username, queue="solo", days=30):
        """Handle lpgraph."""
        log_command(
            ctx,
            server=server,
            username=username,
            queue=queue,
            days=days,
        )
        await ctx.defer()
        target, state = await self._history_target(ctx, server, username)
        if state is None:
            return
        entries = state.history.get(_queue_id(queue), [])
        LOGGER.debug("/lpgraph: %d history entries for %s (queue=%s, days=%d)", len(entries), target.riot_id, queue, days)
        if not entries:
            await ctx.respond(
                embed=make_embed(
                    "No LP history recorded yet. It starts filling after the next ranked game."
                )
            )
            return
        chart = await asyncio.to_thread(build_lp_chart, entries, days=days)
        embed = make_embed(
            summary_text(summarize(entries)), title=f"LP History — {target.riot_id}"
        )
        if chart is None:
            embed.set_footer(text="Chart rendering is unavailable; showing the text summary.")
            await ctx.respond(embed=embed)
            return
        embed.set_image(url=f"attachment://{LP_CHART_FILENAME}")
        await ctx.respond(embed=embed, file=chart)

    @discord.slash_command(
        guild_ids=GUILD_IDS, description="Rank tracked accounts from stored snapshots"
    )
    @discord.option(
        "queue", description="Ranked queue", choices=["solo", "flex"], required=False
    )
    @discord.option(
        "refresh", bool, description="Refresh snapshots (owner only)", required=False
    )
    async def leaderboard(self, ctx, queue="solo", refresh=False):
        """Handle leaderboard."""
        log_command(ctx, queue=queue, refresh=refresh)
        if refresh and ctx.author.id != get_settings().discord_owner_id:
            await ctx.respond(
                embed=make_embed("Only the bot owner can refresh all ranks."),
                ephemeral=True,
            )
            return
        await ctx.defer()
        overrides = None
        if refresh:
            LOGGER.info("/leaderboard: refreshing all rank snapshots (requested by %s)", ctx.author)
            overrides, _failed = await asyncio.to_thread(fetch_all_rank_snapshots)
            LOGGER.debug("/leaderboard: refresh returned %d overrides, %d failed", len(overrides or {}), len(_failed or []))
        accounts, state = await asyncio.gather(
            asyncio.to_thread(load_accounts), asyncio.to_thread(load_tracker_state)
        )
        rows: list[LeaderboardRow] = leaderboard_rows(
            accounts, state, _queue_id(queue), rank_overrides=overrides
        )
        timestamps = [
            row.rank.updated_at for row in rows if row.rank and row.rank.updated_at
        ]
        oldest_snapshot = min(timestamps, default=None)

        def render_page(items, page, pages):
            """Render page."""
            return _leaderboard_embed(
                items, page, pages, oldest_snapshot=oldest_snapshot
            )

        paginator = Paginator(
            rows,
            author_id=ctx.author.id,
            render_page=render_page,
            page_size=LEADERBOARD_PAGE_SIZE,
        )
        view = paginator if paginator.max_page else None
        paginator.message = await ctx.respond(embed=paginator.render(), view=view)


def setup(bot: discord.Bot) -> None:
    """Register this command module with the bot."""
    bot.add_cog(RankingCommands(bot))

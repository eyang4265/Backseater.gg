"""Public /bestplayer leaderboard of tracked players' 180-day champion records."""

import asyncio
import logging

import discord
from discord.ext import commands

from .shared import GUILD_IDS, log_command
from ..best_player import QUEUE_CHOICES
from ..best_player_history import fetch_best_players
from ..paginator import Paginator
from ..render import make_embed

LOGGER = logging.getLogger(__name__)


def bestplayer_embed(rows, names, page=0, pages=1, min_games=10, queue="All Games", coverage=None):
    """Render concise filters and ten pairs with plain rank labels."""
    embed = make_embed(
        f"Last 180 days · {queue} · **{min_games}+ games per champion**",
        title="Best Player — Champion Win Rates",
    )
    if coverage is not None:
        if coverage.incomplete_accounts or coverage.unavailable_matches:
            embed.description += (f"\n**Partial history:** {coverage.incomplete_accounts} incomplete accounts; "
                                  f"{coverage.unavailable_matches} unavailable matches.")
    if not rows:
        embed.description += f"\nNo tracked player has at least {min_games} valid games on a champion yet."
        return embed
    embed.add_field(name="Player", value="\n".join(
        f"#{page * 10 + index + 1} {discord.utils.escape_markdown(names.get(row.puuid, 'Unknown player'))[:80]}"
        for index, row in enumerate(rows)), inline=True)
    embed.add_field(name="Champion", value="\n".join(
        discord.utils.escape_markdown(row.champion)[:80] for row in rows), inline=True)
    embed.add_field(name="Win rate · Record", value="\n".join(
        f"{row.win_rate:.1%} · {row.wins}W–{row.games-row.wins}L ({row.games} games)"
        for row in rows), inline=True)
    embed.set_footer(text=f"Page {page+1}/{pages}")
    return embed


class BestPlayerCommands(commands.Cog):
    """Fetch the last 180 days of tracked histories and show qualifying records."""

    def __init__(self):
        """Allow only one roster-wide scan at a time."""
        self._running = False

    async def _scan(self, message, min_games, queue):
        """Refresh a normal channel message so long scans survive webhook expiry."""
        status = None

        def record(done, total, matches):
            nonlocal status
            status = (done, total, matches)

        task = asyncio.create_task(asyncio.to_thread(fetch_best_players, min_games, queue, progress=record))
        shown = None
        while not task.done():
            finished, _ = await asyncio.wait({task}, timeout=5)
            if finished or status is None or shown == status:
                continue
            shown = status
            done, total, matches = status
            try:
                await message.edit(embed=make_embed(
                    f"Fetching last 180 days… {done}/{total} accounts, {matches} unique matches.\n"
                    f"{queue} · Minimum {min_games} games", title="Best Player — Scanning"))
            except discord.DiscordException:
                LOGGER.debug("Could not edit bestplayer scan progress", exc_info=True)
        return await task

    @discord.slash_command(guild_ids=GUILD_IDS, description="Rank tracked players' champion win rates over the last 180 days")
    @discord.option("min_games", int, description="Minimum games per champion (default 10)", min_value=1, required=False)
    @discord.option("queue", str, description="Queue filter (default All Games)", choices=QUEUE_CHOICES, required=False)
    async def bestplayer(self, ctx, min_games=10, queue="All Games"):
        """Acknowledge, show scan progress, then publish a paginated history ranking."""
        log_command(ctx, min_games=min_games, queue=queue)
        if self._running:
            await ctx.respond("A `/bestplayer` history scan is already running.", ephemeral=True)
            return
        self._running = True
        message = None
        try:
            await ctx.defer()
            reply = await ctx.respond(embed=make_embed(
                "Fetching tracked-player history…",
                title="Best Player — Scanning"))
            message = reply.channel.get_partial_message(reply.id)
            result = await self._scan(message, min_games, queue)
            paginator = Paginator(result.rows, author_id=ctx.author.id, page_size=10,
                render_page=lambda items, page, pages: bestplayer_embed(
                    items, result.names, page, pages, min_games, queue, result))
            paginator.message = message
            await message.edit(embed=paginator.render(), view=paginator if paginator.max_page else None,
                               allowed_mentions=discord.AllowedMentions.none())
        except Exception:
            LOGGER.exception("Could not finish bestplayer history scan")
            if message is not None:
                await message.edit(embed=make_embed("The history scan failed. Please try again.", title="Best Player"))
        finally:
            self._running = False


def setup(bot):
    """Register the public bestplayer command."""
    bot.add_cog(BestPlayerCommands())

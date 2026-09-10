"""The ``/tftmatchhistory`` command: a TFT counterpart to ``/matchhistory``."""

from __future__ import annotations

import asyncio
import logging

import discord
from discord.ext import commands

from ..announce import _TFT_QUEUE_NAMES, _tft_queue_name
from ..render import format_duration
from ..services.riot_api import RiotAPIError, get_client
from .shared import (
    GUILD_IDS,
    SERVERS,
    log_command,
    make_embed,
    riot_to_thread,
    set_tft_player_author,
    tft_not_found_embed,
    tft_target_for,
)

LOGGER = logging.getLogger(__name__)

_HISTORY_COUNT = 10
_FILTER_LOOKBACK = 40
_FETCH_BATCH = 5


def _queue_choices(_ctx: discord.AutocompleteContext) -> list[str]:
    """Human-readable TFT queue names offered by the ``queue`` filter."""
    return sorted(set(_TFT_QUEUE_NAMES.values()))


def _match_queue_name(info: dict) -> str:
    """Resolve a completed TFT match's mode label the way announcements do."""
    return _tft_queue_name(
        info.get("queue_id") or info.get("queueId"),
        info.get("tft_game_type") or info.get("tftGameType"),
    )


def _history_line(match: dict, puuid: str) -> tuple[str, bool] | None:
    """One compact history row for ``puuid`` plus whether it was a top-4 finish."""
    info = match.get("info", {})
    participant = next(
        (
            entry
            for entry in info.get("participants", []) or []
            if entry.get("puuid") == puuid
        ),
        None,
    )
    if participant is None:
        return None
    placement = int(participant.get("placement") or 0)
    if not placement:
        return None
    top_four = placement <= 4
    level = participant.get("level") or "?"
    eliminated = (
        participant.get("players_eliminated")
        or participant.get("playersEliminated")
        or 0
    )
    game_length = float(info.get("game_length") or info.get("gameLength") or 0)
    # Match-V1 `game_datetime` is already the game-end time; adding the game
    # length on top of it put this timestamp a whole game into the future.
    ended = int(int(info.get("game_datetime") or info.get("gameDatetime") or 0) / 1000)
    ago = f" · <t:{ended}:R>" if ended else ""
    medal = "🥇" if placement == 1 else ("✅" if top_four else "❌")
    return (
        f"{medal} **#{placement}** · Lvl {level} · {eliminated} elim · "
        f"{_match_queue_name(info)} ({format_duration(round(game_length))}){ago}",
        top_four,
    )


class TftMatchHistoryCommands(commands.Cog):
    """Expose a tracked TFT account's recent placements as a compact list."""

    def __init__(self, bot: discord.Bot) -> None:
        """Initialize the cog."""
        self.bot = bot

    @discord.slash_command(
        guild_ids=GUILD_IDS,
        description="Show a player's recent TFT placements",
    )
    @discord.option("server", description="Server", choices=SERVERS, required=False)
    @discord.option(
        "username",
        description="TFT Riot ID or Discord username (defaults to you)",
        required=False,
    )
    @discord.option(
        "queue",
        description="Filter by TFT queue, such as TFT Ranked or TFT Double Up",
        autocomplete=discord.utils.basic_autocomplete(_queue_choices),
        required=False,
    )
    async def tftmatchhistory(self, ctx, server, username, queue):
        """List recent TFT games for a target, mirroring ``/matchhistory``."""
        log_command(ctx, server=server, username=username, queue=queue)
        await ctx.defer()
        try:
            target = await tft_target_for(ctx, server, username)
        except RiotAPIError as error:
            await ctx.respond(embed=make_embed(f"Could not resolve TFT account: {error}"))
            return
        if target is None:
            await ctx.respond(embed=tft_not_found_embed(username, server, ctx=ctx))
            return

        client = get_client()
        try:
            match_ids = await riot_to_thread(
                client.tft_match_ids,
                target.puuid,
                target.server,
                count=_FILTER_LOOKBACK if queue else _HISTORY_COUNT,
            )
        except RiotAPIError as error:
            LOGGER.info("TFT history fetch failed for %s: %s", target.riot_id, error)
            await ctx.respond(
                embed=make_embed(f"Could not fetch TFT match history: {error}")
            )
            return
        if not match_ids:
            await ctx.respond(
                embed=make_embed(f"No TFT matches found for {target.riot_id}.")
            )
            return

        lines: list[str] = []
        wins = 0
        filtered_out = 0
        want = queue.casefold() if queue else None
        for start in range(0, len(match_ids), _FETCH_BATCH):
            if len(lines) >= _HISTORY_COUNT:
                break
            batch = match_ids[start : start + _FETCH_BATCH]
            fetched = await asyncio.gather(
                *(
                    riot_to_thread(client.tft_match, match_id, target.server)
                    for match_id in batch
                ),
                return_exceptions=True,
            )
            for match_id, result in zip(batch, fetched):
                if isinstance(result, Exception):
                    LOGGER.warning(
                        "Could not fetch TFT match %s for history: %s", match_id, result
                    )
                    continue
                info = result.get("info", {})
                if want and want not in _match_queue_name(info).casefold():
                    filtered_out += 1
                    continue
                row = _history_line(result, target.puuid)
                if row is None:
                    filtered_out += 1
                    continue
                line, top_four = row
                lines.append(line)
                wins += int(top_four)
                if len(lines) >= _HISTORY_COUNT:
                    break

        if not lines:
            details = [
                f"Player: {target.riot_id} ({target.server})",
                f"Matches checked: {len(match_ids)}",
            ]
            if queue:
                details.append(f"Queue filter: {queue}")
            if filtered_out:
                details.append(f"Filtered out: {filtered_out}")
            await ctx.respond(
                embed=make_embed(
                    "No TFT matches matched. Please try again.\n" + "\n".join(details)
                )
            )
            return

        losses = len(lines) - wins
        embed = make_embed(
            "\n\n".join(lines),
            title=f"TFT Match History — {target.riot_id} ({wins} top 4 · {losses} bottom 4)",
        )
        set_tft_player_author(embed, target, name=f"{target.riot_id}'s TFT History")
        await ctx.respond(embed=embed)
        LOGGER.info(
            "Sent /tftmatchhistory for %s (%d shown, %d filtered out)",
            target.riot_id,
            len(lines),
            filtered_out,
        )


def setup(bot: discord.Bot) -> None:
    """Register the TFT match-history command."""
    bot.add_cog(TftMatchHistoryCommands(bot))

"""Per-match analysis: /timeline."""

from __future__ import annotations

import asyncio
import logging

import discord
from discord.ext import commands

from ... import ddragon
from ...charts import (
    KILL_MAP_FILENAME,
    build_jungle_heatmap,
    build_kill_map,
    jungle_checkpoint_minutes,
    jungle_lane_involvement,
    jungle_kda_at,
    jungle_proximity_breakdown,
)
from ...emoji import champion_emoji, prefixed
from ...render import make_embed, outcome_color
from ...queues import queue_name
from ...services.riot_api import RiotAPIError, get_client
from ...timeline import (
    MatchTimeline,
    format_lane_lines,
    max_level_for_position,
    opponent_participant_id,
    participant_at_slot,
)
from ..shared import (
    GUILD_IDS,
    MATCH_POSITION_DESCRIPTION,
    SERVERS,
    Target,
    log_command,
    match_reference_index,
    not_found_embed,
    set_player_author,
    target_at_match_position,
    target_for,
)

LOGGER = logging.getLogger(__name__)

_NO_LANE_DATA = "No lane opponent found (ARAM/Arena or missing position data)."


def _first_blood_text(participant: dict) -> str:
    """Handle blood text."""
    if participant.get("firstBloodKill"):
        return "Yes (kill)"
    if participant.get("firstBloodAssist"):
        return "Yes (assist)"
    return "No"


class MatchCommands(commands.Cog):
    def __init__(self, bot: discord.Bot) -> None:
        """Initialize the instance."""
        self.bot = bot

    @discord.slash_command(
        guild_ids=GUILD_IDS,
        description="Solo kills/deaths and other stats for a match, pulled from its timeline",
    )
    @discord.option("server", description="Server", choices=SERVERS, required=False)
    @discord.option("username", description="League or Discord username (defaults to you)", required=False)
    @discord.option(
        "match_id",
        description="Match ID or recent-game number (1=latest); leave blank for the most recent game",
        required=False,
    )
    @discord.option(
        "position",
        int,
        description=MATCH_POSITION_DESCRIPTION,
        min_value=1,
        max_value=10,
        required=False,
    )
    async def timeline(self, ctx, server, username, match_id, position):
        """One player's box score for a match, plus timeline-derived stats."""
        log_command(
            ctx,
            server=server,
            username=username,
            match_id=match_id,
            position=position,
        )
        await ctx.defer()

        target = await target_for(ctx, server, username)
        if target is None:
            await ctx.respond(embed=not_found_embed(username, server, ctx=ctx))
            return
        if match_id and match_id.isdigit() and match_reference_index(match_id) is None:
            await ctx.respond(embed=make_embed("Recent-game numbers must be between 1 and 20."))
            return

        reference_index = match_reference_index(match_id)
        if reference_index is not None:
            selected_id = await self._recent_match_id(target, reference_index)
        else:
            selected_id = match_id or await self._latest_match_id(ctx, target)
        if selected_id is None:
            return

        client = get_client()
        try:
            match = await asyncio.to_thread(client.match, selected_id, target.server)
        except RiotAPIError as error:
            LOGGER.info("Match fetch failed for %s: %s", selected_id, error)
            await ctx.respond(
                embed=make_embed(f"Could not fetch match `{selected_id}`: {error}")
            )
            return

        participants = match.get("info", {}).get("participants", []) or []
        LOGGER.debug("Match %s has %d participants", selected_id, len(participants))

        subject = target
        if position is None:
            participant = next(
                (p for p in participants if p.get("puuid") == target.puuid), None
            )
            if participant is None:
                await ctx.respond(
                    embed=make_embed(
                        f"{target.riot_id} wasn't found in match `{selected_id}`."
                    )
                )
                return
        else:
            participant = participant_at_slot(match, position)
            if participant is None:
                await ctx.respond(
                    embed=make_embed(
                        f"Match `{selected_id}` has no player in position {position}."
                    )
                )
                return
            subject = target_at_match_position(match, position, target.server) or target
            LOGGER.debug("Reporting on position %d (%s) instead of %s", position, subject.riot_id, target.riot_id)

        try:
            payload = await asyncio.to_thread(
                client.match_timeline, selected_id, target.server
            )
        except RiotAPIError as error:
            LOGGER.info("Timeline fetch failed for %s: %s", selected_id, error)
            await ctx.respond(
                embed=make_embed(
                    f"Could not fetch the timeline for `{selected_id}`: {error}"
                )
            )
            return

        analysis = MatchTimeline(payload)
        embed = await asyncio.to_thread(
            self._build_embed, match, participants, participant, analysis, selected_id
        )
        set_player_author(embed, subject)

        kill_map = await asyncio.to_thread(build_kill_map, match, analysis, participant)
        if kill_map is None:
            LOGGER.debug("No kill map rendered for match %s", selected_id)
            await ctx.respond(embed=embed)
            return
        embed.set_image(url=f"attachment://{KILL_MAP_FILENAME}")
        LOGGER.info("Sent timeline stats for match %s", selected_id)
        await ctx.respond(embed=embed, file=kill_map)

    @staticmethod
    async def _latest_match_id(ctx, target: Target) -> str | None:
        """Handle match id."""
        try:
            match_ids = await asyncio.to_thread(
                get_client().match_ids, target.puuid, target.server, count=1
            )
        except RiotAPIError as error:
            await ctx.respond(embed=make_embed(f"Could not fetch match list: {error}"))
            return None
        if not match_ids:
            await ctx.respond(
                embed=make_embed(f"No matches found for {target.riot_id}.")
            )
            return None
        return match_ids[0]

    @staticmethod
    async def _recent_match_id(target: Target, index: int) -> str | None:
        """Resolve a 1-based user reference to a recent match ID."""
        match_ids = await asyncio.to_thread(
            get_client().match_ids, target.puuid, target.server, count=index + 1
        )
        return match_ids[index] if index < len(match_ids) else None

    @staticmethod
    def _build_embed(
        match, participants, participant, analysis, match_id
    ) -> discord.Embed:
        """Build embed."""
        participant_id = participant.get("participantId")
        solo = analysis.solo_kill_stats(participant_id)

        opponent_id = opponent_participant_id(match, participant)
        opponent = next(
            (p for p in participants if p.get("participantId") == opponent_id), None
        )

        catalog = ddragon.catalog()

        def champion_name(record) -> str:
            """Handle name."""
            champion = catalog.by_key(record.get("championId")) if catalog else None
            return champion.name if champion else record.get("championName", "Unknown")

        if opponent is None:
            lane_header, lane_body = "Lane Diff", _NO_LANE_DATA
        else:
            position = participant.get("teamPosition")

            cap = max_level_for_position(position)
            lane_header = (
                f"Lane vs {champion_name(opponent)} ({(position or '').title()})"
            )
            series = analysis.lane_diff_series(
                participant_id, opponent_id, max_level=cap
            )
            lane_body = format_lane_lines(series) or _NO_LANE_DATA

        outcome = "Victory" if participant.get("win") else "Defeat"
        cs = participant.get("totalMinionsKilled", 0) + participant.get(
            "neutralMinionsKilled", 0
        )

        embed = discord.Embed(
            title=f"Match Stats — {queue_name(match.get('info', {}).get('queueId'))} — {match_id}",
            description=(
                f"**{champion_name(participant)}** · {outcome} · "
                f"{participant.get('kills', 0)}/{participant.get('deaths', 0)}/"
                f"{participant.get('assists', 0)}"
            ),
            color=outcome_color(outcome),
        )
        embed.add_field(name=lane_header, value=lane_body, inline=False)
        embed.add_field(
            name="Combat",
            value=(
                f"**Solo Kills:** {solo.solo_kills}\n"
                f"**Solo Deaths:** {solo.solo_deaths}\n"
                f"**First Blood:** {_first_blood_text(participant)}\n"
                f"**Killing Spree:** {participant.get('largestKillingSpree', 0)}\n"
                f"**Multikill:** {participant.get('largestMultiKill', 0)}\n"
                f"**D/T/Q/P:** "
                f"{participant.get('doubleKills', 0)}/{participant.get('tripleKills', 0)}/"
                f"{participant.get('quadraKills', 0)}/{participant.get('pentaKills', 0)}"
            ),
            inline=True,
        )
        embed.add_field(
            name="Damage",
            value=(
                f"**To Champions:** {participant.get('totalDamageDealtToChampions', 0):,}\n"
                f"**Taken:** {participant.get('totalDamageTaken', 0):,}"
            ),
            inline=True,
        )
        embed.add_field(
            name="Economy",
            value=(
                f"**Gold:** {participant.get('goldEarned', 0):,}\n"
                f"**CS:** {cs}\n"
                f"**Vision:** {participant.get('visionScore', 0)}"
            ),
            inline=True,
        )
        return embed


def setup(bot: discord.Bot) -> None:
    """Register this command module with the bot."""
    bot.add_cog(MatchCommands(bot))

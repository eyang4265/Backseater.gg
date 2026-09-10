"""Owner-only commands: /data, /updateriotids, /reload, /test, /selftest."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import discord
from discord.ext import commands

from ...accounts import refresh_riot_ids
from ...announce import MatchAnnouncementView, TrackedPlayer, build_announcement_embed, format_match
from ...charts import MATPLOTLIB_AVAILABLE
from ...config import JSON_DIR
from ...queues import lobby_queue_name
from ...render import (
    add_team_columns,
    build_lobby_columns,
    format_duration,
    make_embed,
)
from ...services.riot_api import RiotAPIError, get_client
from ...routing import DEFAULT_PLATFORM
from ...store import load_accounts, read_json
from ..shared import GUILD_IDS, log_command, match_reference_index

LOGGER = logging.getLogger(__name__)


SELFTEST_PUUID = (
    "7ZZXIU4VCFrp089nPP9-GyeZOLOnyynl9qckvRrClsbbWWD4lQQKK-slhgJG-D2BjP8ElALjQKQrIg"
)
SELFTEST_SERVER = DEFAULT_PLATFORM
SELFTEST_NAME = "StealthSwifter"

SAMPLE_DIR = JSON_DIR / "samples"
SAMPLE_CHOICES = ["livegame", "match"]

_EMBED_DESCRIPTION_LIMIT = 4096


def _load_sample(filename: str) -> dict[str, Any] | None:
    """Load a canned fixture, returning None rather than raising."""
    payload = read_json(SAMPLE_DIR / filename, None)
    return payload if isinstance(payload, dict) else None


def _tracked_players_in(match: dict[str, Any]) -> list[TrackedPlayer]:
    """Every data.json account that played in a match — the same selection
    rule the live poller uses."""
    match_puuids = {
        participant.get("puuid")
        for participant in match.get("info", {}).get("participants", []) or []
    }
    return [
        TrackedPlayer(
            puuid=account.puuid,
            riot_id=account.riot_id,
            server=account.server,
        )
        for account in load_accounts().values()
        if account.puuid in match_puuids
    ]


class AdminCommands(commands.Cog):
    def __init__(self, bot: discord.Bot) -> None:
        """Initialize the instance."""
        self.bot = bot

    @discord.slash_command(
        guild_ids=GUILD_IDS, description="List tracked accounts (owner only)"
    )
    @commands.is_owner()
    async def data(self, ctx):
        """Every tracked Discord user and their Riot ID."""
        log_command(ctx)
        accounts = load_accounts()
        LOGGER.debug("Listing %d tracked accounts", len(accounts))
        listing = "\n".join(
            f"<@{account.discord_id}>: {account.riot_id}"
            for account in accounts.values()
        )
        await ctx.respond(
            embed=make_embed(listing or "No accounts are tracked.", title="Data"),
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @discord.slash_command(
        guild_ids=GUILD_IDS, description="Update every saved Riot ID (owner only)"
    )
    @commands.is_owner()
    async def updateriotids(self, ctx):
        """Re-resolve every tracked account's riot id and report what moved."""
        log_command(ctx)
        await ctx.defer(ephemeral=True)

        LOGGER.info("Refreshing Riot IDs for all tracked accounts")
        result = refresh_riot_ids()
        lines = [*result.changed, f"Unchanged: {result.unchanged}"]
        if result.failed:
            lines += ["Failed:", *result.failed]
        report = "\n".join(lines)

        LOGGER.info("Riot ID refresh: %s", report.replace("\n", "; "))
        await ctx.respond(
            embed=make_embed(
                report[:_EMBED_DESCRIPTION_LIMIT], title="Update Complete"
            ),
            ephemeral=True,
        )

    @discord.slash_command(
        guild_ids=GUILD_IDS, description="Alias for /updateriotids (owner only)"
    )
    @commands.is_owner()
    async def reload(self, ctx):
        """Handle reload."""
        await self.updateriotids(ctx)

    @discord.slash_command(
        guild_ids=GUILD_IDS, description="Connectivity check (owner only)"
    )
    @commands.is_owner()
    async def test(self, ctx):
        """Handle test."""
        log_command(ctx)
        LOGGER.debug("Connectivity check requested")
        await ctx.respond(embed=make_embed("Hi"))

    @discord.slash_command(
        guild_ids=GUILD_IDS,
        description="Run the match-announcement pipeline on demand (owner only)",
    )
    @commands.is_owner()
    @discord.option(
        "match_id",
        description=f"Match ID or recent-game number (1=latest); blank for {SELFTEST_NAME}'s latest match",
        required=False,
    )
    @discord.option(
        "sample",
        description="Use a canned fixture instead of live Riot data (ignores match_id)",
        required=False,
        choices=SAMPLE_CHOICES,
    )
    async def selftest(self, ctx, match_id, sample=None):
        """Exercise the announcement pipeline without waiting for the poller.

        ``sample`` swaps in a fixture from json/samples instead of calling the
        Riot API: ``livegame`` renders a lobby and ``match`` runs a match
        through the normal pipeline.
        """
        log_command(ctx, match_id=match_id, sample=sample)
        await ctx.defer()

        if match_id and match_id.isdigit() and match_reference_index(match_id) is None:
            await ctx.respond(embed=make_embed("Recent-game numbers must be between 1 and 20."))
            return

        handlers = {
            "livegame": self._sample_livegame,
            "match": self._sample_match,
        }
        handler = handlers.get(sample)
        if handler is not None:
            LOGGER.debug("Running selftest using canned sample %s", sample)
            await handler(ctx)
            return

        LOGGER.debug("Running selftest against live Riot data (match_id=%s)", match_id)
        await self._live_match(ctx, match_id)

    @staticmethod
    async def _sample_livegame(ctx) -> None:
        """Handle livegame."""
        game = _load_sample("sample_livegame.json")
        if game is None:
            await ctx.respond(
                embed=make_embed("Could not load json/samples/sample_livegame.json.")
            )
            return

        server = game.get("platformId") or DEFAULT_PLATFORM
        columns = build_lobby_columns(game, server)
        embed = discord.Embed(
            title=f"Live Game — {lobby_queue_name(game.get('gameQueueConfigId'))}",
            description=f"**Game Time:** {format_duration(game.get('gameLength', 0))}",
            color=discord.Color.gold(),
        )
        add_team_columns(embed, columns)
        embed.set_author(name="Sample Lobby (json/samples/sample_livegame.json)")
        await ctx.respond(embed=embed)

    async def _sample_match(self, ctx) -> None:
        """Handle match."""
        match = _load_sample("sample_match.json")
        if match is None or "info" not in match:
            await ctx.respond(
                embed=make_embed("Could not load json/samples/sample_match.json.")
            )
            return
        await self._respond_with_announcement(ctx, match, _tracked_players_in(match))

    async def _live_match(self, ctx, match_id: str | None) -> None:
        """Render a selected match; numeric references 1–20 count back from recent games."""
        client = get_client()
        try:
            reference_index = match_reference_index(match_id)
            selected_id = (
                await asyncio.to_thread(self._latest_selftest_match_id, reference_index or 0)
                if reference_index is not None
                else match_id or await asyncio.to_thread(self._latest_selftest_match_id, 0)
            )
        except RiotAPIError as error:
            await ctx.respond(
                embed=make_embed(f"Could not fetch a match to test with: {error}")
            )
            return

        if selected_id is None:
            await ctx.respond(embed=make_embed(f"No match found for {SELFTEST_NAME}."))
            return

        try:
            match = await asyncio.to_thread(client.match, selected_id, SELFTEST_SERVER)
        except RiotAPIError as error:
            await ctx.respond(
                embed=make_embed(f"Could not fetch match `{selected_id}`: {error}")
            )
            return

        if "info" not in match:
            await ctx.respond(
                embed=make_embed(f"Match `{selected_id}` returned no data.")
            )
            return

        players = _tracked_players_in(match)
        LOGGER.debug("Match %s has %d tracked players", selected_id, len(players))
        if not match_id and not any(
            player.puuid == SELFTEST_PUUID for player in players
        ):
            await ctx.respond(
                embed=make_embed(
                    f"{SELFTEST_NAME}'s latest match could not be matched to the configured PUUID."
                )
            )
            return

        await self._respond_with_announcement(ctx, match, players)

    @staticmethod
    def _latest_selftest_match_id(index: int = 0) -> str | None:
        """Handle selftest match id."""
        match_ids = get_client().match_ids(
            SELFTEST_PUUID, SELFTEST_SERVER, count=index + 1
        )
        return match_ids[index] if index < len(match_ids) else None

    @staticmethod
    async def _respond_with_announcement(
        ctx,
        match: dict[str, Any],
        players: list[TrackedPlayer],
    ) -> None:
        """Render a match exactly as the poller would, and reply with it.

        Recency and ranked-queue gates are off so any match can be rendered.
        """
        announcement = format_match(
            match,
            players,
            require_finished=False,
            require_ranked_queue=False,
        )
        if announcement is None:
            await ctx.respond(
                embed=make_embed(
                    "No tracked players were found in that match, so there is nothing to announce."
                )
            )
            return

        embed, chart = await build_announcement_embed(announcement)
        LOGGER.info("Rendered selftest announcement for match %s", match.get("metadata", {}).get("matchId"))

        footer = []
        if not MATPLOTLIB_AVAILABLE:
            footer.append("Damage graph unavailable (matplotlib not installed).")
        if footer:
            embed.set_footer(text=" | ".join(footer))

        await ctx.respond(
            embed=embed,
            file=chart,
            view=MatchAnnouncementView(announcement),
        )


def setup(bot: discord.Bot) -> None:
    """Register this command module with the bot."""
    bot.add_cog(AdminCommands(bot))

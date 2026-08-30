"""Champion mastery: /mastery, plus its paginated view."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

import discord
from discord.ext import commands

from .. import ddragon
from ..emoji import champion_emoji, prefixed
from ..paginator import Paginator
from ..render import make_embed, relative_time
from ..services.riot_api import RiotAPIError, get_client
from .shared import (
    GUILD_IDS,
    SERVERS,
    Target,
    log_command,
    not_found_embed,
    set_player_author,
    target_for,
)

LOGGER = logging.getLogger(__name__)

PAGE_SIZE = 10
_VIEW_TIMEOUT_SECONDS = 120


@dataclass(frozen=True)
class MasteryEntry:
    """One champion's mastery, joined against the Data Dragon catalog."""

    name: str
    champion: ddragon.Champion | None
    level: int
    points: int
    last_played_ms: int
    chest_granted: bool
    tokens: int

    @classmethod
    def from_api(
        cls, entry: dict[str, Any], catalog: ddragon.ChampionCatalog | None
    ) -> "MasteryEntry":
        """Handle api."""
        champion_id = entry.get("championId")
        champion = catalog.by_key(champion_id) if catalog else None
        return cls(
            name=champion.name if champion else f"Champion {champion_id}",
            champion=champion,
            level=entry.get("championLevel", 0),
            points=entry.get("championPoints", 0),
            last_played_ms=entry.get("lastPlayTime", 0),
            chest_granted=entry.get("chestGranted", False),
            tokens=entry.get("tokensEarned", 0),
        )


class MasteryPaginator(Paginator):
    """A player's full mastery list, ten champions per page."""

    def __init__(
        self,
        riot_id: str,
        entries: list[MasteryEntry],
        author_id: int,
        profile_icon_url: str | None = None,
        server: str = "Unknown",
    ) -> None:
        """Initialize the instance."""
        self.riot_id = riot_id
        self.entries = entries
        self.profile_icon_url = profile_icon_url
        self.server = server
        super().__init__(
            entries,
            author_id=author_id,
            render_page=self._render_page,
            page_size=PAGE_SIZE,
            timeout=_VIEW_TIMEOUT_SECONDS,
        )

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Allow anyone to page through mastery results, not just the invoker."""
        return True

    @property
    def _totals_text(self) -> str:
        """Handle text."""
        champions = len(self.entries)
        return (
            f"Champions: {champions} · "
            f"Mastery Levels: {sum(entry.level for entry in self.entries)} · "
            f"Mastery Points: {sum(entry.points for entry in self.entries):,} · "
            f"Chests: {sum(1 for entry in self.entries if entry.chest_granted)}/{champions}"
        )

    def _render_page(self, page_entries, page: int, pages: int) -> discord.Embed:
        """Render page."""
        champion_lines = [
            f"{prefixed(champion_emoji(entry.champion, name=entry.name), f'**{entry.name}**')}"
            f" – {entry.points:,}"
            for entry in page_entries
        ]
        played_lines = [relative_time(entry.last_played_ms) for entry in page_entries]
        chest_lines = [
            "✅ Mastered"
            if entry.chest_granted
            else f"▫️ {entry.tokens} Token{'s' if entry.tokens != 1 else ''}"
            for entry in page_entries
        ]

        embed = make_embed("The champions with the most mastery points are:")
        embed.set_author(
            name=f"Champion Mastery: {self.riot_id}", icon_url=self.profile_icon_url
        )
        embed.add_field(
            name="Champion/Points", value="\n".join(champion_lines) or "—", inline=True
        )
        embed.add_field(
            name="Last Played", value="\n".join(played_lines) or "—", inline=True
        )
        embed.add_field(
            name="Chest/Status", value="\n".join(chest_lines) or "—", inline=True
        )
        embed.add_field(name="\u200b", value=self._totals_text, inline=False)
        embed.set_footer(text=f"Page {page + 1}/{pages}")
        return embed


class MasteryCommands(commands.Cog):
    def __init__(self, bot: discord.Bot) -> None:
        """Initialize the instance."""
        self.bot = bot

    @discord.slash_command(guild_ids=GUILD_IDS, description="Player's Champion Mastery")
    @discord.option("server", description="Server", choices=SERVERS, required=False)
    @discord.option("username", description="League or Discord username (defaults to you)", required=False)
    @discord.option("champion", description="Champion", required=False)
    async def mastery(self, ctx, server, username, champion):
        """Every champion's mastery, or one champion's detail when named."""
        log_command(
            ctx, server=server, username=username, champion=champion
        )
        await ctx.defer()

        target = await target_for(ctx, server, username)
        if target is None:
            await ctx.respond(embed=not_found_embed(username, server, ctx=ctx))
            return

        if champion:
            await self._respond_single_champion(ctx, target, champion)
        else:
            await self._respond_full_list(ctx, target)

    @staticmethod
    async def _respond_full_list(ctx, target: Target) -> None:
        """Handle full list."""
        try:
            masteries = await asyncio.to_thread(
                get_client().champion_masteries, target.puuid, target.server
            )
        except RiotAPIError as error:
            LOGGER.warning("Could not fetch masteries for %s: %s", target.puuid, error)
            await ctx.respond(
                embed=make_embed(
                    f"Could not fetch champion mastery for {target.riot_id}."
                )
            )
            return

        if not masteries:
            LOGGER.info("/mastery: %s has no champion mastery data", target.riot_id)
            await ctx.respond(
                embed=make_embed(f"{target.riot_id} has no champion mastery data!")
            )
            return
        LOGGER.debug("/mastery: %d champion entries for %s", len(masteries), target.riot_id)

        catalog = await asyncio.to_thread(ddragon.catalog)
        entries = [
            MasteryEntry.from_api(entry, catalog)
            for entry in sorted(
                masteries, key=lambda m: m.get("championPoints", 0), reverse=True
            )
        ]

        paginator = MasteryPaginator(
            target.riot_id,
            entries,
            ctx.author.id,
            target.icon_url,
            target.server,
        )
        paginator.message = await ctx.respond(embed=paginator.render(), view=paginator)

    @staticmethod
    async def _respond_single_champion(
        ctx, target: Target, champion_query: str
    ) -> None:
        """Handle single champion."""
        catalog = await asyncio.to_thread(ddragon.catalog)
        found = catalog.by_query(champion_query) if catalog else None
        if found is None:
            LOGGER.info("/mastery could not resolve champion query %r", champion_query)
            await ctx.respond(
                embed=make_embed(
                    f"{champion_query} is not a champion or is misspelled!"
                )
            )
            return

        try:
            entry = await asyncio.to_thread(
                get_client().champion_mastery, target.puuid, target.server, found.key
            )
        except RiotAPIError as error:
            LOGGER.warning(
                "Could not fetch mastery for %s on %s: %s",
                target.puuid,
                found.name,
                error,
            )
            await ctx.respond(
                embed=make_embed(
                    f"No mastery found for {found.name} for {target.riot_id}."
                )
            )
            return

        if entry is None:
            LOGGER.info("/mastery: %s has never played %s", target.riot_id, found.name)
            await ctx.respond(
                embed=make_embed(f"{target.riot_id} has never played {found.name}!")
            )
            return

        display = prefixed(champion_emoji(found), found.name)
        description = "\n".join(
            [
                f"Champion: {display}",
                f"Champion Level: {entry.get('championLevel', 0)}",
                f"Champion Points: {entry.get('championPoints', 0):,}",
                f"Last Played: <t:{int(entry.get('lastPlayTime', 0) / 1000)}:R>",
                f"Champion Points Since Last Level: {entry.get('championPointsSinceLastLevel', 0)}",
                f"Champion Points Until Next Level: {entry.get('championPointsUntilNextLevel', 0)}",
                f"Mark Required For Next Level: {entry.get('markRequiredForNextLevel', 0)}",
                f"Tokens Earned: {entry.get('tokensEarned', 0)}",
                f"Champion Season Milestone: {entry.get('championSeasonMilestone', 0)}",
            ]
        )

        embed = make_embed(description)
        set_player_author(embed, target, name=f"Champion Mastery: {target.riot_id}")
        await ctx.respond(embed=embed)


def setup(bot: discord.Bot) -> None:
    """Register this command module with the bot."""
    bot.add_cog(MasteryCommands(bot))

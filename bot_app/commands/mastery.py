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
from ..render import make_embed, profile_author_icon, relative_time
from ..services.riot_api import RiotAPIError, get_client
from .shared import GUILD_IDS, SERVERS, Target, log_command, not_found_embed, resolve_target, set_player_author

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
    def from_api(cls, entry: dict[str, Any], catalog: ddragon.ChampionCatalog | None) -> "MasteryEntry":
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


class MasteryPaginator(discord.ui.View):
    """A player's full mastery list, ten champions per page."""

    def __init__(
        self,
        riot_id: str,
        entries: list[MasteryEntry],
        author_id: int,
        profile_icon_url: str | None = None,
    ) -> None:
        super().__init__(timeout=_VIEW_TIMEOUT_SECONDS)
        self.riot_id = riot_id
        self.entries = entries
        self.author_id = author_id
        self.profile_icon_url = profile_icon_url
        self.page = 0
        self.max_page = max((len(entries) - 1) // PAGE_SIZE, 0)
        self.message: discord.Message | None = None
        self._sync_buttons()

    @property
    def _totals_text(self) -> str:
        champions = len(self.entries)
        return (
            f"Champions: {champions} · "
            f"Mastery Levels: {sum(entry.level for entry in self.entries)} · "
            f"Mastery Points: {sum(entry.points for entry in self.entries):,} · "
            f"Chests: {sum(1 for entry in self.entries if entry.chest_granted)}/{champions}"
        )

    def _sync_buttons(self) -> None:
        at_start = self.page <= 0
        at_end = self.page >= self.max_page
        self.first_button.disabled = at_start
        self.previous_button.disabled = at_start
        self.next_button.disabled = at_end
        self.last_button.disabled = at_end

    def render(self) -> discord.Embed:
        start = self.page * PAGE_SIZE
        page_entries = self.entries[start : start + PAGE_SIZE]

        # Three inline fields act as columns: unlike a monospace code block
        # they render custom emoji, and Discord aligns them without padding.
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
        embed.set_author(name=f"Champion Mastery: {self.riot_id}", icon_url=self.profile_icon_url)
        embed.add_field(name="Champion/Points", value="\n".join(champion_lines) or "—", inline=True)
        embed.add_field(name="Last Played", value="\n".join(played_lines) or "—", inline=True)
        embed.add_field(name="Chest/Status", value="\n".join(chest_lines) or "—", inline=True)
        embed.add_field(name="​", value=self._totals_text, inline=False)
        embed.set_footer(text=f"Page {self.page + 1}/{self.max_page + 1}")
        return embed

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "This isn't your mastery list! Run /mastery yourself to page through your own.",
                ephemeral=True,
            )
            return False
        return True

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                LOGGER.debug("Could not disable a timed-out mastery view", exc_info=True)

    async def _go_to(self, page: int, interaction: discord.Interaction) -> None:
        self.page = min(max(page, 0), self.max_page)
        self._sync_buttons()
        await interaction.response.edit_message(embed=self.render(), view=self)

    @discord.ui.button(label="« First", style=discord.ButtonStyle.secondary)
    async def first_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        await self._go_to(0, interaction)

    @discord.ui.button(label="◀ Prev", style=discord.ButtonStyle.secondary)
    async def previous_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        await self._go_to(self.page - 1, interaction)

    @discord.ui.button(label="Next ▶", style=discord.ButtonStyle.secondary)
    async def next_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        await self._go_to(self.page + 1, interaction)

    @discord.ui.button(label="Last »", style=discord.ButtonStyle.secondary)
    async def last_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        await self._go_to(self.max_page, interaction)


class MasteryCommands(commands.Cog):
    def __init__(self, bot: discord.Bot) -> None:
        self.bot = bot

    @discord.slash_command(guild_ids=GUILD_IDS, description="Player's Champion Mastery")
    @discord.option("server", description="Server", choices=SERVERS, required=False)
    @discord.option("summoner", description="Game Name", required=False)
    @discord.option("tag", description="Tagline", required=False)
    @discord.option("champion", description="Champion", required=False)
    @discord.option("user", description="User", required=False)
    async def mastery(self, ctx, server, summoner, tag, champion, user):
        """Every champion's mastery, or one champion's detail when named."""
        log_command(ctx, server=server, summoner=summoner, tag=tag, champion=champion, user=user)
        await ctx.defer()

        target = resolve_target(ctx, server, summoner, tag, user)
        if target is None:
            await ctx.respond(embed=not_found_embed(summoner, tag, server))
            return

        if champion:
            await self._respond_single_champion(ctx, target, champion)
        else:
            await self._respond_full_list(ctx, target)

    @staticmethod
    async def _respond_full_list(ctx, target: Target) -> None:
        try:
            masteries = await asyncio.to_thread(
                get_client().champion_masteries, target.puuid, target.server
            )
        except RiotAPIError as error:
            LOGGER.warning("Could not fetch masteries for %s: %s", target.puuid, error)
            await ctx.respond(
                embed=make_embed(f"Could not fetch champion mastery for {target.riot_id}.")
            )
            return

        if not masteries:
            await ctx.respond(embed=make_embed(f"{target.riot_id} has no champion mastery data!"))
            return

        catalog = ddragon.catalog()
        entries = [
            MasteryEntry.from_api(entry, catalog)
            for entry in sorted(masteries, key=lambda m: m.get("championPoints", 0), reverse=True)
        ]

        paginator = MasteryPaginator(
            target.riot_id, entries, ctx.author.id, profile_author_icon(target.puuid, target.server)
        )
        paginator.message = await ctx.respond(embed=paginator.render(), view=paginator)

    @staticmethod
    async def _respond_single_champion(ctx, target: Target, champion_query: str) -> None:
        catalog = ddragon.catalog()
        found = catalog.by_query(champion_query) if catalog else None
        if found is None:
            await ctx.respond(
                embed=make_embed(f"{champion_query} is not a champion or is misspelled!")
            )
            return

        try:
            entry = await asyncio.to_thread(
                get_client().champion_mastery, target.puuid, target.server, found.key
            )
        except RiotAPIError as error:
            LOGGER.warning("Could not fetch mastery for %s on %s: %s", target.puuid, found.name, error)
            await ctx.respond(
                embed=make_embed(f"No mastery found for {found.name} for {target.riot_id}.")
            )
            return

        if entry is None:
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
    bot.add_cog(MasteryCommands(bot))

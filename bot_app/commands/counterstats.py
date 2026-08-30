"""The public ``/counterstats`` champion matchup command."""

from __future__ import annotations

import asyncio
import logging

import discord
from discord.ext import commands

from .. import ddragon, emoji
from ..champstats import ALL_GAMES, ALL_ROLES, QUEUE_SCOPES
from ..config import JSON_DIR
from ..counterstats import CounterStatsReport, aggregate
from ..match_cache import MatchCache
from ..render import make_embed
from ..services.riot_api import RiotAPIError, get_client
from .shared import GUILD_IDS, SERVERS, log_command, not_found_embed, target_for

LOGGER = logging.getLogger(__name__)
_CACHE_PATH = JSON_DIR / "matches.sqlite"
_SCAN_PAGE_SIZE = 100
_MATCHUP_PAGE_SIZE = 15
_ROLES = ("Top", "Jungle", "Mid", "ADC", "Support")


def _counter_page_count(report: CounterStatsReport) -> int:
    """Return the number of Discord-safe matchup pages."""
    return max((len(report.counters) - 1) // _MATCHUP_PAGE_SIZE + 1, 1)


def _counter_embed(report: CounterStatsReport, player_name: str, page: int = 0) -> discord.Embed:
    """Render one Discord-safe page of aligned matchup columns."""
    embed = make_embed(
        f"{report.queue_scope} · Player: {report.role} · Enemy: {report.enemy_role} · {report.games} games · {report.wins}W–{report.losses}L · {report.win_rate:.1%} WR",
        title=f"{report.champion} Counter Stats — {player_name}",
    )
    if report.counters:
        start = page * _MATCHUP_PAGE_SIZE
        rows = report.counters[start : start + _MATCHUP_PAGE_SIZE]
        embed.add_field(
            name="Enemy Champion",
            value="\n".join(
                emoji.prefixed(emoji.champion_emoji(None, name=row.champion), row.champion)
                for row in rows
            ),
            inline=True,
        )
        embed.add_field(name="Record", value="\n".join(f"{row.wins}W–{row.losses}L ({row.games})" for row in rows), inline=True)
        embed.add_field(name="Win Rate", value="\n".join(f"{row.win_rate:.1%}" for row in rows), inline=True)
    else:
        embed.description += "\n\nNo matching enemy champions were found in the available history."
    footer = "Each eligible game counts once against every champion on the opposing team."
    if report.usage_filter:
        footer += " Showing matchups above 1% usage."
    if report.counters and _counter_page_count(report) > 1:
        footer += f" Page {page + 1}/{_counter_page_count(report)}."
    embed.set_footer(text=footer)
    return embed


class CounterStatsView(discord.ui.View):
    """Author-scoped role buttons that never expire for matchup tables."""

    def __init__(self, payloads, puuid: str, champion: str, queue_scope: str, player_role: str, usage_filter: bool, player_name: str, author_id: int) -> None:
        super().__init__(timeout=None)
        self.payloads = payloads
        self.champion = champion
        self.queue_scope = queue_scope
        self.player_name = player_name
        self.author_id = author_id
        self.puuid = puuid
        self.player_role = player_role
        self.usage_filter = usage_filter
        self.enemy_role = ALL_ROLES
        self.page = 0
        self._buttons: dict[str, discord.ui.Button] = {}
        self._previous = discord.ui.Button(label="Previous", style=discord.ButtonStyle.secondary, custom_id="counterstats:previous")
        self._next = discord.ui.Button(label="Next", style=discord.ButtonStyle.secondary, custom_id="counterstats:next")
        self._previous.callback = self._go_previous
        self._next.callback = self._go_next

    def add_role_buttons(self) -> None:
        """Add the five role controls."""
        for role in _ROLES:
            button = discord.ui.Button(label=role, style=discord.ButtonStyle.primary, custom_id=f"counterstats:{role.casefold()}")
            button.callback = self._select_role(role)
            self._buttons[role] = button
            self.add_item(button)
        self._refresh()

    def _select_role(self, role: str):
        async def callback(interaction: discord.Interaction) -> None:
            if interaction.user.id != self.author_id:
                await interaction.response.send_message("Only the command author can use these controls.", ephemeral=True)
                return
            await interaction.response.defer()
            self.enemy_role = role
            self.page = 0
            self._refresh()
            report = self._report(enemy_role=role)
            await interaction.edit_original_response(embed=_counter_embed(report, self.player_name, self.page), view=self)
        return callback

    async def _check(self, interaction: discord.Interaction) -> bool:
        """Authorize and acknowledge a component interaction."""
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("Only the command author can use these controls.", ephemeral=True)
            return False
        await interaction.response.defer()
        return True

    async def _go_previous(self, interaction: discord.Interaction) -> None:
        """Show the previous matchup page."""
        if not await self._check(interaction):
            return
        self.page = max(self.page - 1, 0)
        self._refresh()
        report = self._report()
        await interaction.edit_original_response(embed=_counter_embed(report, self.player_name, self.page), view=self)

    async def _go_next(self, interaction: discord.Interaction) -> None:
        """Show the next matchup page."""
        if not await self._check(interaction):
            return
        report = self._report()
        self.page = min(self.page + 1, _counter_page_count(report) - 1)
        self._refresh()
        await interaction.edit_original_response(embed=_counter_embed(report, self.player_name, self.page), view=self)

    def _refresh(self) -> None:
        report = self._report()
        for role, button in self._buttons.items():
            button.disabled = role == self.enemy_role
        pages = _counter_page_count(report)
        self._previous.disabled = self.page <= 0
        self._next.disabled = self.page >= pages - 1
        if pages > 1:
            if self._previous not in self.children:
                self.add_item(self._previous)
                self.add_item(self._next)
        elif self._previous in self.children:
            self.remove_item(self._previous)
            self.remove_item(self._next)

    def _report(self, *, enemy_role: str | None = None) -> CounterStatsReport:
        """Recompute the current player-role and enemy-role view."""
        return aggregate(
            self.payloads,
            self.puuid,
            self.champion,
            self.queue_scope,
            self.player_role,
            enemy_role=self.enemy_role if enemy_role is None else enemy_role,
            min_usage_rate=0.01 if self.usage_filter else 0.0,
        )

    async def on_timeout(self) -> None:
        for button in self._buttons.values():
            button.disabled = True


def _load_payloads(puuid: str, server: str, *, scan_riot: bool) -> list[dict]:
    """Load cached matches and optionally append the paginated Riot history."""
    cache = MatchCache(_CACHE_PATH)
    try:
        payloads = [
            payload
            for payload in cache.iter_match_payloads()
            if any(isinstance(row, dict) and row.get("puuid") == puuid for row in (payload.get("info", {}).get("participants", []) if isinstance(payload, dict) and isinstance(payload.get("info"), dict) else []))
        ]
        if scan_riot:
            client = get_client()
            start = 0
            seen: set[str] = set()
            while True:
                ids = client.match_ids(puuid, server, start=start, count=_SCAN_PAGE_SIZE)
                new_ids = [match_id for match_id in ids if match_id not in seen]
                if not new_ids:
                    break
                seen.update(new_ids)
                for match_id in new_ids:
                    try:
                        payloads.append(client.match(match_id, server))
                    except RiotAPIError:
                        LOGGER.warning("Could not scan counterstats match %s", match_id)
                start += len(ids)
                if len(ids) < _SCAN_PAGE_SIZE:
                    break
        return payloads
    finally:
        cache.close()


class CounterStatsCommands(commands.Cog):
    """Register the /counterstats command."""

    def __init__(self, bot: discord.Bot) -> None:
        self.bot = bot
        self._scan_tasks: set[asyncio.Task] = set()

    async def _finish_scan(self, message, view: CounterStatsView, puuid: str, server: str) -> None:
        """Refresh the visible matchup table after the Riot history scan."""
        try:
            view.payloads = await asyncio.to_thread(_load_payloads, puuid, server, scan_riot=True)
            report = view._report()
            view.page = min(view.page, _counter_page_count(report) - 1)
            view._refresh()
            await message.edit(embed=_counter_embed(report, view.player_name, view.page), view=view)
        except Exception:
            LOGGER.exception("Could not finish /counterstats Riot history scan")

    @discord.slash_command(guild_ids=GUILD_IDS, description="Show champion win rates against every enemy champion")
    @discord.option("champion", description="Champion name", required=True)
    @discord.option("queue", description="Queue scope", choices=QUEUE_SCOPES, required=False)
    @discord.option("role", description="Your champion's role", choices=_ROLES, required=True)
    @discord.option("filter", bool, description="Only show matchups above 1% usage", required=False, default=False)
    @discord.option("server", description="Preferred lookup platform", choices=SERVERS, required=False)
    @discord.option("username", description="League or Discord username (defaults to you)", required=False)
    async def counterstats(self, ctx, champion, role, queue, filter: bool, server, username):
        """Show a champion's win rate against enemy champions in the selected role, with role buttons."""
        queue = queue or ALL_GAMES
        log_command(ctx, champion=champion, queue=queue, role=role, filter=filter, server=server, username=username)
        await ctx.defer()
        target = await target_for(ctx, server, username, include_icon=False)
        if target is None:
            await ctx.respond(embed=not_found_embed(username, server, ctx=ctx))
            return
        found = await asyncio.to_thread(lambda: ddragon.catalog().by_query(champion) if ddragon.catalog() else None)
        if found is None:
            await ctx.respond(embed=make_embed(f"I could not resolve `{champion}` as a champion."))
            return
        payloads = await asyncio.to_thread(_load_payloads, target.puuid, target.server, scan_riot=False)
        report = aggregate(payloads, target.puuid, found.name, queue, role, min_usage_rate=0.01 if filter else 0.0)
        view = CounterStatsView(payloads, target.puuid, found.name, queue, role, bool(filter), target.riot_id, ctx.author.id)
        view.add_role_buttons()
        embed = _counter_embed(report, target.riot_id, view.page)
        embed.description += "\n\nScanning Riot match history; this message will refresh when it finishes."
        message = await ctx.respond(embed=embed, view=view)
        if message is not None:
            task = asyncio.create_task(self._finish_scan(message, view, target.puuid, target.server))
            self._scan_tasks.add(task)
            task.add_done_callback(self._scan_tasks.discard)


def setup(bot: discord.Bot) -> None:
    """Register this command module with the bot."""
    bot.add_cog(CounterStatsCommands(bot))

"""The public ``/counterstats`` champion matchup command."""

from __future__ import annotations

import asyncio
import logging

import discord
from discord.ext import commands

from .. import ddragon, emoji
from ..champstats import (
    ALL_GAMES,
    ALL_ROLES,
    QUEUE_SCOPES,
    ROLE_POSITION_IDS,
    queue_ids_for_scope,
)
from ..config import DATA_DIR
from ..counterstats import CounterStatsReport, aggregate
from ..match_cache import MatchCache
from ..render import make_embed
from ..services.riot_api import RiotAPIError, get_client
from .shared import (
    GUILD_IDS,
    MATCH_POSITION_DESCRIPTION,
    SERVERS,
    log_command,
    not_found_embed,
    remember_view_state,
    target_at_latest_match_position,
    target_for,
)

LOGGER = logging.getLogger(__name__)
VIEW_KIND = "counterstats"
_CACHE_PATH = DATA_DIR / "matches.sqlite"
_SCAN_PAGE_SIZE = 100
_MATCHUP_PAGE_SIZE = 15
_ROLES = ("Top", "Jungle", "Mid", "ADC", "Support")


def _counter_page_count(report: CounterStatsReport) -> int:
    """Return the number of Discord-safe matchup pages."""
    return max((len(report.counters) - 1) // _MATCHUP_PAGE_SIZE + 1, 1)


def _counter_embed(report: CounterStatsReport, player_name: str, page: int = 0) -> discord.Embed:
    """Render one Discord-safe page of aligned matchup columns."""
    result_label = "15m gold leads" if report.laning else "games"
    summary = (
        f"{report.queue_scope} · Player: {report.role} · Enemy: {report.enemy_role} · "
        f"{report.games} {result_label} · {report.wins}W–{report.losses}L · "
        f"{report.win_rate:.1%} WR"
    )
    embed = make_embed(
        summary,
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
    if report.laning:
        footer += (
            " Wins use total gold versus the direct role opponent at 15:00; "
            "ties and missing timelines are excluded."
        )
    if report.usage_filter:
        footer += " Showing matchups above 1% usage."
    if report.counters and _counter_page_count(report) > 1:
        footer += f" Page {page + 1}/{_counter_page_count(report)}."
    embed.set_footer(text=footer)
    return embed


class CounterStatsView(discord.ui.View):
    """Shared role and page buttons that never expire for matchup tables."""

    def __init__(
        self, payloads, puuid: str, champion: str, queue_scope: str,
        player_role: str, usage_filter: bool, player_name: str, author_id: int,
        *, timelines=None, laning: bool = False, server: str = "NA1",
        enemy_role: str = ALL_ROLES, page: int = 0,
    ) -> None:
        super().__init__(timeout=None)
        self.payloads = payloads or []
        self._loaded = payloads is not None
        self.champion = champion
        self.queue_scope = queue_scope
        self.player_name = player_name
        self.author_id = author_id
        self.puuid = puuid
        self.player_role = player_role
        self.usage_filter = usage_filter
        self.timelines = timelines or {}
        self.laning = laning
        self.server = server
        self.enemy_role = enemy_role if enemy_role in {ALL_ROLES, *_ROLES} else ALL_ROLES
        self.page = max(int(page), 0)
        self._buttons: dict[str, discord.ui.Button] = {}
        self._previous = discord.ui.Button(label="Previous", style=discord.ButtonStyle.secondary, custom_id="counterstats:previous")
        self._next = discord.ui.Button(label="Next", style=discord.ButtonStyle.secondary, custom_id="counterstats:next")
        self._previous.callback = self._go_previous
        self._next.callback = self._go_next

    def state_payload(self) -> dict:
        """Persist query inputs, never the potentially large match payloads."""
        return {
            "puuid": self.puuid,
            "server": self.server,
            "champion": self.champion,
            "queue_scope": self.queue_scope,
            "player_role": self.player_role,
            "usage_filter": self.usage_filter,
            "player_name": self.player_name,
            "author_id": self.author_id,
            "laning": self.laning,
            "enemy_role": self.enemy_role,
            "page": self.page,
        }

    async def _ensure_loaded(self) -> None:
        """Reload cached inputs lazily after a process restart."""
        if self._loaded:
            return
        self.payloads = await asyncio.to_thread(
            _load_payloads, self.puuid, self.server, scan_riot=False
        )
        if self.laning:
            self.timelines = await asyncio.to_thread(
                _load_timelines,
                self.payloads,
                self.puuid,
                self.champion,
                self.queue_scope,
                self.player_role,
                self.server,
                fetch_missing=False,
            )
        self._loaded = True

    async def _remember(self, interaction: discord.Interaction) -> None:
        await remember_view_state(interaction.message, VIEW_KIND, self.state_payload())

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
            await interaction.response.defer()
            await self._ensure_loaded()
            self.enemy_role = role
            self.page = 0
            self._refresh()
            report = self._report(enemy_role=role)
            await interaction.edit_original_response(embed=_counter_embed(report, self.player_name, self.page), view=self)
            await self._remember(interaction)
        return callback

    async def _acknowledge(self, interaction: discord.Interaction) -> None:
        """Acknowledge a reader's click before loading cached history."""
        await interaction.response.defer()

    async def _go_previous(self, interaction: discord.Interaction) -> None:
        """Show the previous matchup page."""
        await self._acknowledge(interaction)
        await self._ensure_loaded()
        self.page = max(self.page - 1, 0)
        self._refresh()
        report = self._report()
        await interaction.edit_original_response(embed=_counter_embed(report, self.player_name, self.page), view=self)
        await self._remember(interaction)

    async def _go_next(self, interaction: discord.Interaction) -> None:
        """Show the next matchup page."""
        await self._acknowledge(interaction)
        await self._ensure_loaded()
        report = self._report()
        self.page = min(self.page + 1, _counter_page_count(report) - 1)
        self._refresh()
        await interaction.edit_original_response(embed=_counter_embed(report, self.player_name, self.page), view=self)
        await self._remember(interaction)

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
            timelines=self.timelines,
            laning=self.laning,
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


def _load_timelines(
    payloads: list[dict], puuid: str, champion: str, queue_scope: str, role: str,
    server: str, *, fetch_missing: bool,
) -> dict[str, dict]:
    """Load timelines needed to score the selected champion's 15-minute lane leads."""
    cache = MatchCache(_CACHE_PATH)
    timelines: dict[str, dict] = {}
    allowed_queues = queue_ids_for_scope(queue_scope)
    allowed_positions = ROLE_POSITION_IDS.get(role) if role != ALL_ROLES else None
    client = get_client() if fetch_missing else None
    try:
        for payload in payloads:
            info = payload.get("info", {}) if isinstance(payload, dict) else {}
            if allowed_queues is not None and info.get("queueId") not in allowed_queues:
                continue
            player = next(
                (
                    row for row in (info.get("participants", []) or [])
                    if isinstance(row, dict) and row.get("puuid") == puuid
                ),
                None,
            )
            if player is None or str(player.get("championName") or "").casefold() != champion.casefold():
                continue
            position = str(player.get("teamPosition") or player.get("individualPosition") or "").upper()
            if allowed_positions is not None and position not in allowed_positions:
                continue
            match_id = str(payload.get("metadata", {}).get("matchId") or "")
            if not match_id or match_id in timelines:
                continue
            timeline = cache.get_timeline(match_id)
            if timeline is None and client is not None:
                try:
                    timeline = client.match_timeline(match_id, server)
                except RiotAPIError:
                    LOGGER.warning("Could not scan counterstats timeline %s", match_id)
            if timeline is not None:
                timelines[match_id] = timeline
        return timelines
    finally:
        cache.close()


class CounterStatsCommands(commands.Cog):
    """Register the /counterstats command."""

    def __init__(self, bot: discord.Bot) -> None:
        self.bot = bot
        self._scan_tasks: set[asyncio.Task] = set()

    async def _finish_scan(self, message, view: CounterStatsView, puuid: str, server: str) -> None:
        """Refresh the channel message after scanning, even if the interaction token expires."""
        try:
            view.payloads = await asyncio.to_thread(_load_payloads, puuid, server, scan_riot=True)
            if view.laning:
                view.timelines = await asyncio.to_thread(
                    _load_timelines, view.payloads, puuid, view.champion, view.queue_scope,
                    view.player_role, server, fetch_missing=True,
                )
            report = view._report()
            view.page = min(view.page, _counter_page_count(report) - 1)
            view._refresh()
            await message.channel.get_partial_message(message.id).edit(
                embed=_counter_embed(report, view.player_name, view.page), view=view,
            )
        except discord.NotFound:
            LOGGER.info("/counterstats scan finished after its message was removed")
        except Exception:
            LOGGER.exception("Could not finish /counterstats Riot history scan")

    @discord.slash_command(guild_ids=GUILD_IDS, description="Show champion win rates against every enemy champion")
    @discord.option("champion", description="Champion name", required=True)
    @discord.option("queue", description="Queue scope", choices=QUEUE_SCOPES, required=False)
    @discord.option("role", description="Your champion's role", choices=_ROLES, required=True)
    @discord.option("filter", bool, description="Only show matchups above 1% usage", required=False, default=False)
    @discord.option(
        "laning", bool, description="Use who is ahead in gold at 15 minutes",
        required=False, default=False,
    )
    @discord.option("server", description="Preferred lookup platform", choices=SERVERS, required=False)
    @discord.option("username", description="League or Discord username (defaults to you)", required=False)
    @discord.option("position", int, description=MATCH_POSITION_DESCRIPTION, min_value=1, max_value=10, required=False)
    async def counterstats(
        self, ctx, champion, role, queue, filter: bool, laning: bool,
        server, username, position,
    ):
        """Show matchup history using final wins or 15-minute lane gold leads."""
        queue = queue or ALL_GAMES
        log_command(
            ctx, champion=champion, queue=queue, role=role, filter=filter,
            laning=laning, server=server, username=username, position=position,
        )
        await ctx.defer()
        target = await target_for(ctx, server, username, include_icon=False)
        if target is None:
            await ctx.respond(embed=not_found_embed(username, server, ctx=ctx))
            return
        if position is not None:
            try:
                target = await target_at_latest_match_position(target, position)
            except RiotAPIError as error:
                await ctx.respond(embed=make_embed(f"Could not fetch the latest match: {error}"))
                return
            if target is None:
                await ctx.respond(embed=make_embed(f"The latest match has no player in position {position}."))
                return
        found = await asyncio.to_thread(lambda: ddragon.catalog().by_query(champion) if ddragon.catalog() else None)
        if found is None:
            await ctx.respond(embed=make_embed(f"I could not resolve `{champion}` as a champion."))
            return
        payloads = await asyncio.to_thread(_load_payloads, target.puuid, target.server, scan_riot=False)
        timelines = await asyncio.to_thread(
            _load_timelines, payloads, target.puuid, found.name, queue, role,
            target.server, fetch_missing=False,
        ) if laning else {}
        report = aggregate(
            payloads, target.puuid, found.name, queue, role,
            min_usage_rate=0.01 if filter else 0.0,
            timelines=timelines, laning=laning,
        )
        view = CounterStatsView(
            payloads, target.puuid, found.name, queue, role, bool(filter),
            target.riot_id, ctx.author.id, timelines=timelines, laning=laning,
            server=target.server,
        )
        view.add_role_buttons()
        embed = _counter_embed(report, target.riot_id, view.page)
        embed.description += "\n\nScanning Riot match history; this message will refresh when it finishes."
        message = await ctx.respond(embed=embed, view=view)
        if message is not None:
            await remember_view_state(message, VIEW_KIND, view.state_payload())
            task = asyncio.create_task(self._finish_scan(message, view, target.puuid, target.server))
            self._scan_tasks.add(task)
            task.add_done_callback(self._scan_tasks.discard)


def setup(bot: discord.Bot) -> None:
    """Register this command module with the bot."""
    bot.add_cog(CounterStatsCommands(bot))

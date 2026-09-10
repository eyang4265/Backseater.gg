"""The public ``/champstats`` cached personal champion-history command."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Iterable

import discord
from discord.ext import commands

from .. import ddragon, emoji
from ..champstats import (
    ALL_GAMES,
    ALL_ROLES,
    QUEUE_SCOPES,
    ROLE_VALUES,
    ChampionStatsReport,
    ChoiceRecord,
    _is_boot_item,
    _patch_matches,
    aggregate,
    positions_for_role,
    queue_ids_for_scope,
)
from ..match_cache import MatchCache
from ..render import make_embed
from ..config import JSON_DIR
from ..services.riot_api import RiotAPIError, get_client
from .shared import (
    GUILD_IDS,
    MATCH_POSITION_DESCRIPTION,
    SERVERS,
    log_command,
    not_found_embed,
    target_at_latest_match_position,
    target_for,
)

_CACHE_PATH = JSON_DIR / "matches.sqlite"
_PAGE_SIZE = 10
_MATCH_SCAN_PAGE_SIZE = 100
LOGGER = logging.getLogger(__name__)


async def _patch_autocomplete(ctx: discord.AutocompleteContext) -> list[str]:
    """Suggest the six newest distinct patches from Data Dragon."""
    typed = str(getattr(ctx, "value", "") or "").casefold()
    patches = await asyncio.to_thread(ddragon.recent_patch_prefixes, 6)
    return [patch for patch in patches if not typed or typed in patch.casefold()]


def _record_text(record: ChoiceRecord) -> str:
    return f"{record.wins}W–{record.losses}L ({record.games})"


def _choice_text(record: ChoiceRecord) -> str:
    return emoji.prefixed(emoji.rune_emoji(record.name) or emoji.item_emoji(record.name, item_id=record.identifier), record.name)


def _rows(records: Iterable[ChoiceRecord], page: int) -> tuple[ChoiceRecord, ...]:
    records = tuple(records)
    return records[page * _PAGE_SIZE : (page + 1) * _PAGE_SIZE]


_MIN_USAGE_RATE = 0.01
_MIN_USAGE_GAMES = 3


def _filtered(
    records: Iterable[ChoiceRecord],
    total_games: int,
    apply_filter: bool,
) -> tuple[ChoiceRecord, ...]:
    """Optionally hide choices below one-percent usage or three games."""
    if not total_games:
        return tuple(records)
    return tuple(
        record
        for record in records
        if not apply_filter
        or record.identifier == 0
        or (
            record.games >= _MIN_USAGE_GAMES
            and record.games / total_games >= _MIN_USAGE_RATE
        )
    )


def _stats_embed(
    report: ChampionStatsReport,
    player_name: str,
    selected: str,
    page: int,
    apply_filter: bool = False,
) -> discord.Embed:
    """Render one selected report table with aligned Discord columns."""
    last_match = ""
    if report.earliest_match_timestamp:
        try:
            last_match = " · Oldest included match " + datetime.fromtimestamp(
                report.earliest_match_timestamp / 1000, tz=timezone.utc
            ).strftime("%Y-%m-%d %H:%M UTC")
        except (OverflowError, OSError, ValueError):
            pass
    if report.patch:
        patch_label = f" · {'Since ' if report.since_patch else ''}Patch {report.patch}"
    elif report.patches:
        patch_label = f" · Patches {report.patches[-1]}–{report.patches[0]}"
    else:
        patch_label = ""
    embed = make_embed(
        f"{report.queue_scope} · {report.role}"
        f"{patch_label}"
        f" · {report.games} games · {report.wins}W–{report.losses}L · {report.win_rate:.1%} WR"
        f"{last_match}",
        title=f"{report.champion} Stats — {player_name}",
    )
    if selected == "Runes":
        groups = (("Keystone", report.keystones), ("Other Runes", report.runes))
    elif selected == "Final Items":
        groups = (("Final Items", report.items),)
    else:
        groups = (("Boots", report.boots),)
    added = False
    for label, records in groups:
        page_rows = _rows(_filtered(records, report.games, apply_filter), page)
        if not page_rows:
            continue
        added = True
        embed.add_field(name=label, value="\n".join(_choice_text(row) for row in page_rows), inline=True)
        embed.add_field(name="Record", value="\n".join(_record_text(row) for row in page_rows), inline=True)
        embed.add_field(name="Win Rate", value="\n".join(f"{row.win_rate:.1%}" for row in page_rows), inline=True)
    if not added:
        embed.description += "\n\nNo details were present in the cached payloads for this view."
    embed.set_footer(text="Local cache plus the latest Riot history scan; local results are subject to the 180-day retention window.")
    return embed


class ChampStatsView(discord.ui.View):
    """Author-scoped controls for champion-history tables that never expire."""

    def __init__(
        self,
        report: ChampionStatsReport,
        player_name: str,
        author_id: int,
        apply_filter: bool = False,
    ) -> None:
        super().__init__(timeout=None)
        self.report = report
        self.player_name = player_name
        self.author_id = author_id
        self.apply_filter = apply_filter
        self.selected = "Runes"
        self.page = 0
        self._select = discord.ui.Select(
            placeholder="Choose a breakdown",
            options=[discord.SelectOption(label=label, value=label, default=label == self.selected) for label in ("Runes", "Final Items", "Boots")],
            custom_id="champstats:display",
        )
        self._select.callback = self._choose
        self.add_item(self._select)
        self._previous = discord.ui.Button(label="Previous", style=discord.ButtonStyle.secondary, custom_id="champstats:previous")
        self._next = discord.ui.Button(label="Next", style=discord.ButtonStyle.secondary, custom_id="champstats:next")
        self._previous.callback = self._go_previous
        self._next.callback = self._go_next
        self._refresh_buttons()

    def _records(self) -> tuple[ChoiceRecord, ...]:
        if self.selected == "Runes":
            records = self.report.keystones + self.report.runes
        else:
            records = {"Final Items": self.report.items, "Boots": self.report.boots}[self.selected]
        return _filtered(records, self.report.games, self.apply_filter)

    def _page_count(self) -> int:
        if self.selected == "Runes":
            largest = max(
                len(_filtered(self.report.keystones, self.report.games, self.apply_filter)),
                len(_filtered(self.report.runes, self.report.games, self.apply_filter)),
            )
        else:
            largest = len(self._records())
        return max((largest - 1) // _PAGE_SIZE + 1, 1)

    def _refresh_buttons(self) -> None:
        pages = self._page_count()
        self.page = min(self.page, pages - 1)
        for option in self._select.options:
            option.default = option.value == self.selected
        has_previous = self._previous in self.children
        if pages > 1 and not has_previous:
            self.add_item(self._previous)
            self.add_item(self._next)
        elif pages <= 1 and has_previous:
            self.remove_item(self._previous)
            self.remove_item(self._next)
        self._previous.disabled = self.page <= 0
        self._next.disabled = self.page >= pages - 1

    async def _check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("Only the command author can use these controls.", ephemeral=True)
            return False
        await interaction.response.defer()
        return True

    async def _choose(self, interaction: discord.Interaction) -> None:
        if not await self._check(interaction):
            return
        self.selected = self._select.values[0]
        self.page = 0
        self._refresh_buttons()
        await interaction.edit_original_response(embed=_stats_embed(self.report, self.player_name, self.selected, self.page, self.apply_filter), view=self)

    async def _go_previous(self, interaction: discord.Interaction) -> None:
        if not await self._check(interaction):
            return
        self.page = max(self.page - 1, 0)
        self._refresh_buttons()
        await interaction.edit_original_response(embed=_stats_embed(self.report, self.player_name, self.selected, self.page, self.apply_filter), view=self)

    async def _go_next(self, interaction: discord.Interaction) -> None:
        if not await self._check(interaction):
            return
        self.page += 1
        self._refresh_buttons()
        await interaction.edit_original_response(embed=_stats_embed(self.report, self.player_name, self.selected, self.page, self.apply_filter), view=self)

    async def on_timeout(self) -> None:
        for child in self.children:
            child.disabled = True


def _load_report(
    puuid: str,
    server: str,
    champion: str,
    queue_scope: str,
    role: str = ALL_ROLES,
    patch: str | None = None,
    since_patch: bool = False,
    *,
    scan_riot: bool = True,
) -> ChampionStatsReport:
    """Load local results and optionally merge every paginated Riot result.

    An omitted patch means all available history; autocomplete still offers
    the newest patches when a filter is wanted. Timeline recovery is limited
    to eligible games on the requested champion so unrelated ADC history does
    not add one slow Riot request per match.
    """
    cache = MatchCache(_CACHE_PATH)
    try:
        patch_window = ()
        payloads = cache.champion_match_payloads(puuid, champion)
        LOGGER.debug("/champstats: %d cached matches for %s/%s", len(payloads), puuid, champion)
        if scan_riot:
            try:
                client = get_client()
                start = 0
                scanned_ids: set[str] = set()
                while True:
                    match_ids = client.match_ids(
                        puuid, server, start=start, count=_MATCH_SCAN_PAGE_SIZE
                    )
                    if not match_ids:
                        break
                    new_ids = [match_id for match_id in match_ids if match_id not in scanned_ids]
                    if not new_ids:
                        break
                    scanned_ids.update(new_ids)
                    for match_id in new_ids:
                        try:
                            payloads.append(client.match(match_id, server))
                        except RiotAPIError as error:
                            LOGGER.warning("Could not scan cached champion match %s: %s", match_id, error)
                    start += len(match_ids)
                    if len(match_ids) < _MATCH_SCAN_PAGE_SIZE:
                        break
                LOGGER.debug("/champstats: scanned %d new match ids for %s", len(scanned_ids), puuid)
            except RiotAPIError as error:
                LOGGER.warning("Could not scan Riot match history for %s: %s", puuid, error)
        timelines: dict[str, dict] = {}
        # Timeline-backed ADC boot recovery is part of the complete report.
        metadata = ddragon.item_metadata()
        allowed_queues = queue_ids_for_scope(queue_scope)
        allowed_positions = positions_for_role(role)
        timeline_match_ids: set[str] = set()
        timeline_client = get_client()
        for payload in payloads:
            info = payload.get("info", {}) if isinstance(payload, dict) else {}
            participant = next(
                (
                    row for row in (info.get("participants", []) or [])
                    if isinstance(row, dict) and row.get("puuid") == puuid
                ),
                None,
            )
            if not participant or str(participant.get("championName") or "").casefold() != champion.casefold():
                continue
            try:
                duration_seconds = float(info.get("gameDuration") or 0)
            except (TypeError, ValueError):
                continue
            if duration_seconds <= 15 * 60 or participant.get("gameEndedInEarlySurrender"):
                continue
            if allowed_queues is not None and info.get("queueId") not in allowed_queues:
                continue
            if not _patch_matches(
                info.get("gameVersion"), patch, frozenset(patch_window), since_patch
            ):
                continue
            position = str(participant.get("teamPosition") or participant.get("individualPosition") or "").upper()
            if allowed_positions is not None and position not in allowed_positions:
                continue
            if position not in {"BOTTOM", "BOT", "ADC"}:
                continue
            final_ids = [participant.get(f"item{slot}") for slot in range(6)]
            if any(
                _is_boot_item(int(item_id), metadata.get(int(item_id)))
                for item_id in final_ids
                if str(item_id or "0").isdigit() and int(item_id or 0)
            ):
                continue
            match_id = str(payload.get("metadata", {}).get("matchId") or "")
            if not match_id or match_id in timeline_match_ids:
                continue
            timeline_match_ids.add(match_id)
            timeline = cache.get_timeline(match_id)
            if timeline is None:
                try:
                    timeline = timeline_client.match_timeline(match_id, server)
                except RiotAPIError as error:
                    LOGGER.debug("Could not fetch champstats timeline %s: %s", match_id, error)
            if timeline is not None:
                timelines[match_id] = timeline
        return aggregate(
            payloads, puuid, champion, queue_scope, metadata, role,
            timelines=timelines,
            patch=patch,
            patches=frozenset(patch_window),
            since_patch=since_patch,
        )
    finally:
        cache.close()


class ChampStatsCommands(commands.Cog):
    """Register the /champstats command."""

    def __init__(self, bot: discord.Bot) -> None:
        self.bot = bot

    @discord.slash_command(guild_ids=GUILD_IDS, description="Show your cached champion history")
    @discord.option("champion", description="Champion name", required=True)
    @discord.option("queue", description="Queue scope", choices=QUEUE_SCOPES, required=False)
    @discord.option("role", description="Role filter", choices=ROLE_VALUES, required=False)
    @discord.option(
        "patch",
        description="Patch filter; choose one of the six newest patches",
        required=False,
        autocomplete=_patch_autocomplete,
    )
    @discord.option("since_patch", bool, description="Include this patch and newer patches", required=False, default=False)
    @discord.option("server", description="Preferred lookup platform", choices=SERVERS, required=False)
    @discord.option("username", description="League or Discord username (defaults to you)", required=False)
    @discord.option("filter", bool, description="Hide choices with a play rate below 1%", required=False, default=True)
    @discord.option("position", int, description=MATCH_POSITION_DESCRIPTION, min_value=1, max_value=10, required=False)
    async def champstats(self, ctx, champion, queue, role, patch, since_patch, server, username, filter: bool, position):
        """Show match-derived choices for a direct target or latest-match slot."""
        queue = queue or ALL_GAMES
        role = role or ALL_ROLES
        apply_filter = bool(filter)
        log_command(ctx, champion=champion, queue=queue, role=role, patch=patch, since_patch=since_patch, server=server, username=username, filter=filter, position=position)
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
            LOGGER.info("/champstats could not resolve champion query %r", champion)
            await ctx.respond(embed=make_embed(f"I could not resolve `{champion}` as a champion."))
            return
        LOGGER.info("/champstats showing %s for %s (queue=%s)", found.name, target.riot_id, queue)
        report = await asyncio.to_thread(
            _load_report,
            target.puuid,
            target.server,
            found.name,
            queue,
            role,
            patch,
            since_patch,
            scan_riot=True,
        )
        player_name = target.riot_id
        view = ChampStatsView(report, player_name, ctx.author.id, apply_filter)
        embed = _stats_embed(report, player_name, view.selected, 0, apply_filter)
        await ctx.respond(embed=embed, view=view)


def setup(bot: discord.Bot) -> None:
    """Register this command module with the bot."""
    bot.add_cog(ChampStatsCommands(bot))

"""Acknowledged manual /stats game selection and legacy result-view helpers."""

import asyncio
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import logging

import discord

from .lolesports import EsportsUnavailable

from .store import read_json

LOGGER = logging.getLogger(__name__)


class WorldsStatsView(discord.ui.View):
    """Read the latest saved series and game snapshots after acknowledging a tab."""

    def __init__(self, series, channel_id, selected=1, *, return_view=None, series_list=None):
        super().__init__(timeout=1800 if return_view is not None or series_list is not None else None)
        self.series = series
        self.channel_id = str(channel_id)
        self.selected = selected
        self.return_view = return_view
        self.series_list = series_list
        self.series_page = 0
        self.manual = return_view is not None or series_list is not None
        self.message = None
        self.closed = False
        self.game_buttons = []
        self._lock = asyncio.Lock()
        self.build_controls()

    def build_controls(self):
        """Build game buttons and a series selector with newer navigation first."""
        self.clear_items()
        self.game_buttons = []
        series = self.series
        limit = min(series["best_of"], sum(series["score"])) if self.series_list is not None else series["best_of"]
        for number in range(1, limit + 1):
            button = discord.ui.Button(label=f"Game {number}", custom_id=f"worlds:stats:game:{number}",
                                       row=(number - 1) // 5)

            async def select(interaction, game_number=number):
                await self.change(interaction, game_number)

            button.callback = select
            self.add_item(button)
            self.game_buttons.append(button)
        if self.return_view is not None:
            back = discord.ui.Button(label="Back to schedule", custom_id="worlds:stats:back", row=1)
            async def restore(interaction):
                await interaction.response.defer()
                async with self._lock:
                    self.closed = True
                    self.stop()
                    await self.return_view.restore(interaction, acknowledged=True)

            back.callback = restore
            self.add_item(back)
        if self.series_list is not None:
            start = self.series_page * 23
            options = []
            for index, match in enumerate(self.series_list[start:start + 23], start):
                label = " vs ".join(match["names"])
                date = datetime.fromtimestamp(match["start"], ZoneInfo("America/Los_Angeles"))
                options.append(discord.SelectOption(label=label[:100], value=str(index),
                               description=f"{date:%a, %b %d, %Y} · {match['stage']}"[:100],
                               default=match["id"] == self.series["id"]))
            if start:
                options.insert(0, discord.SelectOption(label="Newer series", value="previous"))
            if start + 23 < len(self.series_list):
                options.append(discord.SelectOption(label="Older series", value="more"))
            picker = discord.ui.Select(placeholder="Choose a Worlds series", options=options, row=2)
            picker.callback = self.choose_series
            self.add_item(picker)
        self.sync()

    async def choose_series(self, interaction):
        """Acknowledge before loading another series' first game or paging its selector."""
        await interaction.response.defer()
        async with self._lock:
            if self.closed:
                return
            choice = interaction.data["values"][0]
            if choice in ("previous", "more"):
                self.series_page = min(max(self.series_page + (-1 if choice == "previous" else 1), 0),
                                       (len(self.series_list) - 1) // 23)
                self.build_controls()
                await interaction.message.edit(view=self)
                return
            self.series = self.series_list[int(choice)]
            self.selected = 1
            self.build_controls()
            self.message = interaction.message
            options = await self.render_game(1, guild=getattr(interaction, "guild", None),
                                             channel=getattr(interaction, "channel", None))
            await interaction.message.edit(attachments=[], allowed_mentions=discord.AllowedMentions.none(), **options)

    def sync(self):
        """Highlight the selected game without disabling unused series-result tabs."""
        for number, button in enumerate(self.game_buttons, 1):
            button.disabled = number == self.selected
            button.style = (discord.ButtonStyle.primary if number == self.selected
                            else discord.ButtonStyle.secondary)

    async def change(self, interaction, number, *, acknowledged=False):
        """Defer, then edit the source message so empty attachments actually remove charts."""
        if not acknowledged:
            await interaction.response.defer()
        async with self._lock:
            if self.closed:
                return
            self.message = interaction.message
            options = await self.render_game(number, guild=getattr(interaction, "guild", None),
                                             channel=getattr(interaction, "channel", None),
                                             message_id=getattr(interaction.message, "id", None))
            await interaction.message.edit(attachments=[], allowed_mentions=discord.AllowedMentions.none(), **options)

    async def render_game(self, number, *, guild=None, channel=None, message_id=None):
        """Load the shared game embed/chart after caller acknowledgement, for sends and edits."""
        from . import worlds
        from .worlds_stats import game_stats_embed, load_saved_stats
        state = await asyncio.to_thread(read_json, worlds.STATE_PATH, {"channels": {}})
        receipt = state.get("channels", {}).get(self.channel_id, {}).get("series", {}).get(self.series["id"], {})
        series = receipt.get("series_snapshot", self.series)
        source = next((r for r in receipt.get("stats_games", {}).values()
                       if r.get("message_id") == message_id), None)
        if not self.manual and source is not None and not source.get("final"):
            embed = worlds.announcement(source.get("series", series), score=True)
            return {"embed": embed, "view": None}
        game = next((g for g in series.get("games", []) if g["number"] == number), None)
        record = receipt.get("stats_games", {}).get(game["id"], {}) if game else {}
        stats = await asyncio.to_thread(load_saved_stats, series, game) if game else None
        if self.manual and game and number <= sum(series["score"]) and not stats:
            from .worlds_stats import final_stats
            try:
                stats = await asyncio.to_thread(final_stats, series, game, datetime.now(timezone.utc))
            except (EsportsUnavailable, ValueError):
                LOGGER.info("Manual Worlds stats unavailable game=%s", game["id"])
        embed = await asyncio.to_thread(game_stats_embed, series, number, stats,
                                       unavailable=record.get("status") == "unavailable"
                                       or (self.manual and stats is None))
        options = {}
        if stats:
            from .charts import build_worlds_damage_share_chart
            can_attach = (guild is None or channel is None
                          or channel.permissions_for(guild.me).attach_files)
            chart = await asyncio.to_thread(build_worlds_damage_share_chart, stats, series) if can_attach else None
            if chart:
                embed.set_image(url="attachment://" + chart.filename)
                options["file"] = chart
        self.selected = number
        if self.series_list is not None:
            self.series = series
            self.build_controls()
        else:
            self.sync()
        return {"embed": embed, "view": self, **options}

    async def on_timeout(self):
        """Disable manual controls without replacing a newer schedule or game view."""
        if self.manual and self.message is not None:
            async with self._lock:
                if self.closed:
                    return
                self.closed = True
                for item in self.children:
                    item.disabled = True
                try:
                    await self.message.edit(view=self)
                except discord.DiscordException:
                    LOGGER.debug("Could not disable expired Worlds game controls", exc_info=True)


def register_worlds_stats_views(bot):
    """Automatic results are score-only; manual /stats views are not persistent."""
    return 0


async def upgrade_stats_messages(channel, series, receipt, save):
    """Keep stats/tabs on final results and strip them from earlier score posts in place."""
    from .worlds_stats import game_stats_embed, load_saved_stats
    for game in series.get("games", []):
        record = receipt.get("stats_games", {}).get(game["id"], {})
        if not record.get("message_id") or record.get("tabs_version") == 9 or record.get("status") == "deleted":
            continue
        try:
            message = await channel.fetch_message(record["message_id"])
            if not record.get("final"):
                from .worlds import announcement
                embed = announcement(record.get("series", series), score=True)
                await message.edit(embed=embed, view=None, attachments=[],
                                   allowed_mentions=discord.AllowedMentions.none())
                record["tabs_version"] = 9
                await save()
                continue
            stats = await asyncio.to_thread(load_saved_stats, series, game)
            embed = game_stats_embed(series, game["number"], stats,
                                     unavailable=record.get("status") == "unavailable")
            options = {}
            if stats:
                from .charts import build_worlds_damage_share_chart
                guild = getattr(channel, "guild", None)
                can_attach = (guild is None or not hasattr(channel, "permissions_for")
                              or channel.permissions_for(guild.me).attach_files)
                chart = await asyncio.to_thread(build_worlds_damage_share_chart, stats, series) if can_attach else None
                if chart:
                    embed.set_image(url="attachment://" + chart.filename)
                    options["file"] = chart
            await message.edit(embed=embed, view=WorldsStatsView(series, channel.id, game["number"]),
                               attachments=[], allowed_mentions=discord.AllowedMentions.none(), **options)
            record["tabs_version"] = 9
        except discord.NotFound:
            record["status"] = "deleted"
        await save()

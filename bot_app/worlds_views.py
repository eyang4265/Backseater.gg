"""Acknowledged date buttons and refreshable manual Worlds schedules."""

import asyncio
from datetime import datetime, time, timezone
import logging
from zoneinfo import ZoneInfo

import discord

from .worlds import status_embeds, schedule_message_embeds, SCHEDULE_URL

LOGGER = logging.getLogger(__name__)


class ScheduleTabs(list):
    """Rendered dates with the corresponding series for the numbered stats picker."""

    def __init__(self, tabs, series_by_tab):
        super().__init__(tabs)
        self.series_by_tab = series_by_tab


def schedule_tabs(series, *, now=None):
    """Prepare relative date labels and series lookup lists using Pacific calendar days."""
    now = now or datetime.now(timezone.utc)
    pacific = ZoneInfo("America/Los_Angeles")
    today = now.astimezone(pacific).date()
    by_day = {}
    for match in sorted(series, key=lambda s: (s["start"], s["id"])):
        by_day.setdefault(datetime.fromtimestamp(match["start"], pacific).date(), []).append(match)
    current = [match for day in sorted(by_day) if day == today or
               (day < today and any(s["state"] == "inProgress" for s in by_day[day]))
               for match in by_day[day]]

    def pages(matches, anchor, *, past=False, yesterday=False):
        markers = {s["id"]: str(i) + "." for i, s in enumerate(matches, 1)}
        embeds = status_embeds(matches, now=anchor, include_next=False, series_markers=markers)
        if past:
            for embed in embeds:
                embed.title = ("Worlds · Yesterday" if yesterday else
                               embed.title.replace("Worlds · Today · ", "Worlds · Past Results · "))
        result = schedule_message_embeds(embeds)
        if past:
            for embed in result:
                embed.url = None
        return result

    tabs = [("Today", pages(current, now))]
    series_by_tab = [current]
    future = [day for day in by_day if day > today]
    if future:
        next_day = min(future)
        matches = by_day[next_day]
        label = "Tomorrow" if (next_day - today).days == 1 else next_day.strftime("%a, %b %d")
        upcoming = status_embeds(matches, now=now, series_markers={s["id"]: str(i) + "." for i, s in enumerate(matches, 1)})
        tabs.append((label, schedule_message_embeds([e for e in upcoming if e.title.startswith("Worlds · Next game day · ")])))
        series_by_tab.append(matches)
    past = [day for day in by_day if day < today]
    if past:
        last_day = max(past)
        yesterday = (today - last_day).days == 1
        anchor = datetime.combine(last_day, time(12), pacific)
        tabs.insert(0, ("Yesterday" if yesterday else "Past Results",
                       pages(by_day[last_day], anchor, past=True, yesterday=yesterday)))
        series_by_tab.insert(0, by_day[last_day])
    else:
        tabs.insert(0, ("Past Results", [discord.Embed(title="Worlds · Past Results",
                       color=0xC8AA6E, description="No past match dates available.")]))
        series_by_tab.insert(0, [])
    return ScheduleTabs(tabs, series_by_tab)


class WorldsScheduleView(discord.ui.View):
    """Open Today with acknowledged date buttons; tracked posts survive restarts."""

    def __init__(self, tabs, *, show_stats=True, tracked=False):
        super().__init__(timeout=None if tracked else 1800)
        self.tracked = tracked
        self.post_id = self.channel_id = None
        self.tabs = tabs
        self.show_stats = show_stats
        self.selected = next((i for i, (label, _) in enumerate(tabs) if label == "Today"), 0)
        self.page = 0
        self.message = None
        self.series_by_tab = getattr(tabs, "series_by_tab", [[] for _ in tabs])
        self.series_page = 0
        self.series_select = None
        self.active_stats = False
        self._lock = asyncio.Lock()
        self._build_controls()

    def _build_controls(self):
        """Rebuild date/page controls when the latest feed changes available dates."""
        self.clear_items()
        self.series_select = None
        self.day_buttons = []
        for index, (label, _pages) in enumerate(self.tabs):
            button = discord.ui.Button(label=label, style=discord.ButtonStyle.secondary, row=0,
                                       custom_id=f"worlds:schedule:day:{index}")

            async def select(interaction, selected=index):
                await self._change(interaction, selected=selected)

            button.callback = select
            self.day_buttons.append(button)
            self.add_item(button)
        self.previous = self.next = None
        if any(len(pages) > 1 for _, pages in self.tabs):
            self.previous = discord.ui.Button(label="Previous", row=1, custom_id="worlds:schedule:previous")
            self.next = discord.ui.Button(label="Next", row=1, custom_id="worlds:schedule:next")

            async def previous(interaction):
                await self._change(interaction, step=-1)

            async def next_page(interaction):
                await self._change(interaction, step=1)

            self.previous.callback = previous
            self.next.callback = next_page
            self.add_item(self.previous)
            self.add_item(self.next)
        self._sync()

    def refresh_tabs(self, tabs):
        """Replace stale scores and date controls while preserving the chosen tab/page."""
        self.tabs = tabs
        self.series_by_tab = getattr(tabs, "series_by_tab", [[] for _ in tabs])
        self.selected = min(self.selected, len(tabs) - 1)
        self.page = min(self.page, len(tabs[self.selected][1]) - 1)
        self._build_controls()

    def _sync(self):
        """Highlight the selected date and bound overflow controls to its pages."""
        for index, button in enumerate(self.day_buttons):
            button.disabled = index == self.selected
            button.style = discord.ButtonStyle.primary if index == self.selected else discord.ButtonStyle.secondary
        self._sync_series_select()
        if self.previous is not None:
            self.previous.disabled = self.page == 0
            self.next.disabled = self.page == len(self.tabs[self.selected][1]) - 1

    def _sync_series_select(self):
        """Offer numbered series matching the schedule, with paging for busy dates."""
        if self.series_select is not None:
            self.remove_item(self.series_select)
            self.series_select = None
        matches = self.series_by_tab[self.selected]
        if not self.show_stats or not matches:
            return
        self.series_page = min(self.series_page, (len(matches) - 1) // 23)
        start = self.series_page * 23
        options = [discord.SelectOption(label=f"Series {index + 1}", value=str(index),
                   description=datetime.fromtimestamp(match["start"], ZoneInfo("America/Los_Angeles")).strftime("%a, %b %d · %I:%M %p Pacific"))
                   for index, match in enumerate(matches[start:start + 23], start)]
        if start:
            options.append(discord.SelectOption(label="Previous series", value="previous"))
        if start + 23 < len(matches):
            options.append(discord.SelectOption(label="More series", value="more"))
        self.series_select = discord.ui.Select(placeholder="Game stats · Choose a series", options=options, row=2)
        self.series_select.callback = self._choose_series
        self.add_item(self.series_select)

    async def _choose_series(self, interaction):
        """Acknowledge before storage, stats requests, or chart rendering."""
        await interaction.response.defer()
        async with self._lock:
            choice = interaction.data["values"][0]
            if choice in ("previous", "more"):
                self.series_page += -1 if choice == "previous" else 1
                self._sync()
                await interaction.message.edit(view=self)
                return
            from .worlds_stats_views import WorldsStatsView
            series = self.series_by_tab[self.selected][int(choice)]
            view = WorldsStatsView(series, interaction.channel_id, return_view=self)
            self.active_stats = True
            await view.change(interaction, 1, acknowledged=True)

    async def restore(self, interaction, *, acknowledged=False):
        """Return to the chosen schedule date and remove the prior chart attachment."""
        if not acknowledged:
            await interaction.response.defer()
        async with self._lock:
            self.active_stats = False
            self._sync()
            if self.is_finished():
                for item in self.children:
                    item.disabled = True
            await interaction.message.edit(embed=self.render(), view=self, attachments=[],
                                           allowed_mentions=discord.AllowedMentions.none())

    def render(self):
        """Return the selected date's already-rendered schedule page."""
        return self.tabs[self.selected][1][self.page]

    async def _change(self, interaction, *, selected=None, step=0):
        """Acknowledge before waiting for another edit or rendering the chosen page."""
        await interaction.response.defer()
        async with self._lock:
            if selected is not None:
                self.selected, self.page = selected, 0
                self.series_page = 0
            else:
                self.page = min(max(self.page + step, 0), len(self.tabs[self.selected][1]) - 1)
            self._sync()
            await interaction.edit_original_response(embed=self.render(), view=self,
                                                     allowed_mentions=discord.AllowedMentions.none())
            if self.post_id is not None:
                from .worlds_schedule_posts import remember_schedule_post
                await remember_schedule_post(self, self.channel_id, self.post_id)

    async def on_timeout(self):
        """Disable expired controls; rerunning /worlds obtains a fresh schedule."""
        for button in self.children:
            button.disabled = True
        if self.message is not None and not self.active_stats:
            try:
                await self.message.edit(view=self)
            except discord.DiscordException:
                LOGGER.debug("Could not disable expired Worlds date buttons", exc_info=True)

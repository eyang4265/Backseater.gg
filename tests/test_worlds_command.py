"""Worlds status selection, shared caching, and acknowledgement before network I/O."""

from datetime import datetime, timezone
import json
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from bot_app import worlds
from bot_app.worlds_views import schedule_tabs, WorldsScheduleView
from bot_app.commands.worlds import WorldsCommands
from bot_app.lolesports import EsportsUnavailable
from tests.test_worlds import event, parse


class WorldsStatusTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 28, 19, tzinfo=timezone.utc)

    def series(self, key, when, score=(0, 0), state="unstarted"):
        result = parse(event(score, state))[0]
        result.update(id=key, start=datetime.fromisoformat(when).timestamp())
        return result

    def test_full_today_and_next_day_include_results_live_and_scheduled(self):
        series = [self.series("a", "2026-10-28T17:00:00+00:00", (2, 0), "completed"),
                  self.series("b", "2026-10-28T18:00:00+00:00", (1, 0), "inProgress"),
                  self.series("c", "2026-10-28T23:00:00+00:00"),
                  self.series("d", "2026-10-30T17:00:00+00:00"),
                  self.series("e", "2026-10-30T23:00:00+00:00"),
                  self.series("f", "2026-10-31T17:00:00+00:00")]
        embeds = worlds.status_embeds(series, now=self.now)
        self.assertEqual(len(embeds), 2)
        self.assertIn("Today", embeds[0].title)
        self.assertIn("Oct 30", embeds[1].title)
        self.assertEqual(len(embeds[0].fields[0].value.splitlines()), 3)
        self.assertEqual(len(embeds[1].fields[0].value.splitlines()), 2)
        self.assertIn("2–0 · Final", embeds[0].fields[2].value)
        self.assertIn("1–0 · Live", embeds[0].fields[2].value)
        self.assertIn(":R>", embeds[0].fields[1].value)
        self.assertTrue(all(e.url == "https://www.twitch.tv/caedrel" for e in embeds))

    def test_pacific_midnight_groups_utc_dates_together(self):
        now = datetime(2026, 10, 29, 1, tzinfo=timezone.utc)
        series = [self.series("a", "2026-10-28T23:00:00+00:00"),
                  self.series("b", "2026-10-29T02:00:00+00:00"),
                  self.series("c", "2026-10-29T20:00:00+00:00")]
        embeds = worlds.status_embeds(series, now=now)
        self.assertIn("Oct 28", embeds[0].title)
        self.assertEqual(len(embeds[0].fields[0].value.splitlines()), 2)
        self.assertIn("Awaiting start", embeds[0].fields[2].value)
        self.assertIn("Tomorrow", embeds[1].title)

    def test_off_day_includes_full_next_game_day(self):
        series = [self.series("a", "2026-10-30T17:00:00+00:00"),
                  self.series("b", "2026-10-30T23:00:00+00:00")]
        embeds = worlds.status_embeds(series, now=self.now)
        self.assertIn("No Worlds series scheduled today", embeds[0].description)
        self.assertEqual(len(embeds[1].fields[0].value.splitlines()), 2)

    def test_long_day_splits_all_rows_under_field_limits(self):
        series = [self.series(str(i), "2026-10-28T20:00:00+00:00") for i in range(40)]
        for item in series:
            item["names"] = ["Long team name" * 10] * 2
        embeds = worlds.status_embeds(series, now=self.now)
        self.assertGreater(len(embeds), 1)
        self.assertEqual(sum(len(e.fields[0].value.splitlines()) for e in embeds), 40)
        self.assertTrue(all(len(f.value) <= 1024 for e in embeds for f in e.fields))
        self.assertTrue(all(len(e) <= 6000 for e in embeds))

    def test_unknown_matchups_are_schedule_only(self):
        payload = event()
        for team in payload["matchTeams"]:
            team.update(teamId="0", code="TBD", result=None)
        html = json.dumps(payload)
        with self.assertRaises(ValueError):
            worlds.parse_series(html)
        schedule = worlds.parse_series(html, include_unknown=True)
        self.assertFalse(schedule[0]["known_teams"])
        embeds = worlds.status_embeds(schedule, now=self.now)
        self.assertIn("TBD vs TBD", embeds[0].fields[0].value)

    def test_combined_pages_respect_field_and_total_limits(self):
        series = [self.series(str(i), "2026-10-28T20:00:00+00:00") for i in range(90)]
        for item in series:
            item["names"] = ["Long team name" * 10] * 2
        pages = worlds.schedule_message_embeds(worlds.status_embeds(series, now=self.now))
        self.assertGreater(len(pages), 1)
        self.assertTrue(all(len(e.fields) <= 25 and len(e) <= 6000 for e in pages))
        rows = sum(len(f.value.splitlines()) - 1 for e in pages for f in e.fields if f.value.startswith("**Series**\n"))
        self.assertEqual(rows, 90)

    def test_feed_cache_is_shared_and_expires(self):
        with patch.object(worlds, "_cached_series", None), patch.object(worlds, "_cache_until", 0), \
             patch.object(worlds.time, "monotonic", return_value=100) as clock, \
             patch.object(worlds, "enrich_stage_labels"), \
             patch.object(worlds, "fetch_worlds_page", return_value=json.dumps(event())) as fetch:
            self.assertEqual(worlds.fetch_series(), worlds.fetch_series())
            fetch.assert_called_once()
            clock.return_value = 131
            worlds.fetch_series()
            self.assertEqual(fetch.call_count, 2)


class WorldsCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_acknowledges_before_fetching(self):
        ctx = SimpleNamespace(defer=AsyncMock(), respond=AsyncMock())

        def fetch(**kwargs):
            self.assertTrue(kwargs["include_unknown"])
            ctx.defer.assert_awaited_once()
            return parse(event())

        with patch("bot_app.commands.worlds.log_command"), \
             patch("bot_app.worlds_command.fetch_series", side_effect=fetch):
            await WorldsCommands.worlds.callback(WorldsCommands(Mock()), ctx)
        ctx.respond.assert_awaited_once()
        self.assertIn("embeds", ctx.respond.call_args.kwargs)

    async def test_today_is_shown_with_next_date_button_in_one_message(self):
        ctx = SimpleNamespace(defer=AsyncMock(), respond=AsyncMock())
        series = parse(event())
        tomorrow = dict(series[0], id="tomorrow", start=series[0]["start"] + 86400)
        now = datetime(2026, 10, 28, 19, tzinfo=timezone.utc)
        tabs = schedule_tabs(series + [tomorrow], now=now)
        with patch("bot_app.commands.worlds.log_command"), \
             patch("bot_app.worlds_command.fetch_series", return_value=series), \
             patch("bot_app.worlds_command.schedule_tabs", return_value=tabs):
            await WorldsCommands.worlds.callback(WorldsCommands(Mock()), ctx)
        ctx.respond.assert_awaited_once()
        sent = ctx.respond.call_args.kwargs["embeds"]
        self.assertEqual(len(sent), 1)
        self.assertIn("Today", sent[0].fields[0].name)
        self.assertEqual(len(sent[0].fields), 3)
        view = ctx.respond.call_args.kwargs["view"]
        self.assertEqual([button.label for button in view.day_buttons], ["Past Results", "Today", "Tomorrow"])
        self.assertIn("Next games", view.tabs[2][1][0].fields[0].name)
        self.assertNotIn("footer", sent[0].to_dict())
        self.assertTrue(sent[0].fields[0].value.startswith("**Series**\n"))

    async def test_feed_failure_is_a_concise_response(self):
        ctx = SimpleNamespace(defer=AsyncMock(), respond=AsyncMock())
        with patch("bot_app.commands.worlds.log_command"), \
             patch("bot_app.worlds_command.fetch_series", side_effect=EsportsUnavailable("offline")), \
             self.assertLogs("bot_app.worlds_command", level="WARNING"):
            await WorldsCommands.worlds.callback(WorldsCommands(Mock()), ctx)
        self.assertIn("unavailable", ctx.respond.call_args.kwargs["embed"].description)


class WorldsDateButtonTests(unittest.IsolatedAsyncioTestCase):
    async def test_switching_dates_acknowledges_before_editing_same_message(self):
        series = parse(event())
        later = dict(series[0], id="later", start=series[0]["start"] + 3 * 86400)
        now = datetime(2026, 10, 28, 19, tzinfo=timezone.utc)
        view = WorldsScheduleView(schedule_tabs(series + [later], now=now))
        self.assertEqual([button.label for button in view.day_buttons], ["Past Results", "Today", "Sat, Oct 31"])
        interaction = SimpleNamespace(response=SimpleNamespace(defer=AsyncMock()))

        async def edit(**options):
            interaction.response.defer.assert_awaited()
            self.assertIs(options["view"], view)

        interaction.edit_original_response = AsyncMock(side_effect=edit)
        await view.day_buttons[2].callback(interaction)
        self.assertEqual(view.selected, 2)
        self.assertIn("Oct 31", view.render().fields[0].name)
        await view.day_buttons[1].callback(interaction)
        self.assertEqual(view.selected, 1)
        self.assertIn("Today", view.render().fields[0].name)
        self.assertEqual(interaction.edit_original_response.await_count, 2)

    async def test_no_future_date_has_today_and_results_buttons(self):
        now = datetime(2026, 10, 28, 19, tzinfo=timezone.utc)
        view = WorldsScheduleView(schedule_tabs(parse(event()), now=now))
        self.assertEqual([button.label for button in view.day_buttons], ["Past Results", "Today"])

    async def test_results_show_last_complete_day_skip_off_days_and_incomplete_today(self):
        now = datetime(2026, 10, 28, 19, tzinfo=timezone.utc)
        older = dict(parse(event((2, 0), "completed"))[0], id="old", start=1793037600)
        older2 = dict(older, id="old2", score=[1, 2], start=older["start"] + 10800)
        live = parse(event((1, 0), "inProgress"))[0]
        tabs = schedule_tabs([older, older2, live], now=now)
        self.assertEqual(tabs[0][0], "Past Results")
        result = tabs[0][1][0]
        self.assertIn("Past Results", result.fields[0].name)
        self.assertIn("Mon, Oct 26", result.fields[0].name)
        self.assertEqual(result.fields[2].value, "**Score / Status**\n2–0 · Final\n1–2 · Final")
        view = WorldsScheduleView(tabs)
        interaction = SimpleNamespace(response=SimpleNamespace(defer=AsyncMock()), edit_original_response=AsyncMock())
        await view.day_buttons[0].callback(interaction)
        interaction.response.defer.assert_awaited_once()
        self.assertIs(view.render(), result)

    async def test_past_results_exclude_completed_today_and_keep_all_latest_date_matches(self):
        now = datetime(2026, 10, 28, 23, tzinfo=timezone.utc)
        today = parse(event((2, 0), "completed"))[0]
        older = dict(today, id="older", start=datetime(2026, 10, 25, 18, tzinfo=timezone.utc).timestamp())
        prior = dict(today, id="prior", start=datetime(2026, 10, 27, 18, tzinfo=timezone.utc).timestamp())
        unfinished = dict(prior, id="unfinished", start=prior["start"] + 3600,
                          state="inProgress", score=[1, 0])
        tabs = schedule_tabs([today, older, prior, unfinished], now=now)
        result = tabs[0][1][0]
        self.assertEqual(tabs[0][0], "Yesterday")
        self.assertIn("Yesterday", result.fields[0].name)
        self.assertEqual(result.fields[2].value, "**Score / Status**\n2–0 · Final\n1–0 · Live")

    async def test_past_results_empty_when_only_today_is_available(self):
        now = datetime(2026, 10, 28, 23, tzinfo=timezone.utc)
        tabs = schedule_tabs(parse(event((2, 0), "completed")), now=now)
        self.assertEqual(tabs[0][0], "Past Results")
        self.assertEqual(tabs[0][1][0].description, "No past match dates available.")

    async def test_changing_day_resets_overflow_page_and_timeout_disables_controls(self):
        embed = worlds.schedule_message_embeds(worlds.status_embeds(parse(event())))[0]
        view = WorldsScheduleView([("Today", [embed, embed]), ("Oct 31", [embed])])
        interaction = SimpleNamespace(response=SimpleNamespace(defer=AsyncMock()), edit_original_response=AsyncMock())
        await view.next.callback(interaction)
        self.assertEqual(view.page, 1)
        await view.day_buttons[1].callback(interaction)
        self.assertEqual(view.page, 0)
        self.assertTrue(view.next.disabled)
        view.message = SimpleNamespace(edit=AsyncMock())
        await view.on_timeout()
        self.assertTrue(all(button.disabled for button in view.children))

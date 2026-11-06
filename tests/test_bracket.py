"""Swiss round reconstruction, rendering, and deferred slash-command delivery."""

from collections import defaultdict
from datetime import datetime, timezone
from io import BytesIO
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from PIL import Image

from bot_app.commands.bracket import BracketCommands
from bot_app.worlds_bracket import (bracket_state, render_bracket, bracket_kind,
                                   knockout_state, render_knockout)


def swiss_fixture():
    """Complete deterministic 16-team Swiss tournament, with series/game counts distinct."""
    records = {str(i): [0, 0] for i in range(16)}
    series = []
    for round_number in range(1, 6):
        groups = defaultdict(list)
        for team, record in records.items():
            if max(record) < 3:
                groups[tuple(record)].append(team)
        for record, teams in groups.items():
            for a, b in zip(teams[::2], teams[1::2]):
                series.append({"id": str(len(series)), "teams": [a, b], "names": [a, b],
                               "stage": "Swiss", "start": 1792700000 + round_number * 86400 + len(series),
                               "state": "completed", "score": [2 if 2 in record else 1, 0]})
                records[a][0] += 1
                records[b][1] += 1
    return series


class BracketTests(unittest.TestCase):
    def test_full_swiss_counts_series_wins_not_game_wins(self):
        state = bracket_state(swiss_fixture())
        self.assertEqual(sum(r[0] == 3 for r in state["records"].values()), 8)
        self.assertEqual(sum(r[1] == 3 for r in state["records"].values()), 8)
        counts = [sum(len(v) for (number, _), v in state["groups"].items() if number == n)
                  for n in range(1, 6)]
        self.assertEqual(counts, [8, 8, 8, 6, 3])

    def test_missing_history_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "incomplete"):
            bracket_state(swiss_fixture()[1:])

    def test_unplayed_series_do_not_advance_records(self):
        series = swiss_fixture()[:8]
        series[0] = dict(series[0], state="inProgress", score=[0, 0])
        state = bracket_state(series)
        self.assertEqual(state["records"]["0"], [0, 0])

    def test_render_is_valid_png_with_placeholder_rounds(self):
        art = render_bracket(bracket_state(swiss_fixture()[:8]))
        with Image.open(art) as image:
            self.assertEqual(image.format, "PNG")
            self.assertEqual(image.size, (2400, 1150))
            image.verify()


def knockout_fixture():
    result = []
    for stage, count in (("Quarterfinals", 4), ("Semifinals", 2), ("Finals", 1)):
        for index in range(count):
            result.append({"id": f"{stage}-{index}", "teams": ["0", "0"], "names": ["TBD", "TBD"],
                           "stage": stage, "state": "unstarted", "score": [0, 0],
                           "start": 1794000000 + len(result) * 86400, "known_teams": False})
    return result


class KnockoutTests(unittest.TestCase):
    def test_future_knockouts_do_not_replace_active_swiss(self):
        now = datetime(2026, 10, 28, tzinfo=timezone.utc)
        self.assertEqual(bracket_kind(swiss_fixture()[:8] + knockout_fixture(), now=now), "Swiss")

    def test_completed_swiss_switches_before_first_quarterfinal(self):
        now = datetime(2026, 10, 28, tzinfo=timezone.utc)
        self.assertEqual(bracket_kind(swiss_fixture() + knockout_fixture(), now=now), "Knockout")

    def test_live_knockout_does_not_require_swiss_history(self):
        fixtures = knockout_fixture()
        fixtures[0]["state"] = "inProgress"
        self.assertEqual(bracket_kind(fixtures), "Knockout")

    def test_tbd_render_has_seven_slots_and_no_inferred_teams(self):
        state = knockout_state(knockout_fixture())
        self.assertEqual([len(r) for r in state["rounds"]], [4, 2, 1])
        self.assertEqual(state["teams"], {})
        with Image.open(render_knockout(state)) as image:
            self.assertEqual(image.size, (1640, 850))
            image.verify()

    def test_published_semifinal_teams_control_upstream_alignment(self):
        fixtures = knockout_fixture()
        for i, match in enumerate(fixtures[:4]):
            match.update(teams=[str(i*2+1), str(i*2+2)], names=[f"A{i}", f"B{i}"])
        fixtures[4].update(teams=["7", "1"], names=["A3", "A0"])
        fixtures[5].update(teams=["5", "3"], names=["A2", "A1"])
        state = knockout_state(fixtures)
        self.assertEqual([m["id"] for m in state["rounds"][0]],
                         ["Quarterfinals-3", "Quarterfinals-0", "Quarterfinals-2", "Quarterfinals-1"])


class BracketCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_defer_before_data_and_render(self):
        ctx = SimpleNamespace(defer=AsyncMock(), respond=AsyncMock())

        def fetch(**kwargs):
            self.assertTrue(kwargs["include_unknown"])
            ctx.defer.assert_awaited_once()
            return swiss_fixture()[:8]

        def render(series, **kwargs):
            ctx.defer.assert_awaited_once()
            return BytesIO(b"png")

        with patch("bot_app.commands.bracket.log_command"), \
             patch("bot_app.commands.bracket.fetch_series", side_effect=fetch), \
             patch("bot_app.commands.bracket.build_bracket", side_effect=render):
            await BracketCommands.bracket.callback(BracketCommands(Mock()), ctx)
        self.assertEqual(ctx.respond.call_args.kwargs["file"].filename, "SPOILER_worlds-bracket.png")
        self.assertTrue(ctx.respond.call_args.kwargs["file"].spoiler)
        self.assertIsNone(ctx.respond.call_args.kwargs["embed"].image)

    async def test_incomplete_history_has_concise_status(self):
        ctx = SimpleNamespace(defer=AsyncMock(), respond=AsyncMock())
        with patch("bot_app.commands.bracket.log_command"), \
             patch("bot_app.commands.bracket.fetch_series", return_value=[]), \
             self.assertLogs("bot_app.commands.bracket", level="WARNING"):
            await BracketCommands.bracket.callback(BracketCommands(Mock()), ctx)
        self.assertIn("unavailable", ctx.respond.call_args.kwargs["embed"].description)

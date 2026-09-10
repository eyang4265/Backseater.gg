"""The ``/tftmatchhistory`` command's row formatting and queue filter."""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from bot_app.commands.shared import Target
from bot_app.commands.tftmatchhistory import TftMatchHistoryCommands, _history_line


def _match(match_id: str, *, placement: int, queue_id: int = 1100, puuid: str = "me") -> dict:
    return {
        "metadata": {"match_id": match_id},
        "info": {
            "queue_id": queue_id,
            "game_length": 1800.0,
            "game_datetime": 1_700_000_000_000,
            "participants": [
                {
                    "puuid": puuid,
                    "placement": placement,
                    "level": 8,
                    "players_eliminated": placement % 3,
                },
                {"puuid": "other", "placement": 1 if placement != 1 else 2},
            ],
        },
    }


class HistoryLineTests(unittest.TestCase):
    def test_top_four_and_first_place_markers(self) -> None:
        line, top = _history_line(_match("NA1_1", placement=1), "me")
        self.assertTrue(top)
        self.assertIn("🥇", line)
        self.assertIn("#1", line)
        self.assertIn("TFT Ranked", line)

        line, top = _history_line(_match("NA1_2", placement=6), "me")
        self.assertFalse(top)
        self.assertIn("❌", line)

    def test_missing_participant_is_dropped(self) -> None:
        self.assertIsNone(_history_line(_match("NA1_3", placement=2), "absent"))

    def test_row_timestamp_is_game_datetime_not_future(self) -> None:
        line, _ = _history_line(_match("NA1_4", placement=3), "me")
        self.assertIn("<t:1700000000:R>", line)
        self.assertNotIn("<t:1700001800:R>", line)


class CommandTests(unittest.IsolatedAsyncioTestCase):
    async def _run(self, *, queue=None, ids, matches):
        ctx = SimpleNamespace(
            author=SimpleNamespace(id=1),
            defer=AsyncMock(),
            respond=AsyncMock(),
        )
        client = Mock()
        client.tft_match_ids.return_value = ids
        client.tft_match.side_effect = lambda match_id, server: matches[match_id]
        cog = TftMatchHistoryCommands(Mock())
        with patch(
            "bot_app.commands.tftmatchhistory.tft_target_for",
            AsyncMock(return_value=Target("me", "NA1", "Me#NA1")),
        ), patch(
            "bot_app.commands.tftmatchhistory.get_client", return_value=client
        ), patch(
            "bot_app.commands.tftmatchhistory.riot_to_thread",
            lambda fn, *a, **k: _immediate(fn, *a, **k),
        ), patch(
            "bot_app.commands.tftmatchhistory.set_tft_player_author"
        ):
            await cog.tftmatchhistory.callback(cog, ctx, None, None, queue)
        return ctx

    async def test_lists_recent_placements_with_score(self) -> None:
        ids = ["NA1_1", "NA1_2", "NA1_3"]
        matches = {
            "NA1_1": _match("NA1_1", placement=1),
            "NA1_2": _match("NA1_2", placement=7),
            "NA1_3": _match("NA1_3", placement=3),
        }
        ctx = await self._run(ids=ids, matches=matches)
        embed = ctx.respond.call_args.kwargs["embed"]
        self.assertIn("2 top 4 · 1 bottom 4", embed.title)
        self.assertEqual(embed.description.count("\n\n"), 2)

    async def test_queue_filter_excludes_other_modes(self) -> None:
        ids = ["NA1_1", "NA1_2"]
        matches = {
            "NA1_1": _match("NA1_1", placement=2, queue_id=1100),
            "NA1_2": _match("NA1_2", placement=4, queue_id=1130),
        }
        ctx = await self._run(queue="TFT Ranked", ids=ids, matches=matches)
        embed = ctx.respond.call_args.kwargs["embed"]
        self.assertIn("1 top 4 · 0 bottom 4", embed.title)


async def _immediate(fn, *args, **kwargs):
    return fn(*args, **kwargs)


if __name__ == "__main__":
    unittest.main()

"""Tests for the /laning command's shared render module."""

import unittest
from unittest.mock import patch

from bot_app.laning_render import build_laning_embed


def _frame(minutes: int, mine: dict, theirs: dict) -> dict:
    return {
        "timestamp": minutes * 60_000,
        "participantFrames": {
            "1": {"totalGold": mine["gold"], "xp": mine["xp"], "minionsKilled": mine["cs"], "jungleMinionsKilled": 0},
            "6": {"totalGold": theirs["gold"], "xp": theirs["xp"], "minionsKilled": theirs["cs"], "jungleMinionsKilled": 0},
        },
    }


def _match(position: str = "TOP") -> dict:
    return {
        "info": {
            "queueId": 420,
            "participants": [
                {
                    "participantId": 1,
                    "puuid": "me",
                    "teamId": 100,
                    "teamPosition": position,
                    "championId": 1,
                    "championName": "Annie",
                    "riotIdGameName": "Me",
                },
                {
                    "participantId": 6,
                    "puuid": "them",
                    "teamId": 200,
                    "teamPosition": position,
                    "championId": 2,
                    "championName": "Olaf",
                    "riotIdGameName": "Them",
                },
            ],
        }
    }


def _timeline() -> dict:
    return {
        "info": {
            "frames": [
                _frame(0, {"gold": 500, "xp": 0, "cs": 0}, {"gold": 500, "xp": 0, "cs": 0}),
                _frame(5, {"gold": 2000, "xp": 1800, "cs": 40}, {"gold": 1700, "xp": 1600, "cs": 32}),
                _frame(10, {"gold": 4200, "xp": 3900, "cs": 85}, {"gold": 3600, "xp": 3500, "cs": 70}),
                _frame(15, {"gold": 6600, "xp": 6100, "cs": 130}, {"gold": 5900, "xp": 5700, "cs": 110}),
            ]
        }
    }


class BuildLaningEmbedTests(unittest.IsolatedAsyncioTestCase):
    async def test_renders_checkpoints_and_chart(self) -> None:
        """Verify the embed shows the player/opponent and a diff per checkpoint, plus a chart."""
        with patch("bot_app.laning_render.ddragon.catalog", return_value=None):
            embed, chart = await build_laning_embed(_match(), _timeline(), "me")

        field_names = [field.name for field in embed.fields]
        self.assertIn("You", field_names)
        self.assertIn("Opponent", field_names)
        self.assertIn("5m", field_names)
        self.assertIn("10m", field_names)
        self.assertIn("15m", field_names)
        self.assertIsNotNone(chart)
        self.assertIsNotNone(embed.image)

    async def test_missing_timeline_shows_a_message(self) -> None:
        """Verify a missing timeline degrades to an explanatory embed, not a crash."""
        embed, chart = await build_laning_embed(_match(), None, "me")
        self.assertIn("No timeline is available", embed.description)
        self.assertIsNone(chart)

    async def test_jungle_position_is_unsupported(self) -> None:
        """Verify jungle (no lane opponent) is rejected with a clear message."""
        embed, chart = await build_laning_embed(_match("JUNGLE"), _timeline(), "me")
        self.assertIn("Jungle has no lane opponent", embed.description)
        self.assertIsNone(chart)

    async def test_unknown_player_shows_a_message(self) -> None:
        """Verify a puuid not present in the match degrades gracefully."""
        embed, chart = await build_laning_embed(_match(), _timeline(), "someone-else")
        self.assertIn("Could not find that player", embed.description)
        self.assertIsNone(chart)


if __name__ == "__main__":
    unittest.main()

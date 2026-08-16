"""Announcement presentation helpers."""

import unittest
from unittest.mock import AsyncMock, Mock, patch

import discord

from bot_app.announce import (
    MatchAnnouncement,
    _RankNamesButton,
    _SoloRankButton,
    build_announcement_embed,
    gold_embed,
    remember_match_view_state,
)


class GoldEmbedTests(unittest.TestCase):
    def test_shows_blue_red_and_per_row_gold_difference(self) -> None:
        """Verify that shows blue red and per row gold difference."""
        embed = gold_embed(
            {
                "info": {
                    "participants": [
                        {"participantId": 1, "teamId": 100, "goldEarned": 22_459},
                        {"participantId": 2, "teamId": 100, "goldEarned": 16_830},
                        {"participantId": 6, "teamId": 200, "goldEarned": 13_202},
                        {"participantId": 7, "teamId": 200, "goldEarned": 17_832},
                    ]
                }
            }
        )

        self.assertEqual(
            [field.name for field in embed.fields], ["Blue Team", "Red Team", "Diff"]
        )
        self.assertEqual(embed.fields[0].value, "22,459 gold\n16,830 gold")
        self.assertEqual(embed.fields[1].value, "13,202 gold\n17,832 gold")
        self.assertEqual(embed.fields[2].value, "+9,257 gold\n-1,002 gold")


class ActiveChartFieldTests(unittest.IsolatedAsyncioTestCase):
    """Toggling ranks/name-column must not reset a non-default chart."""

    async def test_build_announcement_embed_renders_the_requested_field(self) -> None:
        """Verify that build_announcement_embed defers to active_field, not the damage chart."""
        announcement = MatchAnnouncement("text", "Victory", {"info": {"participants": []}}, set())
        with (
            patch("bot_app.announce.build_match_columns", return_value=Mock()),
            patch("bot_app.announce.add_team_columns"),
            patch("bot_app.announce.build_damage_chart") as damage_chart,
        ):
            damage_chart.return_value = discord.File.__new__(discord.File)
            damage_chart.return_value.filename = "goldEarned.png"
            await build_announcement_embed(announcement, active_field="goldEarned")
        self.assertEqual(damage_chart.call_args.kwargs["metric_field"], "goldEarned")

    async def test_rank_names_button_keeps_the_currently_shown_chart(self) -> None:
        """Verify that pressing Ranks/Players re-renders the same chart field, not the default."""
        announcement = MatchAnnouncement("text", "Victory", {"info": {"participants": []}}, set())
        button = _RankNamesButton(
            announcement, target_show_rank_names=True, active_field="visionScore"
        )
        interaction = Mock(response=Mock(defer=AsyncMock()), edit_original_response=AsyncMock())
        build = AsyncMock(return_value=(discord.Embed(), None))
        with patch("bot_app.announce.build_announcement_embed", build):
            await button.callback(interaction)
        self.assertEqual(build.call_args.kwargs["active_field"], "visionScore")

    async def test_solo_rank_button_keeps_the_currently_shown_chart(self) -> None:
        """Verify that switching to Solo/Duo ranks re-renders the same chart field."""
        announcement = MatchAnnouncement("text", "Victory", {"info": {"participants": []}}, set())
        button = _SoloRankButton(announcement, active_field="teamGoldDifference")
        interaction = Mock(response=Mock(defer=AsyncMock()), edit_original_response=AsyncMock())
        build = AsyncMock(return_value=(discord.Embed(), None))
        with patch("bot_app.announce.build_announcement_embed", build):
            await button.callback(interaction)
        self.assertEqual(build.call_args.kwargs["active_field"], "teamGoldDifference")


class RememberMatchViewStateTests(unittest.IsolatedAsyncioTestCase):
    async def test_persists_button_state_for_a_real_message(self) -> None:
        """Verify that a message with integer id/channel gets its state saved."""
        announcement = MatchAnnouncement("text", "Victory", {"info": {}}, {"p1"})
        message = Mock(id=42)
        with patch("bot_app.announce.remember_embed_button_state") as remember:
            await remember_match_view_state(message, 7, announcement)
        remember.assert_called_once()
        args, _ = remember.call_args
        self.assertEqual(args[:3], (42, 7, "match"))

    async def test_skips_a_message_with_no_usable_id(self) -> None:
        """Verify that a placeholder/non-integer message id is not persisted."""
        announcement = MatchAnnouncement("text", "Victory", {"info": {}}, {"p1"})
        message = Mock(id="not-an-int")
        with patch("bot_app.announce.remember_embed_button_state") as remember:
            await remember_match_view_state(message, 7, announcement)
        remember.assert_not_called()


if __name__ == "__main__":
    unittest.main()

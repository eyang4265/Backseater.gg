"""Announcement presentation helpers."""

import io
import unittest
from unittest.mock import AsyncMock, Mock, patch

import discord

from bot_app.announce import (
    MatchAnnouncement,
    LiveGameAnnouncement,
    TrackedPlayer,
    _ChartSelect,
    _InventoryChartSelect,
    _MatchDisplaySelect,
    _MatchInventoryView,
    _MatchRatingView,
    _RatingChartSelect,
    _LiveDisplaySelect,
    _CHART_CACHE,
    _build_match_chart,
    _fetch_match_timeline,
    build_live_game_embed,
    build_announcement_embed,
    build_rating_embed,
    format_live_game,
    format_match,
    gold_embed,
    remember_match_view_state,
)
from bot_app.queues import FLEX_QUEUE_ID, SOLO_QUEUE_ID
from bot_app.rating import PlayerRating, RatingBuckets
from bot_app.riot import RiotAPIError


class LiveDisplaySelectTests(unittest.IsolatedAsyncioTestCase):
    """The live lobby uses one Display dropdown for its name-column modes."""

    async def test_ranks_option_replaces_names_and_keeps_rank_queue(self) -> None:
        """Verify that the live Ranks option uses the shared rank-column renderer."""
        announcement = LiveGameAnnouncement(
            "text",
            {"gameQueueConfigId": 420, "participants": []},
            set(),
        )
        select = _LiveDisplaySelect(
            announcement,
            active_mode="players",
            is_flex_lobby=False,
        )
        interaction = _select_with_value(select, "ranks")
        with patch("bot_app.announce.build_live_game_embed", AsyncMock(return_value=discord.Embed())) as build:
            await select.callback(interaction)

        build.assert_awaited_once_with(
            announcement,
            rank_queue_id=None,
            show_rank_names=True,
            show_mastery=False,
        )
        view = interaction.edit_original_response.call_args.kwargs["view"]
        self.assertIsInstance(view.children[0], _LiveDisplaySelect)

    async def test_flex_lobby_offers_flex_and_solo_rank_options(self) -> None:
        """Verify that a Flex lobby's dropdown offers Flex Rank and Solo Rank instead of Ranks."""
        announcement = LiveGameAnnouncement(
            "text",
            {"gameQueueConfigId": FLEX_QUEUE_ID, "participants": []},
            set(),
        )
        select = _LiveDisplaySelect(
            announcement,
            active_mode="players",
            is_flex_lobby=True,
        )
        values = {option.value for option in select.options}
        self.assertEqual(values, {"players", "flex_rank", "solo_rank", "mastery"})

    async def test_flex_rank_option_uses_flex_queue_id(self) -> None:
        """Verify that picking Flex Rank on a Flex lobby requests the Flex queue's standings."""
        announcement = LiveGameAnnouncement(
            "text",
            {"gameQueueConfigId": FLEX_QUEUE_ID, "participants": []},
            set(),
        )
        select = _LiveDisplaySelect(
            announcement,
            active_mode="players",
            is_flex_lobby=True,
        )
        interaction = _select_with_value(select, "solo_rank")
        with patch("bot_app.announce.build_live_game_embed", AsyncMock(return_value=discord.Embed())) as build:
            await select.callback(interaction)

        build.assert_awaited_once_with(
            announcement,
            rank_queue_id=SOLO_QUEUE_ID,
            show_rank_names=True,
            show_mastery=False,
        )

    async def test_live_rank_mode_uses_name_column_without_bottom_rank_rows(self) -> None:
        """Verify that live ranks replace names and are not rendered below the teams."""
        announcement = LiveGameAnnouncement(
            "text",
            {"gameQueueConfigId": 420, "participants": []},
            set(),
        )
        columns = Mock()
        with (
            patch("bot_app.announce.build_lobby_columns", return_value=columns),
            patch("bot_app.announce.add_team_columns") as add_columns,
        ):
            await build_live_game_embed(announcement, show_rank_names=True)

        self.assertFalse(add_columns.call_args.kwargs["include_rank_rows"])


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


class RelativeTimestampTests(unittest.TestCase):
    """The how-long-ago timers on match and live-game announcements."""

    def test_format_match_includes_a_relative_end_timestamp(self) -> None:
        """Verify that the header carries a Discord relative-timestamp for gameEndTimestamp."""
        match = {
            "info": {
                "gameEndTimestamp": 1_700_000_000_000,
                "gameDuration": 1500,
                "queueId": 420,
                "participants": [{"puuid": "p1", "teamId": 100, "win": True}],
            }
        }
        players = [TrackedPlayer(puuid="p1", riot_id="Name#TAG")]
        announcement = format_match(match, players, require_ranked_queue=False)
        self.assertIsNotNone(announcement)
        self.assertIn("<t:1700000000:R>", announcement.text)

    def test_format_match_omits_the_timestamp_when_missing(self) -> None:
        """Verify that a match with no gameEndTimestamp produces no relative-timestamp markup."""
        match = {
            "info": {
                "gameEndTimestamp": 1,
                "gameDuration": 1500,
                "queueId": 420,
                "participants": [{"puuid": "p1", "teamId": 100, "win": True}],
            }
        }
        players = [TrackedPlayer(puuid="p1", riot_id="Name#TAG")]
        announcement = format_match(match, players, require_ranked_queue=False)
        self.assertIn("<t:", announcement.text)

    def test_format_live_game_includes_a_relative_start_timestamp(self) -> None:
        """Verify that the header carries a Discord relative-timestamp for gameStartTime."""
        game = {
            "gameStartTime": 1_700_000_000_000,
            "gameLength": 300,
            "gameQueueConfigId": 420,
            "participants": [{"puuid": "p1", "championId": 1}],
        }
        players = [TrackedPlayer(puuid="p1", riot_id="Name#TAG")]
        announcement = format_live_game(game, players)
        self.assertIsNotNone(announcement)
        self.assertIn("<t:1700000000:R>", announcement.text)


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

    async def test_rendered_chart_bytes_are_reused_with_fresh_discord_files(self) -> None:
        """Repeated views avoid rerendering while receiving independent file handles."""
        match = {"metadata": {"matchId": "NA1_CACHE_TEST"}, "info": {}}
        _CHART_CACHE.clear()
        with patch(
            "bot_app.announce.build_damage_chart",
            side_effect=lambda *args, **kwargs: discord.File(
                io.BytesIO(b"png"), filename=kwargs["filename"]
            ),
        ) as render:
            first = await _build_match_chart(match, "visionScore", set())
            second = await _build_match_chart(match, "visionScore", set())
        self.assertEqual(render.call_count, 1)
        self.assertIsNot(first.fp, second.fp)
        first.close()
        second.close()
        _CHART_CACHE.clear()

    async def test_display_select_ranks_keeps_the_currently_shown_chart(self) -> None:
        """Verify that picking Ranks re-renders the same chart field, not the default."""
        announcement = MatchAnnouncement("text", "Victory", {"info": {"participants": []}}, set())
        select = _MatchDisplaySelect(announcement, active_field="visionScore")
        interaction = Mock(response=Mock(defer=AsyncMock()), edit_original_response=AsyncMock(), data={})
        select._interaction = interaction
        select._selected_values = ["ranks"]
        build = AsyncMock(return_value=(discord.Embed(), None))
        with patch("bot_app.announce.build_announcement_embed", build):
            await select.callback(interaction)
        self.assertEqual(build.call_args.kwargs["active_field"], "visionScore")
        self.assertTrue(build.call_args.kwargs["show_rank_names"])
        self.assertIsNone(build.call_args.kwargs["rank_queue_id"])

    async def test_display_select_rankings_keeps_the_currently_shown_chart(self) -> None:
        """Verify that picking Rankings re-renders the same chart field with solo/duo ranks."""
        announcement = MatchAnnouncement("text", "Victory", {"info": {"participants": []}}, set())
        select = _MatchDisplaySelect(announcement, active_field="teamGoldDifference")
        interaction = Mock(response=Mock(defer=AsyncMock()), edit_original_response=AsyncMock(), data={})
        select._interaction = interaction
        select._selected_values = ["rankings"]
        build = AsyncMock(return_value=(discord.Embed(), None))
        with patch("bot_app.announce.build_announcement_embed", build):
            await select.callback(interaction)
        self.assertEqual(build.call_args.kwargs["active_field"], "teamGoldDifference")
        self.assertTrue(build.call_args.kwargs["show_rank_names"])
        self.assertEqual(build.call_args.kwargs["rank_queue_id"], SOLO_QUEUE_ID)


def _select_with_value(select: discord.ui.Select, value: str) -> Mock:
    """Fake a Discord select interaction that chose ``value``."""
    interaction = Mock(response=Mock(defer=AsyncMock()), edit_original_response=AsyncMock(), data={})
    select._interaction = interaction
    select._selected_values = [value]
    return interaction


class RatingDisplayTests(unittest.IsolatedAsyncioTestCase):
    """The Display dropdown's Ratings option and its rating-mode Chart dropdown."""

    async def test_display_select_ratings_fetches_a_timeline_and_shows_the_rating_view(
        self,
    ) -> None:
        """Verify that picking Ratings computes and displays scores."""
        announcement = MatchAnnouncement(
            "text", "Victory", {"info": {"participants": []}}, set()
        )
        select = _MatchDisplaySelect(announcement)
        interaction = _select_with_value(select, "ratings")
        with (
            patch(
                "bot_app.announce._fetch_match_timeline", AsyncMock(return_value=None)
            ),
            patch("bot_app.announce.rate_match", return_value={}) as rate_match,
        ):
            await select.callback(interaction)

        rate_match.assert_called_once_with(announcement.match, None)
        interaction.response.defer.assert_awaited_once()
        kwargs = interaction.edit_original_response.call_args.kwargs
        self.assertEqual(kwargs["attachments"], [])
        self.assertIsInstance(kwargs["embed"], discord.Embed)
        self.assertIsInstance(kwargs["view"], _MatchRatingView)

    async def test_display_select_ratings_keeps_the_currently_shown_chart(self) -> None:
        """Verify that entering Ratings carries over the active chart field."""
        announcement = MatchAnnouncement(
            "text", "Victory", {"info": {"participants": []}}, set()
        )
        select = _MatchDisplaySelect(announcement, active_field="visionScore")
        interaction = _select_with_value(select, "ratings")
        with (
            patch(
                "bot_app.announce._fetch_match_timeline", AsyncMock(return_value=None)
            ),
            patch("bot_app.announce.rate_match", return_value={}),
            patch(
                "bot_app.announce._build_match_chart", AsyncMock(return_value=None)
            ) as build_chart,
        ):
            await select.callback(interaction)

        self.assertEqual(build_chart.call_args.args[1], "visionScore")
        view = interaction.edit_original_response.call_args.kwargs["view"]
        self.assertTrue(
            any(isinstance(child, _RatingChartSelect) for child in view.children)
        )

    async def test_rating_chart_select_swaps_the_chart_without_recomputing_ratings(
        self,
    ) -> None:
        """Verify that changing the chart in rating mode keeps the rating columns."""
        announcement = MatchAnnouncement(
            "text", "Victory", {"info": {"participants": []}}, set()
        )
        select = _RatingChartSelect(announcement, active_field="totalDamageDealtToChampions")
        rating_embed = discord.Embed(title="Match Ratings")
        interaction = _select_with_value(select, "goldEarned")
        interaction.message = Mock(embeds=[rating_embed])
        with (
            patch(
                "bot_app.announce._build_match_chart", AsyncMock(return_value=None)
            ) as build_chart,
            patch("bot_app.announce.rate_match") as rate_match,
        ):
            await select.callback(interaction)

        build_chart.assert_awaited_once()
        self.assertEqual(build_chart.call_args.args[1], "goldEarned")
        rate_match.assert_not_called()
        kwargs = interaction.edit_original_response.call_args.kwargs
        self.assertEqual(kwargs["embed"].title, "Match Ratings")

    async def test_display_select_players_from_ratings_mode_restores_the_match_view(
        self,
    ) -> None:
        """Verify that picking Players while in ratings mode rebuilds the standard embed."""
        announcement = MatchAnnouncement(
            "text", "Victory", {"info": {"participants": []}}, set()
        )
        select = _MatchDisplaySelect(announcement, mode="ratings")
        interaction = _select_with_value(select, "players")
        build = AsyncMock(return_value=(discord.Embed(), None))
        with patch("bot_app.announce.build_announcement_embed", build):
            await select.callback(interaction)

        build.assert_awaited_once()
        self.assertEqual(
            interaction.edit_original_response.call_args.kwargs["attachments"], []
        )


class ItemsDisplayTests(unittest.IsolatedAsyncioTestCase):
    """The Display dropdown's Items option keeps whichever chart is on screen."""

    async def test_items_option_keeps_the_currently_shown_chart(self) -> None:
        """Verify that entering Items renders the active chart behind the columns."""
        announcement = MatchAnnouncement(
            "text", "Victory", {"info": {"participants": []}}, set()
        )
        select = _MatchDisplaySelect(announcement, active_field="visionScore")
        interaction = _select_with_value(select, "inventory")
        chart_file = discord.File(io.BytesIO(b"png"), filename="visionScore.png")
        with patch(
            "bot_app.announce._build_match_chart", AsyncMock(return_value=chart_file)
        ) as build_chart:
            await select.callback(interaction)

        self.assertEqual(build_chart.call_args.args[1], "visionScore")
        kwargs = interaction.edit_original_response.call_args.kwargs
        self.assertEqual(kwargs["embed"].image.url, "attachment://visionScore.png")
        self.assertIs(kwargs["file"], chart_file)
        view = interaction.edit_original_response.call_args.kwargs["view"]
        self.assertTrue(
            any(isinstance(child, _MatchDisplaySelect) for child in view.children)
        )
        self.assertTrue(
            any(isinstance(child, _InventoryChartSelect) for child in view.children)
        )

    async def test_items_option_without_a_chart_clears_the_image(self) -> None:
        """Verify that no available chart still renders the item columns cleanly."""
        announcement = MatchAnnouncement(
            "text", "Victory", {"info": {"participants": []}}, set()
        )
        select = _MatchDisplaySelect(announcement)
        interaction = _select_with_value(select, "inventory")
        with patch(
            "bot_app.announce._build_match_chart", AsyncMock(return_value=None)
        ):
            await select.callback(interaction)

        kwargs = interaction.edit_original_response.call_args.kwargs
        self.assertIsNone(kwargs["embed"].image)
        self.assertEqual(kwargs["attachments"], [])

    async def test_items_option_keeps_the_win_loss_embed_color(self) -> None:
        """Verify that the side stripe stays the outcome color, not a fixed one."""
        announcement = MatchAnnouncement(
            "text", "Defeat", {"info": {"participants": []}}, set()
        )
        select = _MatchDisplaySelect(announcement)
        interaction = _select_with_value(select, "inventory")
        with patch(
            "bot_app.announce._build_match_chart", AsyncMock(return_value=None)
        ):
            await select.callback(interaction)

        embed = interaction.edit_original_response.call_args.kwargs["embed"]
        self.assertEqual(embed.color, discord.Color.red())

    async def test_inventory_chart_select_swaps_the_chart_without_losing_items(
        self,
    ) -> None:
        """Verify that changing the chart in items mode keeps the item columns."""
        announcement = MatchAnnouncement(
            "text", "Victory", {"info": {"participants": []}}, set()
        )
        select = _InventoryChartSelect(announcement, active_field="totalDamageDealtToChampions")
        item_embed = discord.Embed(title="Items")
        interaction = _select_with_value(select, "goldEarned")
        interaction.message = Mock(embeds=[item_embed])
        with patch(
            "bot_app.announce._build_match_chart", AsyncMock(return_value=None)
        ) as build_chart:
            await select.callback(interaction)

        build_chart.assert_awaited_once()
        self.assertEqual(build_chart.call_args.args[1], "goldEarned")
        kwargs = interaction.edit_original_response.call_args.kwargs
        self.assertEqual(kwargs["embed"].title, "Items")
        self.assertIsInstance(kwargs["view"], _MatchInventoryView)


class JungleProximityChartTests(unittest.IsolatedAsyncioTestCase):
    """The Chart dropdown's Jungle Proximity option, which swaps only the image."""

    _MATCH = {
        "metadata": {"matchId": "NA1_JUNGLE_TEST"},
        "info": {
            "platformId": "NA1",
            "gameDuration": 1200,
            "participants": [
                {"participantId": 1, "teamId": 100, "teamPosition": "JUNGLE"},
                {"participantId": 6, "teamId": 200, "teamPosition": "JUNGLE"},
            ],
        },
    }

    async def test_jungle_proximity_chart_swaps_only_the_image(self) -> None:
        """Picking Jungle Proximity in Chart keeps whatever Display columns are on screen."""
        select = _ChartSelect(self._MATCH, highlight_puuids=set())
        interaction = _select_with_value(select, "jungleProximity")
        existing_embed = discord.Embed(title="Match")
        existing_embed.add_field(name="Blue Team", value="Players...", inline=True)
        interaction.message = Mock(embeds=[existing_embed])
        chart_file = discord.File(io.BytesIO(b"png"), filename="jungleProximity.png")
        client = Mock()
        client.match_timeline.return_value = {"info": {"frames": []}}
        _CHART_CACHE.clear()
        try:
            with (
                patch("bot_app.announce.get_client", return_value=client),
                patch("bot_app.announce.jungle_chart_checkpoints", return_value=[(5, {})]),
                patch(
                    "bot_app.announce.build_jungle_proximity_comparison_chart",
                    return_value=chart_file,
                ),
            ):
                await select.callback(interaction)
        finally:
            _CHART_CACHE.clear()

        kwargs = interaction.edit_original_response.call_args.kwargs
        self.assertEqual([field.name for field in kwargs["embed"].fields], ["Blue Team"])
        self.assertEqual(kwargs["embed"].image.url, "attachment://jungleProximity.png")
        self.assertEqual(kwargs["file"].filename, "jungleProximity.png")

    async def test_jungle_proximity_chart_without_a_timeline_keeps_the_embed_unchanged(
        self,
    ) -> None:
        """No timeline means no chart, but the existing embed and columns stay intact."""
        select = _ChartSelect(self._MATCH, highlight_puuids=set())
        interaction = _select_with_value(select, "jungleProximity")
        existing_embed = discord.Embed(title="Match")
        existing_embed.add_field(name="Blue Team", value="Players...", inline=True)
        interaction.message = Mock(embeds=[existing_embed])
        client = Mock()
        client.match_timeline.return_value = None
        _CHART_CACHE.clear()
        try:
            with (
                patch("bot_app.announce.get_client", return_value=client),
                patch("bot_app.announce.jungle_chart_checkpoints", return_value=None),
            ):
                await select.callback(interaction)
        finally:
            _CHART_CACHE.clear()

        kwargs = interaction.edit_original_response.call_args.kwargs
        self.assertIsNone(kwargs["file"])
        self.assertIsNone(kwargs["embed"].image)
        self.assertEqual([field.name for field in kwargs["embed"].fields], ["Blue Team"])


class FetchMatchTimelineTests(unittest.IsolatedAsyncioTestCase):
    """The shared timeline fetch used by both Ratings and Jungle Proximity."""

    async def test_fetch_match_timeline_degrades_to_none_on_a_riot_api_error(
        self,
    ) -> None:
        """Verify that a failed timeline fetch degrades to None rather than raising."""
        match = {"metadata": {"matchId": "NA1_1"}, "info": {"platformId": "NA1"}}
        with patch(
            "bot_app.announce.get_client",
            return_value=Mock(
                match_timeline=Mock(side_effect=RiotAPIError("boom"))
            ),
        ):
            result = await _fetch_match_timeline(match)
        self.assertIsNone(result)

    async def test_fetch_match_timeline_returns_none_without_a_match_id(self) -> None:
        """Verify that a match with no id is not sent to the Riot client."""
        result = await _fetch_match_timeline({"metadata": {}, "info": {}})
        self.assertIsNone(result)


class BuildRatingEmbedTests(unittest.TestCase):
    def test_unrateable_match_says_so_instead_of_showing_empty_columns(self) -> None:
        """Verify that an unrateable match explains why instead of looking broken."""
        embed = build_rating_embed({"info": {"queueId": 420}}, {})
        self.assertIn("too short", embed.description)
        self.assertEqual(embed.fields, [])

    def test_notes_from_every_rated_player_are_deduplicated_and_shown(self) -> None:
        """Verify that duplicate confidence notes across players collapse to one line."""
        note = "Rated from match data only — timeline unavailable."
        buckets = RatingBuckets(50.0, 50.0, 50.0, 50.0, 50.0, 50.0)
        ratings = {
            "a": PlayerRating(
                puuid="a", participant_id=1, champion="X", role="TOP", team_id=100,
                score=5.0, grade="Average", composite=0.0, buckets=buckets,
                confidence="Limited", notes=(note,),
            ),
            "b": PlayerRating(
                puuid="b", participant_id=2, champion="Y", role="TOP", team_id=200,
                score=5.0, grade="Average", composite=0.0, buckets=buckets,
                confidence="Limited", notes=(note,),
            ),
        }
        match = {
            "info": {
                "queueId": 420,
                "participants": [
                    {"puuid": "a", "teamId": 100, "championName": "X"},
                    {"puuid": "b", "teamId": 200, "championName": "Y"},
                ],
            }
        }
        with patch("bot_app.announce.ddragon.catalog", return_value=None):
            embed = build_rating_embed(match, ratings)
        notes_field = next(f for f in embed.fields if f.name == "Notes")
        self.assertEqual(notes_field.value, f"• {note}")


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

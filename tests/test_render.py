"""Shared embed layout regression tests."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from bot_app import ddragon, emoji
from bot_app.rating import PlayerRating, RatingBuckets
from bot_app.render import (
    RatingColumns,
    TeamColumns,
    _PlayerLookup,
    _Row,
    _columns_from_rows,
    add_rating_columns,
    add_team_columns,
    build_lobby_columns,
    build_match_columns,
    _final_items_text,
    build_rating_columns,
    make_embed,
    rank_text,
)
from bot_app.ranks import RankSnapshot


class InventoryBootFallbackTests(unittest.TestCase):
    """ADC inventory rows recover boots omitted from final Match-V5 slots."""

    @patch("bot_app.render.ddragon.item_metadata", return_value={})
    @patch("bot_app.render.ddragon.item_name", return_value="Item")
    @patch("bot_app.render.emoji_lookup.item_emoji", side_effect=lambda _name, item_id=None: f"<{item_id}>")
    def test_adc_rows_have_seven_core_slots_before_the_trinket(
        self, *_mocks: object
    ) -> None:
        """Unused ADC slots are black squares; other roles retain six slots."""
        adc = _final_items_text({"teamPosition": "BOTTOM", "item0": 3031, "item6": 3340}).split()
        top = _final_items_text({"teamPosition": "TOP", "item0": 3031, "item6": 3340}).split()

        self.assertEqual(adc, ["<3031>", *(["⬛"] * 6), "<3340>"])
        self.assertEqual(top, ["<3031>", *(["⬛"] * 5), "<3340>"])

    @patch("bot_app.render.ddragon.item_metadata", return_value={})
    @patch("bot_app.render.ddragon.item_name", return_value="Item")
    @patch("bot_app.render.emoji_lookup.item_emoji", side_effect=lambda _name, item_id=None: f"<{item_id}>")
    def test_adc_uses_last_purchased_boot_when_final_slots_have_none(
        self, *_mocks: object
    ) -> None:
        """The latest purchased boot is shown even when final slots replaced it."""
        participant = {
            "participantId": 9,
            "teamPosition": "BOTTOM",
            "item0": 3031,
            "item1": 6672,
            "item2": 3036,
            "item3": 3085,
            "item4": 1038,
            "item5": 1037,
            "item6": 3340,
        }
        timeline = {
            "info": {
                "frames": [
                    {"events": [{"type": "ITEM_PURCHASED", "participantId": 9, "itemId": 3006, "timestamp": 100}]},
                    {"events": [{"type": "ITEM_PURCHASED", "participantId": 9, "itemId": 3020, "timestamp": 200}]},
                ]
            }
        }

        text = _final_items_text(participant, timeline)

        self.assertIn("<3020>", text)
        self.assertNotIn("<3006>", text)
        self.assertEqual(len(text.split()), 8)

    @patch("bot_app.render.ddragon.item_metadata", return_value={})
    @patch("bot_app.render.ddragon.item_name", return_value="Item")
    @patch("bot_app.render.emoji_lookup.item_emoji", side_effect=lambda _name, item_id=None: f"<{item_id}>")
    def test_adc_does_not_recover_a_boot_sold_later_in_the_timeline(
        self, *_mocks: object
    ) -> None:
        """A sold or undone boot is not restored from the timeline fallback."""
        participant = {
            "participantId": 9,
            "teamPosition": "BOTTOM",
            "item0": 3031,
            "item1": 6672,
            "item2": 3036,
            "item3": 3085,
            "item4": 1038,
            "item5": 1037,
            "item6": 3340,
        }
        timeline = {
            "info": {
                "frames": [{"events": [
                    {"type": "ITEM_PURCHASED", "participantId": 9, "itemId": 3020, "timestamp": 100},
                    {"type": "ITEM_SOLD", "participantId": 9, "itemId": 3020, "timestamp": 200},
                ]}]
            }
        }

        text = _final_items_text(participant, timeline)

        self.assertNotIn("<3020>", text)

    @patch("bot_app.render.ddragon.item_metadata", return_value={})
    @patch("bot_app.render.ddragon.item_name", return_value="Item")
    @patch("bot_app.render.emoji_lookup.item_emoji", side_effect=lambda _name, item_id=None: f"<{item_id}>")
    def test_adc_keeps_a_boot_moved_to_the_role_quest_slot(
        self, *_mocks: object
    ) -> None:
        """Bot quest completion reports the boot move as ITEM_DESTROYED."""
        participant = {
            "participantId": 9,
            "teamPosition": "BOTTOM",
            "item0": 3031,
            "item1": 6672,
            "item2": 3036,
            "item3": 3085,
            "item4": 1038,
            "item5": 1037,
            "item6": 3340,
        }
        timeline = {
            "info": {
                "frames": [{"events": [
                    {"type": "ITEM_PURCHASED", "participantId": 9, "itemId": 3020, "timestamp": 100},
                    {"type": "ITEM_DESTROYED", "participantId": 9, "itemId": 3020, "timestamp": 200},
                ]}]
            }
        }

        text = _final_items_text(participant, timeline)

        self.assertIn("<3020>", text)

    @patch("bot_app.render.ddragon.item_metadata", return_value={})
    @patch("bot_app.render.ddragon.item_name", return_value="Item")
    @patch("bot_app.render.emoji_lookup.item_emoji", side_effect=lambda _name, item_id=None: f"<{item_id}>")
    def test_adc_keeps_final_boot_instead_of_using_timeline_fallback(
        self, *_mocks: object
    ) -> None:
        """A boot in final slots remains authoritative."""
        participant = {
            "participantId": 9,
            "teamPosition": "BOTTOM",
            "item0": 3031,
            "item1": 3006,
            "item2": 3036,
            "item3": 3085,
            "item4": 1038,
            "item5": 1037,
            "item6": 3340,
        }
        timeline = {
            "info": {
                "frames": [{"events": [
                    {"type": "ITEM_PURCHASED", "participantId": 9, "itemId": 3020, "timestamp": 200},
                ]}]
            }
        }

        text = _final_items_text(participant, timeline)

        self.assertIn("<3006>", text)
        self.assertNotIn("<3020>", text)

    @patch("bot_app.render.ddragon.item_metadata", return_value={})
    @patch("bot_app.render.ddragon.item_name", return_value="Item")
    @patch("bot_app.render.emoji_lookup.item_emoji", side_effect=lambda _name, item_id=None: f"<{item_id}>")
    def test_manamune_3004_does_not_count_as_an_adc_boot(
        self, *_mocks: object
    ) -> None:
        """Manamune 3004 must not suppress recovery of a purchased boot."""
        participant = {
            "participantId": 9,
            "teamPosition": "BOTTOM",
            "item0": 3004,
            "item1": 3031,
            "item2": 6672,
            "item3": 3036,
            "item4": 3085,
            "item5": 1038,
            "item6": 3340,
        }
        timeline = {
            "info": {
                "frames": [{"events": [
                    {"type": "ITEM_PURCHASED", "participantId": 9, "itemId": 3020, "timestamp": 200},
                ]}]
            }
        }

        text = _final_items_text(participant, timeline)

        self.assertIn("<3004>", text)
        self.assertIn("<3020>", text)

    @patch("bot_app.render.ddragon.item_metadata", return_value={})
    @patch("bot_app.render.ddragon.item_name", return_value=None)
    @patch("bot_app.render.emoji_lookup.item_emoji", return_value=None)
    def test_boot_3008_remains_visible_without_data_dragon_or_custom_emoji(
        self, *_mocks: object
    ) -> None:
        """A missing catalog or guild emoji does not hide Gluttonous Greaves."""
        participant = {
            "participantId": 1,
            "teamPosition": "TOP",
            "item0": 3008,
            "item6": 3340,
        }

        text = _final_items_text(participant)

        self.assertIn("🥾", text)

    @patch("bot_app.render.ddragon.item_metadata", return_value={})
    @patch("bot_app.render.emoji_lookup.item_emoji", return_value=None)
    def test_stormrazor_remains_visible_without_custom_emoji(
        self, *_mocks: object
    ) -> None:
        """A final Stormrazor slot remains visible without current assets."""
        participant = {
            "teamPosition": "BOTTOM", "item0": 1055, "item1": 3095,
            "item2": 3031, "item3": 3035, "item6": 3363,
        }

        self.assertEqual(ddragon.item_name(3095), "Stormrazor")
        self.assertEqual(ddragon.item_name(3097), "Stormrazor")
        self.assertIsNone(ddragon.item_name(3094))
        self.assertIn("Stormrazor", _final_items_text(participant))
        participant["item1"] = 3097
        self.assertIn("Stormrazor", _final_items_text(participant))

    @patch("bot_app.emoji._emoji_index", return_value={"stormrazer": "<stormrazer>"})
    def test_stormrazor_uses_legacy_emoji_spelling(self, *_mocks: object) -> None:
        """The existing misspelled asset can render both Stormrazor ids."""
        self.assertEqual(emoji.item_emoji("Stormrazor", item_id=3095), "<stormrazer>")
        self.assertEqual(emoji.item_emoji("Stormrazor", item_id=3097), "<stormrazer>")
        self.assertIsNone(emoji.item_emoji("Rapid Firecannon", item_id=3094))

    @patch("bot_app.render.ddragon.item_metadata", return_value={})
    @patch("bot_app.emoji._emoji_index", return_value={"3097": "<:3097:123>"})
    def test_final_stormrazor_row_uses_icon_instead_of_text(self, *_mocks: object) -> None:
        """An older final slot stays one icon wide when the 3097 asset exists."""
        participant = {"teamPosition": "TOP", "item0": 3153, "item1": 3047, "item2": 3095}

        row = _final_items_text(participant)

        self.assertIn("<:3097:123>", row)
        self.assertNotIn("Stormrazor", row)


class TeamColumnLayoutTests(unittest.TestCase):
    def test_ranked_solo_text_includes_win_rate(self) -> None:
        """Ranked Solo/Duo standings include the account win rate."""
        self.assertEqual(
            rank_text(
                RankSnapshot("GOLD", "II", 42, wins=29, losses=21),
                with_winrate=True,
            ),
            "Gold II (42 LP) · 58%",
        )

    def test_emerald_rank_uses_short_display_label(self) -> None:
        """Individual and team-average rank displays share the Em label."""
        from bot_app.render import average_rank_text

        self.assertEqual(rank_text(RankSnapshot("EMERALD", "II", 42)), "Em II (42 LP)")
        self.assertEqual(
            average_rank_text(RankSnapshot("EMERALD", "II", 42).value),
            "Em II (42 LP)",
        )

    def test_places_team_names_then_team_ranks_in_aligned_rows(self) -> None:
        """Blue/red names occupy the first row and their ranks the second."""
        embed = make_embed("Live game")
        add_team_columns(
            embed,
            TeamColumns(
                blue_names=["Blue player"],
                blue_ranks=["Gold II"],
                red_names=["Red player"],
                red_ranks=["Platinum IV"],
                rank_header="Flex Rank:",
            ),
        )

        self.assertEqual(
            [field.name for field in embed.fields],
            [
                "Blue | Unranked",
                "Red | Unranked",
                "\u200b",
                "\u200b",
                "Blue Flex Rank",
                "Red Flex Rank",
                "\u200b",
            ],
        )
        self.assertEqual(
            [field.value for field in embed.fields[:2]], ["Blue player\n\u200b", "Red player"]
        )
        self.assertEqual(
            [field.value for field in embed.fields[4:6]], ["Gold II\n\u200b", "Platinum IV"]
        )

    def test_arena_rows_are_kept_in_separate_teams(self) -> None:
        """Arena teams do not collapse into blue and red columns."""
        columns = _columns_from_rows(
            [
                _Row(100, "Top", "A", "Gold", 10),
                _Row(200, "Top", "B", "Silver", 5),
                _Row(300, "Top", "C", "Bronze", 1),
                _Row(300, "Top", "D", "Unranked", None),
            ],
            arena=True,
        )
        embed = make_embed("Arena")
        add_team_columns(embed, columns, include_rank_rows=False)

        self.assertEqual(
            [field.name.split(" | ")[0] for field in embed.fields],
            ["Team 1", "Team 2", "Team 3"],
        )
        self.assertIn("C", embed.fields[2].value)
        self.assertIn("D", embed.fields[2].value)

    def test_arena_1750_recovers_six_teams_from_shared_team_ids(self) -> None:
        """Split queue 1750's shared live team id into six groups of three."""
        rows = [_Row(100, "Top", f"Player {index}", "Unranked", None) for index in range(18)]
        columns = _columns_from_rows(rows, arena=True, arena_team_size=3)

        self.assertEqual(len(columns.arena_teams), 6)
        self.assertEqual([len(team[1]) for team in columns.arena_teams], [3] * 6)

    @patch("bot_app.render.tracked_puuids", return_value=())
    @patch("bot_app.render.ddragon.champion_tags_by_internal_id", return_value={})
    @patch("bot_app.render._positions_by_index")
    @patch("bot_app.render.ddragon.catalog", return_value=None)
    @patch("bot_app.render._resolve_concurrently")
    @patch("bot_app.render.emoji_lookup.champion_emoji", return_value=None)
    def test_bravery_arena_match_and_livegame_use_three_player_subteams(
        self,
        _emoji: object,
        resolve: object,
        _catalog: object,
        positions: object,
        _tags: object,
        _tracked: object,
    ) -> None:
        """Both shared announcement renderers group queue 1740 as six 3v3 teams."""
        participants = [
            {
                "puuid": f"p{index}",
                "participantId": index,
                "championId": index,
                "championName": f"Champion {index}",
                "teamId": 100 if subteam <= 3 else 200,
                "playerSubteamId": subteam,
            }
            for index, subteam in enumerate(
                (4, 6, 6, 3, 3, 6, 4, 4, 5, 5, 1, 1, 1, 3, 5, 2, 2, 2),
                start=1,
            )
        ]
        positions.return_value = ["Top"] * len(participants)
        resolve.side_effect = lambda items, _fetch: [
            _PlayerLookup(f"Player {index}#NA1", None, False)
            for index, _item in enumerate(items, start=1)
        ]

        match_columns = build_match_columns(participants, server="na1", queue_id=1740)
        live_columns = build_lobby_columns(
            {"gameQueueConfigId": 1740, "participants": participants}, "na1"
        )

        for columns in (match_columns, live_columns):
            self.assertEqual(len(columns.arena_teams), 6)
            self.assertEqual([len(team[1]) for team in columns.arena_teams], [3] * 6)
            rendered_teams = ["\n".join(team[1]) for team in columns.arena_teams]
            self.assertTrue(
                all(f"Player {index}" in rendered_teams[0] for index in (11, 12, 13))
            )
            self.assertTrue(
                all(f"Player {index}" in rendered_teams[1] for index in (16, 17, 18))
            )

        self.assertEqual(
            [team[0] for team in match_columns.arena_teams],
            [
                "🔵 Team 1",
                "🔴 Team 2",
                "🟢 Team 3",
                "🟡 Team 4",
                "🟣 Team 5",
                "🟠 Team 6",
            ],
        )
        self.assertEqual(
            [team[0] for team in live_columns.arena_teams],
            [f"Team {number}" for number in range(1, 7)],
        )

    @patch("bot_app.render.tracked_puuids", return_value=())
    @patch("bot_app.render._positions_by_index", return_value=["Top"])
    @patch("bot_app.render.ddragon.catalog", return_value=None)
    @patch(
        "bot_app.render._resolve_concurrently",
        return_value=[_PlayerLookup("victorwembanyama#NA1", None, False)],
    )
    @patch("bot_app.render.emoji_lookup.champion_emoji", return_value="<:champ:1>")
    def test_match_rows_keep_full_name_and_compact_kda(
        self, *_mocks: object
    ) -> None:
        """Finished-match rows save width without dropping a Riot ID or K/D/A."""
        columns = build_match_columns(
            [{"puuid": "player", "teamId": 100, "championName": "Ahri", "kills": 2,
              "deaths": 1, "assists": 3}],
            server="na1",
        )

        self.assertEqual(columns.blue_names, ["<:champ:1> victorwembanyama (2/1/3)"])

    @patch("bot_app.render.tracked_puuids", return_value=())
    @patch("bot_app.render._positions_by_index", return_value=["Top"])
    @patch("bot_app.render.ddragon.catalog", return_value=None)
    @patch("bot_app.render._resolve_concurrently", return_value=[_PlayerLookup("فاكهة#NA1", None, False)])
    @patch("bot_app.render.emoji_lookup.champion_emoji", return_value="<:champ:1>")
    def test_match_rows_anchor_rtl_names_with_ltr_stats(self, *_mocks: object) -> None:
        """Arabic names do not push the champion icon to the row's right edge."""
        columns = build_match_columns(
            [{"puuid": "player", "teamId": 200, "championName": "Ahri", "kills": 5,
              "deaths": 8, "assists": 11}],
            server="na1",
        )

        self.assertEqual(
            columns.red_names,
            ["\u2066<:champ:1> \u2067فاكهة\u2069 (5/8/11)\u2069"],
        )

    @patch("bot_app.render.tracked_puuids", return_value=())
    @patch("bot_app.render._positions_by_index", return_value=["Top"])
    @patch("bot_app.render.ddragon.catalog", return_value=None)
    @patch(
        "bot_app.render._resolve_concurrently",
        return_value=[_PlayerLookup("فاكهة#NA1", None, False)],
    )
    @patch("bot_app.render.emoji_lookup.champion_emoji", return_value="<:champ:1>")
    def test_live_rows_anchor_rtl_names_after_the_champion_icon(
        self, *_mocks: object
    ) -> None:
        """Live rows use the same explicit icon/name bidi boundaries."""
        columns = build_lobby_columns(
            {
                "gameQueueConfigId": 420,
                "participants": [
                    {"puuid": "player", "teamId": 200, "championId": 103}
                ],
            },
            server="na1",
        )

        self.assertEqual(
            columns.red_names,
            ["\u2066<:champ:1> \u2067فاكهة\u2069\u2069"],
        )

    @patch("bot_app.render.tracked_puuids", return_value=())
    @patch("bot_app.render._positions_by_index", return_value=["Top"])
    @patch("bot_app.render.ddragon.catalog", return_value=None)
    @patch(
        "bot_app.render._resolve_concurrently",
        return_value=[_PlayerLookup("Player#NA1", None, False)],
    )
    @patch("bot_app.render.emoji_lookup.champion_emoji", return_value="<:champ:1>")
    def test_match_rows_omit_kda_when_showing_rank_names(self, *_mocks: object) -> None:
        """The rank-name column shows just the rank, with no per-player KDA."""
        columns = build_match_columns(
            [{"puuid": "player", "teamId": 100, "championName": "Ahri", "kills": 2,
              "deaths": 1, "assists": 3}],
            server="na1",
            show_rank_names=True,
        )

        self.assertEqual(columns.blue_names, ["<:champ:1> Unranked"])


def _rating(score: float, grade: str, label: str | None = None) -> PlayerRating:
    """Handle rating."""
    return PlayerRating(
        puuid="",
        participant_id=0,
        champion="Ahri",
        role="MIDDLE",
        team_id=100,
        score=score,
        grade=grade,
        composite=0.0,
        buckets=RatingBuckets(50.0, 50.0, 50.0, 50.0, 50.0, 50.0),
        label=label,
    )


class RatingColumnLayoutTests(unittest.TestCase):
    @patch("bot_app.render.ddragon.catalog", return_value=None)
    @patch("bot_app.render.emoji_lookup.champion_emoji", return_value="<:champ:1>")
    def test_score_and_kda_columns_line_up_with_the_name_column(
        self, *_mocks: object
    ) -> None:
        """Verify that score and kda columns line up with the name column."""
        match = {
            "info": {
                "queueId": 420,
                "participants": [
                    {
                        "puuid": "blue",
                        "teamId": 100,
                        "teamPosition": "TOP",
                        "championName": "Ahri",
                        "kills": 2,
                        "deaths": 1,
                        "assists": 3,
                    },
                    {
                        "puuid": "red",
                        "teamId": 200,
                        "teamPosition": "TOP",
                        "championName": "Garen",
                        "kills": 1,
                        "deaths": 4,
                        "assists": 2,
                    },
                ],
            }
        }
        ratings = {
            "blue": _rating(7.4, "A", label="MVP"),
            "red": _rating(3.1, "D"),
        }
        columns = build_rating_columns(match, ratings)

        self.assertEqual(columns.blue_scores, ["7.4 A · MVP"])
        self.assertEqual(columns.blue_kda, ["2/1/3"])
        self.assertEqual(columns.red_scores, ["3.1 D"])
        self.assertEqual(columns.red_kda, ["1/4/2"])

        embed = make_embed("Ratings")
        add_rating_columns(embed, columns)
        self.assertEqual(
            [field.name for field in embed.fields],
            ["Blue", "Score", "K/D/A", "Red", "Score", "K/D/A"],
        )

    def test_unrated_player_shows_an_em_dash(self) -> None:
        """Verify that an unrated player shows an em dash instead of a blank."""
        match = {
            "info": {
                "queueId": 420,
                "participants": [
                    {"puuid": "blue", "teamId": 100, "championName": "Ahri"}
                ],
            }
        }
        with patch("bot_app.render.ddragon.catalog", return_value=None):
            columns = build_rating_columns(match, {})
        self.assertEqual(columns.blue_scores, ["—"])

    def test_empty_lobby_produces_no_columns(self) -> None:
        """Verify that an empty lobby produces no columns."""
        columns = build_rating_columns({"info": {"participants": []}}, {})
        self.assertEqual(columns, RatingColumns())


if __name__ == "__main__":
    unittest.main()

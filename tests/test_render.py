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
    build_inventory_columns,
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

    def test_completed_arena_headings_show_final_placements(self) -> None:
        """Completed Arena teams expose Riot's placement in each heading."""
        columns = _columns_from_rows(
            [
                _Row(10, "Top", "A", "Gold", 10, placement=2),
                _Row(10, "Top", "B", "Gold", 10, placement=2),
                _Row(20, "Top", "C", "Silver", 5, placement=1),
                _Row(20, "Top", "D", "Silver", 5, placement=1),
            ],
            arena=True,
            colored_arena_headings=True,
        )

        self.assertEqual(
            [team[0] for team in columns.arena_teams],
            ["🔵 1st", "🔴 2nd"],
        )
        self.assertEqual([team[1] for team in columns.arena_teams], [["C", "D"], ["A", "B"]])

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
                "placement": subteam,
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
                "🔵 1st",
                "🔴 2nd",
                "🟢 3rd",
                "🟡 4th",
                "🟣 5th",
                "⚪ 6th",
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
    @patch("bot_app.render.teammate_puuids", return_value=("player",))
    def test_mayhem_live_rows_highlight_linked_teammates(
        self, *_mocks: object
    ) -> None:
        """Mayhem linked teammates are highlighted in the shared team columns."""
        columns = build_lobby_columns(
            {
                "gameQueueConfigId": 2400,
                "participants": [
                    {"puuid": "player", "teamId": 200, "championId": 103}
                ],
            },
            server="na1",
        )

        self.assertEqual(
            columns.red_names,
            ["**\u2066<:champ:1> \u2067فاكهة\u2069 ⭐\u2069**"],
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
    @patch("bot_app.render.emoji_lookup.champion_emoji", return_value=None)
    def test_arena_rating_and_item_headings_show_placements(
        self, *_mocks: object
    ) -> None:
        """Every completed-match display keeps the Arena placement heading."""
        match = {
            "info": {
                "queueId": 1740,
                "participants": [
                    {
                        "puuid": "player",
                        "playerSubteamId": 4,
                        "championName": "Ahri",
                        "placement": 2,
                    }
                ],
            }
        }

        self.assertEqual(build_rating_columns(match, {}).arena_teams[0][0], "🔵 2nd")
        self.assertEqual(build_inventory_columns(match).arena_teams[0][0], "🔵 2nd")

    @patch("bot_app.render.ddragon.catalog", return_value=None)
    @patch("bot_app.render.emoji_lookup.champion_emoji", return_value=None)
    def test_arena_rating_and_item_teams_are_sorted_by_placement(
        self, *_mocks: object
    ) -> None:
        """Every completed-match display lists first place before later teams."""
        match = {
            "info": {
                "queueId": 1740,
                "participants": [
                    {"puuid": "later", "playerSubteamId": 1, "championName": "Ahri", "placement": 2},
                    {"puuid": "winner", "playerSubteamId": 8, "championName": "Garen", "placement": 1},
                ],
            }
        }

        self.assertEqual(
            [team[0] for team in build_rating_columns(match, {}).arena_teams],
            ["🔵 1st", "🔴 2nd"],
        )
        self.assertEqual(
            [team[0] for team in build_inventory_columns(match).arena_teams],
            ["🔵 1st", "🔴 2nd"],
        )

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


class PartymateRowTests(unittest.TestCase):
    """Registry markers stay distinct across both shared surfaces and all modes."""

    def test_lonely_marker_replaces_star_on_both_surfaces_and_all_modes(self):
        participants = [{"puuid": puuid, "teamId": team, "championId": 1}
                        for puuid, team in (("tracked", 100), ("other", 100), ("mate", 200))]
        with patch("bot_app.render.load_partymates", return_value={}), \
             patch("bot_app.render.tracked_puuids", return_value=("tracked",)), \
             patch("bot_app.render.teammate_puuids", return_value=("mate",)), \
             patch("bot_app.render.ddragon.catalog", return_value=None), \
             patch("bot_app.render.ddragon.champion_tags_by_internal_id", return_value={}), \
             patch("bot_app.render._positions_by_index", return_value=["Top", "Mid", "Top"]), \
             patch("bot_app.render._lookup_mastery", return_value=123), \
             patch("bot_app.render._lookup_player", return_value=_PlayerLookup("Alice#NA1", None, False)):
            for mode in ({}, {"show_rank_names": True}, {"show_mastery": True}):
                for companion in (False, True):
                    participants[2]["teamId"] = 100 if companion else 200
                    for queue in (420, 2400, 1700, None, 0):
                        for columns in (
                            build_match_columns(participants, server="NA1", queue_id=queue, **mode),
                            build_lobby_columns({"participants": participants,
                                                 "gameQueueConfigId": queue}, "NA1", **mode),
                        ):
                            with self.subTest(mode=mode, companion=companion, queue=queue):
                                row = (columns.arena_teams[0][1][0] if columns.arena_teams
                                       else columns.blue_names[0])
                                lonely = not companion and queue in (420, 2400)
                                self.assertEqual("😢" in row, lonely)
                                self.assertNotIn("⭐", row)
                                self.assertEqual("🫂" in row, not lonely)
                                self.assertTrue(row.startswith("**"))

    def test_hugging_requires_exactly_two_starred_players_in_each_game(self):
        from contextlib import ExitStack
        for count in (1, 2, 3):
            participants = [{"puuid": puuid, "teamId": 100, "championId": 1}
                            for puuid in ["party"] + [f"tracked{i}" for i in range(count)]]
            with ExitStack() as stack:
                for target, value in (
                    ("load_partymates", {"party": object()}),
                    ("tracked_puuids", tuple(f"tracked{i}" for i in range(count))),
                    ("teammate_puuids", ()),
                    ("ddragon.catalog", None),
                    ("ddragon.champion_tags_by_internal_id", {}),
                    ("_positions_by_index", ["Top"] * len(participants)),
                    ("_lookup_mastery", 123),
                    ("_lookup_player", _PlayerLookup("Alice#NA1", None, False)),
                ):
                    stack.enter_context(patch("bot_app.render." + target, return_value=value))
                for mode in ({}, {"show_rank_names": True}, {"show_mastery": True}):
                    for columns in (
                        build_match_columns(participants, server="NA1", queue_id=420, **mode),
                        build_lobby_columns({"participants": participants,
                                             "gameQueueConfigId": 420}, "NA1", **mode),
                    ):
                        with self.subTest(count=count, mode=mode):
                            self.assertIn("👥", columns.blue_names[0])
                            for row in columns.blue_names[1:]:
                                self.assertEqual("🫂" in row, count == 2)
                                self.assertEqual("⭐" in row, count != 2)

    def test_saved_partymate_rows_are_bold_with_marker_in_every_label_mode(self):
        for riot_id in ("Alice#NA1", "שלום#NA1"):
            with patch("bot_app.render.load_partymates", return_value={"party": object(), "tracked": object()}), \
                 patch("bot_app.render.tracked_puuids", return_value=("tracked",)), \
                 patch("bot_app.render.teammate_puuids", return_value=("mate", "tracked")), \
                 patch("bot_app.render.ddragon.catalog", return_value=None), \
                 patch("bot_app.render.ddragon.champion_tags_by_internal_id", return_value={}), \
                 patch("bot_app.render._positions_by_index", return_value=["Top", "Jungle", "Mid", "Bottom"]), \
                 patch("bot_app.render._lookup_mastery", return_value=123), \
                 patch("bot_app.render._lookup_player", side_effect=lambda puuid, *_args:
                       _PlayerLookup(riot_id if puuid == "party" else puuid.title() + "#NA1", None, False)), \
                 patch("bot_app.render.emoji_lookup.champion_emoji", return_value="<:champ:1>"):
                participants = [{"puuid": puuid, "teamId": 100, "championId": 1,
                                 "championName": "Ahri", "kills": 2, "deaths": 1,
                                 "assists": 3} for puuid in ("party", "solo", "tracked", "mate")]
                for mode in ({}, {"show_rank_names": True}, {"show_mastery": True}):
                    with self.subTest(riot_id=riot_id, mode=mode):
                        match = build_match_columns(participants, server="NA1", **mode)
                        live = build_lobby_columns({"participants": participants}, "NA1", **mode)
                        for columns in (match, live):
                            party, solo, tracked, mate = columns.blue_names
                            self.assertIn(" 🫂", tracked)
                            self.assertNotIn("👥", tracked)
                            self.assertIn(" 🫂", mate)
                            self.assertTrue(tracked.startswith("**") and mate.startswith("**"))
                            self.assertTrue(party.startswith("**") and party.endswith("**"))
                            self.assertEqual(party.count("👥"), 1)
                            self.assertNotIn("👥", solo)
                            self.assertFalse(solo.startswith("**"))
                            if not mode:
                                self.assertIn(riot_id.split("#")[0], party)
                            embed = make_embed("Partymates")
                            add_team_columns(embed, columns)
                            self.assertIn(party, embed.fields[0].value)
                            self.assertTrue(all(len(field.value) <= 1024 for field in embed.fields))


if __name__ == "__main__":
    unittest.main()


class RepeatEncounterRowTests(unittest.TestCase):
    def test_relation_markers_match_on_both_surfaces_and_all_modes(self):
        from contextlib import ExitStack
        from bot_app.match_cache import MatchCache
        cache = MatchCache(':memory:')
        self.addCleanup(cache.close)
        # Prior blue ally and red enemy; current sides reverse.
        prior = [{'puuid': 'you', 'teamId': 100}, {'puuid': 'ally', 'teamId': 100},
                 {'puuid': 'enemy', 'teamId': 200}, {'puuid': 'switched', 'teamId': 200}]
        # A player from an older lobby must not regain a marker in this game.
        cache.remember_live_team_games('NA1_0', 2400, 500,
                                      {'you': {'teamId': 100}, 'older': {'teamId': 100}})
        cache.remember_live_team_games('NA1_1', 2400, 1000, {p['puuid']: p for p in prior})
        current = [dict(p, championId=1, teamId=(100 if p['puuid'] == 'enemy' else 200)) for p in prior]
        current.append({'puuid': 'older', 'teamId': 100, 'championId': 1})
        with ExitStack() as stack:
            for name, value in (
                ('get_match_cache', cache), ('load_partymates', {}),
                ('tracked_puuids', ('you',)), ('teammate_puuids', ()),
                ('ddragon.catalog', None), ('ddragon.champion_tags_by_internal_id', {}),
                ('_positions_by_index', ['Top', 'Jungle', 'Top', 'Mid', 'Mid']),
                ('_lookup_mastery', 123),
            ):
                stack.enter_context(patch('bot_app.render.' + name, return_value=value))
            stack.enter_context(patch('bot_app.render._lookup_player',
                                      side_effect=lambda puuid, *_args: _PlayerLookup(puuid, None, False)))
            for mode in ({}, {'show_rank_names': True}, {'show_mastery': True}):
                live = build_lobby_columns({'participants': current, 'gameQueueConfigId': 2400,
                                           'gameStartTime': 2000}, 'NA1', **mode)
                match = build_match_columns(current, server='NA1', queue_id=2400, game_start_ms=2000, **mode)
                for columns in (live, match):
                    self.assertNotIn('👤', columns.blue_names[1])
                    self.assertNotIn('😈', columns.blue_names[1])
                    self.assertNotIn('🔄', columns.blue_names[1])
                    self.assertIn('👤', columns.red_names[1])
                    self.assertIn('😈', columns.blue_names[0])
                    self.assertIn('🔄', columns.red_names[2])
                    self.assertNotIn('🔄', columns.red_names[1])
                    self.assertNotIn('🔄', columns.blue_names[0])
                    self.assertNotIn('👤', columns.red_names[2])
                    self.assertNotIn('😈', columns.red_names[2])
                    self.assertNotIn('👤', columns.red_names[0])
                for queue, start in ((1700, 2000), (None, 2000), (2400, None), (2400, 500)):
                    columns = build_match_columns(current, server='NA1', queue_id=queue, game_start_ms=start, **mode)
                    text = str(columns)
                    self.assertNotIn('👤', text)
                    self.assertNotIn('😈', text)
                    self.assertNotIn('🔄', text)

    def test_registry_markers_override_both_encounter_markers_in_every_mode(self):
        from contextlib import ExitStack
        participants = [{'puuid': p, 'teamId': t, 'championId': 1}
                        for p, t in (('you', 100), ('ally', 100), ('enemy', 200))]
        with ExitStack() as stack:
            cache = stack.enter_context(patch('bot_app.render.get_match_cache')).return_value
            cache.prior_encounters.return_value = {'ally': {True, False}, 'enemy': {True, False}}
            parties = stack.enter_context(patch('bot_app.render.load_partymates', return_value={}))
            tracked = stack.enter_context(patch('bot_app.render.tracked_puuids', return_value=('you',)))
            mates = stack.enter_context(patch('bot_app.render.teammate_puuids', return_value=()))
            for name, value in (
                ('ddragon.catalog', None), ('ddragon.champion_tags_by_internal_id', {}),
                ('_positions_by_index', ['Top', 'Mid', 'Top']), ('_lookup_mastery', 123),
                ('_lookup_player', _PlayerLookup('Name#NA1', None, False)),
            ):
                stack.enter_context(patch('bot_app.render.' + name, return_value=value))
            for registry in ('tracked', 'teammate', 'partymate'):
                tracked.return_value = ('you', 'ally', 'enemy') if registry == 'tracked' else ('you',)
                mates.return_value = ('ally', 'enemy') if registry == 'teammate' else ()
                parties.return_value = {'ally': object(), 'enemy': object()} if registry == 'partymate' else {}
                for mode in ({}, {'show_rank_names': True}, {'show_mastery': True}):
                    for columns in (
                        build_match_columns(participants, server='NA1', queue_id=420, game_start_ms=2000, **mode),
                        build_lobby_columns({'participants': participants, 'gameQueueConfigId': 420,
                                             'gameStartTime': 2000}, 'NA1', **mode),
                    ):
                        with self.subTest(registry=registry, mode=mode):
                            for row in (columns.blue_names[1], columns.red_names[0]):
                                marker = '👥' if registry == 'partymate' else '⭐'
                                if registry == 'tracked':
                                    marker = '😢' if row == columns.red_names[0] else '🫂'  # Existing lonely-tracked behavior stays intact.
                                self.assertIn(marker, row)
                                self.assertNotIn('👤', row)
                                self.assertNotIn('😈', row)
                                self.assertNotIn('🔄', row)

    def test_both_switch_directions_and_mixed_history(self):
        from bot_app.render import _repeat_encounter_markers
        participants = [{'puuid': p, 'teamId': t} for p, t in
                        (('you', 100), ('ex_ally', 200), ('ex_enemy', 100), ('both', 100), ('new', 200))]
        with patch('bot_app.render.get_match_cache') as get_cache:
            get_cache.return_value.prior_encounters.return_value = {
                'ex_ally': {True}, 'ex_enemy': {False}, 'both': {True, False},
            }
            self.assertEqual(_repeat_encounter_markers(participants, 420, 2000, {'you'}),
                             {'ex_ally': ' 🔄', 'ex_enemy': ' 🔄', 'both': ' 👤 🔄'})


class FiveStackRowTests(unittest.TestCase):
    """Full-roster evidence, marker precedence, and read-only rendering."""

    def setUp(self):
        from bot_app.match_cache import MatchCache
        self.cache = MatchCache(':memory:')
        self.addCleanup(self.cache.close)
        self.start = 2_000_000_000_000
        self.roster = [dict(puuid=p, teamId=100, championId=1, championName='Ahri')
                       for p in ('a', 'b', 'c', 'd', 'e')]

    def seed(self, match_id, roster=None, queue=420, end=None):
        end = end or self.start - 1000000
        self.cache.put(match_id, 'NA1', {'info': {
            'queueId': queue, 'gameStartTimestamp': end - 600000,
            'gameEndTimestamp': end, 'gameDuration': 600,
            'participants': roster if roster is not None else self.roster,
        }})

    def test_clowns_replace_all_markers_on_both_surfaces_and_every_mode(self):
        from contextlib import ExitStack
        self.seed('NA1_1')
        self.seed('NA1_2', end=self.start - 2000000)
        with ExitStack() as stack:
            for name, value in (
                ('get_match_cache', self.cache), ('tracked_puuids', ('a',)),
                ('teammate_puuids', ('b',)), ('load_partymates', {'c': object()}),
                ('ddragon.catalog', None), ('ddragon.champion_tags_by_internal_id', {}),
                ('_positions_by_index', ['Top', 'Jungle', 'Mid', 'Bottom', 'Support']),
                ('_lookup_mastery', 123),
            ):
                stack.enter_context(patch('bot_app.render.' + name, return_value=value))
            stack.enter_context(patch('bot_app.render._lookup_player',
                                      side_effect=lambda puuid, *_: _PlayerLookup(puuid, None, False)))
            for mode in ({}, {'show_rank_names': True}, {'show_mastery': True}):
                for columns in (
                    build_match_columns(self.roster, server='NA1', queue_id=420,
                                        game_start_ms=self.start, **mode),
                    build_lobby_columns({'participants': self.roster, 'gameQueueConfigId': 420,
                                         'gameStartTime': self.start}, 'NA1', **mode),
                ):
                    self.assertEqual(len(columns.blue_names), 5)
                    for row in columns.blue_names:
                        self.assertEqual(row.count('🤡'), 1)
                        for marker in ('⭐', '👥', '😢', '👤', '😈', '🔄'):
                            self.assertNotIn(marker, row)
                    self.assertTrue(all(row.startswith('**') for row in columns.blue_names[:3]))

    def test_incomplete_rosters_pairs_and_insufficient_history_do_not_qualify(self):
        from bot_app.render import _five_stack_puuids
        with patch('bot_app.render.get_match_cache', return_value=self.cache):
            self.seed('NA1_1')
            self.assertFalse(_five_stack_puuids(self.roster, 420, self.start))
            self.seed('NA1_2', roster=[dict(p, teamId=200 if p['puuid'] == 'e' else 100)
                                        for p in self.roster])
            self.assertFalse(_five_stack_puuids(self.roster, 420, self.start))
            self.seed('NA1_3')
            self.assertEqual(_five_stack_puuids(self.roster, 420, self.start), set('abcde'))
            for roster, queue, start in (
                (self.roster[:4], 420, self.start),
                ([dict(p, bot=True) for p in self.roster], 420, self.start),
                (self.roster, 1700, self.start), (self.roster, 420, None),
                (self.roster, 420, self.start + 8 * 86400000),
            ):
                self.assertFalse(_five_stack_puuids(roster, queue, start))
            for index in range(5):
                self.seed(f'NA1_extra{index}', roster=[self.roster[0]], end=self.start - index - 1)
            self.assertFalse(_five_stack_puuids(self.roster, 420, self.start))

    def test_mayhem_requires_three_observations_and_rendering_adds_none(self):
        from bot_app.render import _five_stack_puuids
        roster = {p['puuid']: p for p in self.roster}
        with patch('bot_app.render.get_match_cache', return_value=self.cache):
            for index in range(2):
                self.cache.remember_live_team_games(f'NA1_{index}', 2400,
                                                    self.start - (2-index)*1000000, roster)
            self.seed('NA1_finished', queue=2400)
            self.assertFalse(_five_stack_puuids(self.roster, 2400, self.start))
            self.assertEqual(len(self.cache.recent_live_team_games('a', 2400, self.start)), 2)
            self.cache.remember_live_team_games('NA1_current', 2400, self.start, roster)
            self.assertEqual(_five_stack_puuids(self.roster, 2400, self.start), set('abcde'))
            self.assertFalse(_five_stack_puuids(self.roster, 2400, self.start - 1))

    def test_full_known_team_overrides_stars_without_history_or_timing(self):
        from contextlib import ExitStack
        from bot_app.render import _five_stack_puuids
        with ExitStack() as stack:
            for name, value in (
                ('get_match_cache', self.cache), ('tracked_puuids', tuple('abcde')),
                ('teammate_puuids', ()), ('load_partymates', {}),
                ('ddragon.catalog', None), ('ddragon.champion_tags_by_internal_id', {}),
                ('_positions_by_index', ['Top', 'Jungle', 'Mid', 'Bottom', 'Support']),
                ('_lookup_mastery', 123),
            ):
                stack.enter_context(patch('bot_app.render.' + name, return_value=value))
            stack.enter_context(patch('bot_app.render._lookup_player',
                                      side_effect=lambda puuid, *_: _PlayerLookup(puuid, None, False)))
            for mode in ({}, {'show_rank_names': True}, {'show_mastery': True}):
                for columns in (
                    build_match_columns(self.roster, server='NA1', queue_id=440, **mode),
                    build_lobby_columns({'participants': self.roster, 'gameQueueConfigId': 440},
                                        'NA1', **mode),
                ):
                    self.assertEqual(len(columns.blue_names), 5)
                    for row in columns.blue_names:
                        self.assertIn('🤡', row)
                        self.assertNotIn('⭐', row)
                        self.assertTrue(row.startswith('**'))
            self.assertEqual(_five_stack_puuids(self.roster, 440, None, set('abcde')), set('abcde'))
            self.assertEqual(_five_stack_puuids(self.roster, 440, None, set('abcd')), set('abcde'))
            split = [dict(p, teamId=200 if p['puuid'] == 'e' else 100) for p in self.roster]
            self.assertFalse(_five_stack_puuids(split, 440, None, set('abcde')))

    def test_flex_four_known_players_mark_whole_team_on_both_surfaces(self):
        """Four mixed known identities qualify only in Flex without adding the fifth."""
        from contextlib import ExitStack
        from bot_app.render import _five_stack_puuids
        with ExitStack() as stack:
            for name, value in (
                ('get_match_cache', self.cache), ('tracked_puuids', ('a',)),
                ('teammate_puuids', ('b',)), ('load_partymates', {'c': {}, 'd': {}}),
                ('ddragon.catalog', None), ('ddragon.champion_tags_by_internal_id', {}),
                ('_positions_by_index', ['Top', 'Jungle', 'Mid', 'Bottom', 'Support']),
                ('_lookup_mastery', 123),
            ):
                stack.enter_context(patch('bot_app.render.' + name, return_value=value))
            stack.enter_context(patch('bot_app.render._lookup_player',
                                      side_effect=lambda puuid, *_: _PlayerLookup(puuid, None, False)))
            for mode in ({}, {'show_rank_names': True}, {'show_mastery': True}):
                for columns in (
                    build_match_columns(self.roster, server='NA1', queue_id=440, **mode),
                    build_lobby_columns({'participants': self.roster, 'gameQueueConfigId': 440},
                                        'NA1', **mode),
                ):
                    self.assertEqual(len(columns.blue_names), 5)
                    for row in columns.blue_names:
                        self.assertEqual(row.count('🤡'), 1)
                        self.assertFalse(any(m in row for m in ('⭐', '👥', '😢', '🫂')))
            for queue in (420, 400, 450, 2400):
                self.assertFalse(_five_stack_puuids(self.roster, queue, None, set('abcd')))
            self.assertFalse(_five_stack_puuids(self.roster, 440, None, set('abc')))
            self.assertFalse(_five_stack_puuids(self.roster[:4], 440, None, set('abcd')))
            self.assertFalse(_five_stack_puuids(
                [dict(p, bot=p['puuid'] == 'e') for p in self.roster], 440, None, set('abcd')))
            split = [dict(p, teamId=200 if p['puuid'] == 'd' else 100) for p in self.roster]
            self.assertFalse(_five_stack_puuids(split, 440, None, set('abcd')))

"""Shared embed layout regression tests."""

import unittest
from unittest.mock import patch

from bot_app.render import (
    NameStyle,
    TeamColumns,
    _PlayerLookup,
    _Row,
    _columns_from_rows,
    add_team_columns,
    build_match_columns,
    make_embed,
)


class TeamColumnLayoutTests(unittest.TestCase):
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
                "Blue Team | Unranked",
                "Red Team | Unranked",
                "\u200b",
                "\u200b",
                "Blue Team Flex Rank",
                "Red Team Flex Rank",
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
            ["Arena Team 1", "Arena Team 2", "Arena Team 3"],
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
    @patch("bot_app.render._positions_by_index", return_value=["Top"])
    @patch("bot_app.render.ddragon.catalog", return_value=None)
    @patch(
        "bot_app.render._resolve_concurrently",
        return_value=[_PlayerLookup("Player#NA1", None, False)],
    )
    @patch("bot_app.render.emoji_lookup.champion_emoji", return_value="<:champ:1>")
    def test_match_rows_separate_name_or_rank_from_kda_with_centered_dot(
        self, *_mocks: object
    ) -> None:
        """Finished-match player labels use a centered dot before their KDA."""
        columns = build_match_columns(
            [{"puuid": "player", "teamId": 100, "championName": "Ahri", "kills": 2,
              "deaths": 1, "assists": 3}],
            server="na1",
            name_style=NameStyle.SUMMONER,
        )

        self.assertEqual(columns.blue_names, ["<:champ:1> Player · (2/1/3)"])

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


if __name__ == "__main__":
    unittest.main()

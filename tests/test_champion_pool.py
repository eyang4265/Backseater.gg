"""Behavior checks for the recent champion pool report."""

import unittest
from unittest.mock import patch

from bot_app.champion_pool import aggregate_pool
from bot_app.commands.championpool import _pool_embed


def match(champion, win, *, role="TOP", queue=420, duration=1800, puuid="player"):
    """Build one relevant Match-V5 payload in newest-first test order."""
    return {"info": {"queueId": queue, "gameDuration": duration, "participants": [{
        "puuid": puuid, "championName": champion, "teamPosition": role, "win": win,
    }]}}


class ChampionPoolTests(unittest.TestCase):
    def test_groups_by_role_and_excludes_nonstandard_games(self):
        matches = [match("Ahri", win) for win in (True, False, True, True, False, False)]
        matches += [match("Ahri", True, role="MIDDLE"), match("Garen", True, queue=450)]
        matches += [match("Ahri", True, duration=900), match("Ahri", True, puuid="other")]
        rows = aggregate_pool(matches, "player")
        self.assertEqual(len(rows), 2)
        top = next(row for row in rows if row.role == "TOP")
        self.assertEqual((top.games, top.wins), (6, 3))

    def test_queue_scope_and_embed_columns(self):
        matches = [match("Jinx", True, role="BOTTOM", queue=420), match("Jinx", False, role="BOTTOM", queue=440)]
        rows = aggregate_pool(matches, "player", "Ranked Solo/Duo")
        self.assertEqual((rows[0].games, rows[0].wins), (1, 1))
        embed = _pool_embed("Player#NA1", matches, "player", "All SR", 2, 0)
        self.assertEqual([field.name for field in embed.fields], ["ADC", "Record · WR", "\u200b"])
        self.assertIn("2 eligible games", embed.description)
        self.assertIsNone(embed.footer)

    def test_each_role_starts_a_new_embed_row(self):
        matches = [match("Garen", True), match("Jinx", False, role="BOTTOM")]
        embed = _pool_embed("Player#NA1", matches, "player", "All SR", 2, 0)
        self.assertEqual(
            [field.name for field in embed.fields],
            ["Top", "Record · WR", "\u200b", "ADC", "Record · WR", "\u200b"],
        )

    def test_champion_icon_precedes_name_with_name_fallback(self):
        with patch(
            "bot_app.commands.championpool.emoji.champion_emoji",
            side_effect=lambda _champion, *, name: "<:Garen:1>" if name == "Garen" else None,
        ):
            embed = _pool_embed(
                "Player#NA1", [match("Garen", True), match("Jinx", False)],
                "player", "All SR", 2, 0,
            )
        self.assertEqual(embed.fields[0].value, "<:Garen:1> Garen\nJinx")

    def test_player_title_links_to_opgg_even_without_eligible_games(self):
        profile = "https://op.gg/lol/summoners/na/Player-NA1?hl=en_US"
        embed = _pool_embed("Player#NA1", [], "player", "All SR", 0, 0, profile)
        self.assertEqual(embed.url, profile)


if __name__ == "__main__":
    unittest.main()

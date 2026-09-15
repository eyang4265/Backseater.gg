"""OP.GG champion-stat parsing and caching adapter tests."""

import unittest
from unittest.mock import Mock, patch

from bot_app.commands.champ import _situational_icons
from bot_app.opgg import (
    ChampionStats,
    fetch_champion_stats,
    opgg_trends_url,
    parse_game_length_win_rates,
    parse_champion_stats,
    parse_role_champions,
)


class OPGGStatsTests(unittest.TestCase):
    def test_parse_role_champions_and_game_length_rates(self) -> None:
        """Extract the selected role list and every game-length curve point."""
        role_html = (
            r'\"key\":\"jinx\",\"name\":\"Jinx\",\"image_url\":\"x\",'
            r'\"positionName\":\"ADC\" '
            r'\"key\":\"garen\",\"name\":\"Garen\",\"image_url\":\"x\",'
            r'\"positionName\":\"TOP\"'
        )
        self.assertEqual(parse_role_champions(role_html, "adc"), (("Jinx", "jinx"),))
        curve_html = (
            r'\"game_length\":0,\"rate\":52.34,\"average\":50,\"rank\":14,'
            r'\"game_length\":25,\"rate\":52.37,\"average\":50,\"rank\":10'
        )
        self.assertEqual(parse_game_length_win_rates(curve_html), {0: 52.34, 25: 52.37})

    def test_trends_url_has_role_and_english(self) -> None:
        """Keep the aggregate scan pointed at the matching OP.GG role list."""
        self.assertEqual(
            opgg_trends_url("support"),
            "https://op.gg/lol/champions?position=support&region=global&hl=en_US",
        )

    def test_parse_headline_stats(self) -> None:
        """Verify that headline values are extracted from the page response."""
        html = (
            r'\"rateWin\":50.3055,\"ratePick\":9.74444,\"rateBan\":4.45804'
            r' ... \"children\":\"1 Tier\" ... patch 16.16'
        )
        stats = parse_champion_stats(html)
        self.assertAlmostEqual(stats.win_rate, 50.3055)
        self.assertAlmostEqual(stats.pick_rate, 9.74444)
        self.assertAlmostEqual(stats.ban_rate, 4.45804)
        self.assertEqual((stats.tier, stats.patch), ("1 Tier", "16.16"))

    def test_parse_build_details(self) -> None:
        """Verify skills, starter items, and runes are retained for rendering."""
        html = (
            r'\"rateWin\":50,\"ratePick\":5,\"rateBan\":1 '
            r'\"starter_items_0\": ... \"itemId\":1054,\"alt\":\"Doran\'s Ring\",\"count\":2 '
            r'\"rune_pages\": {\"main_runes\": '
            r'\"name\":\"Arcane Comet\" ... \"isActive\":true '
            r'\"skill_order\":[\"Q\",\"W\",\"E\",\"Q\"]'
        )
        stats = parse_champion_stats(html)
        self.assertEqual(stats.starter_items, ("Doran's Ring",))
        self.assertEqual(stats.starter_item_ids, ("1054",))
        self.assertEqual(stats.starter_item_counts, (2,))
        self.assertEqual(stats.runes, ("Arcane Comet",))
        self.assertEqual(stats.skill_order, ("Q", "W", "E", "Q"))

    def test_parse_item_quantity_before_icon_name(self) -> None:
        """Verify quantities placed before an item icon are retained."""
        html = (
            r'\"rateWin\":50,\"ratePick\":5,\"rateBan\":1 '
            r'\"starter_items_0\": ... \"count\":2,\"alt\":\"Health Potion\"'
        )
        stats = parse_champion_stats(html)
        self.assertEqual(stats.starter_items, ("Health Potion",))
        self.assertEqual(stats.starter_item_counts, (2,))

    def test_parse_item_quantity_from_current_opgg_badge(self) -> None:
        """Read the item count from OP.GG's React Flight quantity badge."""
        html = (
            r'\"rateWin\":50,\"ratePick\":5,\"rateBan\":1 '
            r'\"starter_items_0\": ... \"itemId\":2003,'
            r'\"alt\":\"Health Potion\", ... '
            r'\"className\":\"absolute bottom-0 right-0 flex size-4\",'
            r'\"children\":2'
        )
        stats = parse_champion_stats(html)
        self.assertEqual(stats.starter_items, ("Health Potion",))
        self.assertEqual(stats.starter_item_counts, (2,))

    def test_quantity_badge_applies_only_to_its_item(self) -> None:
        """Do not apply a potion x2 badge to the preceding starter item."""
        html = (
            r'\"rateWin\":50,\"ratePick\":5,\"rateBan\":1 '
            r'\"starter_items_0\": ... \"alt\":\"Doran\'s Ring\" ... '
            r'\"alt\":\"Health Potion\" ... '
            r'\"className\":\"absolute bottom-0 right-0 flex size-4\",'
            r'\"children\":2'
        )
        stats = parse_champion_stats(html)
        self.assertEqual(stats.starter_items, ("Doran's Ring", "Health Potion"))
        self.assertEqual(stats.starter_item_counts, (1, 2))

    def test_parse_situational_items_from_fourth_item_rows(self) -> None:
        """Extract the leading fourth-item recommendations for the build panel."""
        html = (
            r'\"rateWin\":50,\"ratePick\":5,\"rateBan\":1 '
            r'\"depth_4_item_0\": ... \"itemId\":3157,'
            r'\"alt\":\"Zhonya\'s Hourglass\" '
            r'\"depth_4_item_1\": ... \"itemId\":3135,'
            r'\"alt\":\"Void Staff\"'
        )
        stats = parse_champion_stats(html)
        self.assertEqual(stats.situational_items, ("Zhonya's Hourglass", "Void Staff"))
        self.assertEqual(stats.situational_item_ids, ("3157", "3135"))

    def test_situational_items_fill_from_later_item_depths(self) -> None:
        """Include fifth-item ideas when fourth-item options are exhausted."""
        html = (
            r'\"rateWin\":50,\"ratePick\":5,\"rateBan\":1 '
            r'\"depth_4_item_0\": ... \"itemId\":3157,'
            r'\"alt\":\"Zhonya\'s Hourglass\" '
            r'\"depth_5_item_0\": ... \"itemId\":3135,'
            r'\"alt\":\"Void Staff\" '
            r'\"depth_6_item_0\": ... \"itemId\":3089,'
            r'\"alt\":\"Rabadon\'s Deathcap\"'
        )
        stats = parse_champion_stats(html)
        self.assertEqual(
            stats.situational_items,
            ("Zhonya's Hourglass", "Void Staff", "Rabadon's Deathcap"),
        )

    def test_situational_items_exclude_mejais(self) -> None:
        """Never offer Mejai's Soulstealer as a situational recommendation."""
        stats = ChampionStats(
            50.0, 10.0, 2.0, "1 Tier", "16.16", "mid", (), (), (), None, None, (),
            situational_items=("Mejai's Soulstealer", "Void Staff"),
            situational_item_ids=("3041", "3135"),
        )
        with patch("bot_app.commands.champ.item_emoji", side_effect=lambda name, **_: name):
            self.assertEqual(_situational_icons(stats), "Void Staff")

    def test_fetch_uses_short_cache(self) -> None:
        """Verify that repeated requests for one champion use the cache."""
        response = Mock(status_code=200)
        response.text = r'\"rateWin\":50,\"ratePick\":5,\"rateBan\":1'
        with patch("bot_app.opgg.requests.get", return_value=response) as get:
            first = fetch_champion_stats("NA1", "Ahri")
            second = fetch_champion_stats("NA1", "Ahri")
        self.assertEqual(first, second)
        get.assert_called_once()

    def test_position_result_keeps_the_returned_position(self) -> None:
        """Verify that fetched position results can populate position buttons."""
        stats = ChampionStats(
            50.0,
            10.0,
            2.0,
            "1 Tier",
            "16.16",
            "mid",
            (),
            (),
            (),
            None,
            None,
            (),
        )
        results = [stats, RuntimeError("position unavailable")]
        by_position = {
            result.position: result
            for result in results
            if isinstance(result, ChampionStats)
        }
        self.assertEqual(by_position, {"mid": stats})


if __name__ == "__main__":
    unittest.main()

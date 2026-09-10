"""Every offered platform must produce usable request hosts."""

import unittest

from bot_app.routing import (
    SERVERS,
    account_route,
    match_route,
    opgg_champion_url,
    opgg_url,
    platform,
    split_riot_id,
)

_ACCOUNT_ROUTES = {"americas", "asia", "europe"}
_MATCH_ROUTES = {"americas", "asia", "europe", "sea"}


class RoutingTests(unittest.TestCase):
    def test_global_opgg_champion_url(self) -> None:
        """Global champion stats do not require a platform slug."""
        self.assertEqual(
            opgg_champion_url("GLOBAL", "Syndra", "mid"),
            "https://op.gg/lol/champions/Syndra/build/mid?region=global&hl=en_US",
        )

    def test_every_offered_server_routes(self) -> None:
        """Verify that every offered server routes."""
        for code in SERVERS:
            with self.subTest(server=code):
                self.assertIn(account_route(code), _ACCOUNT_ROUTES)
                self.assertIn(match_route(code), _MATCH_ROUTES)

    def test_routing_is_case_insensitive(self) -> None:
        """Verify that routing is case insensitive."""
        self.assertEqual(account_route("na1"), "americas")
        self.assertEqual(match_route("oc1"), "sea")

    def test_unknown_platform_returns_none(self) -> None:
        """Verify that unknown platform returns none."""
        self.assertIsNone(platform("ZZ9"))
        self.assertIsNone(account_route("ZZ9"))
        self.assertIsNone(match_route(None))

    def test_opgg_url_escapes_names(self) -> None:
        """Verify that opgg url escapes names."""
        self.assertEqual(
            opgg_url("NA1", "Some Name#NA1"),
            "https://op.gg/lol/summoners/na/Some%20Name-NA1?hl=en_US",
        )

    def test_opgg_url_absent_for_unsupported_platform_or_malformed_id(self) -> None:
        """Verify that opgg url absent for unsupported platform or malformed id."""
        self.assertIsNone(opgg_url("PBE1", "Name#TAG"))
        self.assertIsNone(opgg_url("NA1", "no-hash-here"))

    def test_opgg_champion_url_uses_server_and_position(self) -> None:
        """Verify that champion stats URLs use the OP.GG region and position."""
        self.assertEqual(
            opgg_champion_url("NA1", "AurelionSol", "mid"),
            "https://op.gg/lol/champions/AurelionSol/build/mid?region=na&hl=en_US",
        )

    def test_split_riot_id_prefers_an_explicit_tag(self) -> None:
        """Verify that split riot id prefers an explicit tag."""
        self.assertEqual(split_riot_id("Name#EUW", None), ("Name", "EUW"))
        self.assertEqual(split_riot_id("Name#EUW", "NA1"), ("Name#EUW", "NA1"))
        self.assertEqual(split_riot_id("Name", None), ("Name", None))


if __name__ == "__main__":
    unittest.main()

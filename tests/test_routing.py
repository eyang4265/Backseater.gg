"""Every offered platform must produce usable request hosts."""

import unittest

from bot_app.routing import (
    SERVERS,
    account_route,
    match_route,
    opgg_url,
    platform,
    split_riot_id,
)

_ACCOUNT_ROUTES = {"americas", "asia", "europe"}
_MATCH_ROUTES = {"americas", "asia", "europe", "sea"}


class RoutingTests(unittest.TestCase):
    def test_every_offered_server_routes(self) -> None:
        # Regression: RU, OC1, PH2, SG2, TH2, VN2 previously resolved to None
        # and built requests against the host "None.api.riotgames.com".
        for code in SERVERS:
            with self.subTest(server=code):
                self.assertIn(account_route(code), _ACCOUNT_ROUTES)
                self.assertIn(match_route(code), _MATCH_ROUTES)

    def test_routing_is_case_insensitive(self) -> None:
        self.assertEqual(account_route("na1"), "americas")
        self.assertEqual(match_route("oc1"), "sea")

    def test_unknown_platform_returns_none(self) -> None:
        self.assertIsNone(platform("ZZ9"))
        self.assertIsNone(account_route("ZZ9"))
        self.assertIsNone(match_route(None))

    def test_opgg_url_escapes_names(self) -> None:
        self.assertEqual(
            opgg_url("NA1", "Some Name#NA1"),
            "https://op.gg/lol/summoners/na/Some%20Name-NA1",
        )

    def test_opgg_url_absent_for_unsupported_platform_or_malformed_id(self) -> None:
        self.assertIsNone(opgg_url("PBE1", "Name#TAG"))
        self.assertIsNone(opgg_url("NA1", "no-hash-here"))

    def test_split_riot_id_prefers_an_explicit_tag(self) -> None:
        self.assertEqual(split_riot_id("Name#EUW", None), ("Name", "EUW"))
        self.assertEqual(split_riot_id("Name#EUW", "NA1"), ("Name#EUW", "NA1"))
        self.assertEqual(split_riot_id("Name", None), ("Name", None))


if __name__ == "__main__":
    unittest.main()

"""Champion catalog indexing and fuzzy name resolution (no network)."""

import unittest

from bot_app.ddragon import Champion, ChampionCatalog, normalize

_CHAMPIONS = [
    Champion(key=36, internal_id="DrMundo", name="Dr. Mundo", tags=("Tank", "Fighter")),
    Champion(key=62, internal_id="MonkeyKing", name="Wukong", tags=("Fighter", "Tank")),
    Champion(key=4, internal_id="TwistedFate", name="Twisted Fate", tags=("Mage",)),
    Champion(key=161, internal_id="Velkoz", name="Vel'Koz", tags=("Mage",)),
    Champion(
        key=20, internal_id="Nunu", name="Nunu & Willump", tags=("Tank", "Fighter")
    ),
]


class NormalizeTests(unittest.TestCase):
    def test_strips_punctuation_spacing_and_case(self) -> None:
        """Verify that strips punctuation spacing and case."""
        self.assertEqual(normalize("Vel'Koz"), "velkoz")
        self.assertEqual(normalize("Dr. Mundo"), "drmundo")
        self.assertEqual(normalize("Nunu & Willump"), "nunuwillump")


class CatalogTests(unittest.TestCase):
    def setUp(self) -> None:
        """Prepare fixtures for the test case."""
        self.catalog = ChampionCatalog("15.1.1", _CHAMPIONS)

    def test_lookup_by_numeric_key_accepts_strings(self) -> None:
        """Verify that lookup by numeric key accepts strings."""
        self.assertEqual(self.catalog.by_key(36).name, "Dr. Mundo")
        self.assertEqual(self.catalog.by_key("36").internal_id, "DrMundo")

    def test_unknown_or_malformed_keys_return_none(self) -> None:
        """Verify that unknown or malformed keys return none."""
        self.assertIsNone(self.catalog.by_key(9999))
        self.assertIsNone(self.catalog.by_key("not-a-number"))
        self.assertIsNone(self.catalog.by_key(None))

    def test_query_matches_display_name_internal_id_and_alias(self) -> None:
        """Verify that query matches display name internal id and alias."""
        for query in ("Dr. Mundo", "drmundo", "dr mundo", "mundo"):
            with self.subTest(query=query):
                self.assertEqual(self.catalog.by_query(query).key, 36)

    def test_query_resolves_names_that_differ_from_internal_ids(self) -> None:
        """Verify that query resolves names that differ from internal ids."""
        self.assertEqual(self.catalog.by_query("wukong").key, 62)
        self.assertEqual(self.catalog.by_query("monkeyking").key, 62)
        self.assertEqual(self.catalog.by_query("tf").key, 4)
        self.assertEqual(self.catalog.by_query("velkoz").key, 161)
        self.assertEqual(self.catalog.by_query("nunu").key, 20)

    def test_unknown_query_returns_none(self) -> None:
        """Verify that unknown query returns none."""
        self.assertIsNone(self.catalog.by_query("not a champion"))
        self.assertIsNone(self.catalog.by_query(""))
        self.assertIsNone(self.catalog.by_query(None))

    def test_tags_are_keyed_by_internal_id(self) -> None:
        """Verify that tags are keyed by internal id."""
        self.assertEqual(
            self.catalog.tags_by_internal_id["MonkeyKing"], ("Fighter", "Tank")
        )


if __name__ == "__main__":
    unittest.main()

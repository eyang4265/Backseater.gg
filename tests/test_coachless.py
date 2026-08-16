"""Coachless JSON-API client tests."""
import unittest
from unittest.mock import patch, MagicMock
from bot_app.coachless import CoachlessError, _cache, fetch_build_stage


def _response(payload):
    response = MagicMock()
    response.json.return_value = payload
    response.raise_for_status.return_value = None
    return response


@patch("bot_app.ddragon.current_version", return_value=None)  # keep name lookups network-free
class CoachlessTests(unittest.TestCase):
    def setUp(self):
        _cache.clear()
        import bot_app.coachless as coachless_module
        coachless_module._patch_cache = (0.0, {"major": 16, "patch": 15, "patchAdditions": 0})

    @patch("bot_app.coachless._session.post")
    def test_all_stages_share_one_fetch(self, post, _version):
        post.side_effect = lambda url, json, timeout: _response(
            [{"rune": 8112, "wpaOverall": 1.2, "occurrence": 10}] if "GetKeystoneData" in url else []
        )
        entries = fetch_build_stage(103, "mid", "runes")
        boots = fetch_build_stage(103, "mid", "boots")
        self.assertEqual(entries[0].identifier, "8112")
        self.assertEqual(entries[0].wpa, 1.2)
        self.assertEqual(entries[0].buys, 10)
        self.assertEqual(boots, ())
        # Runes, summoner spells, and six item stages share one cached fetch.
        self.assertEqual(post.call_count, 8)

    @patch("bot_app.coachless._session.post")
    def test_item_stage_uses_item_id(self, post, _version):
        post.side_effect = lambda url, json, timeout: _response(
            [{"itemId": 6657, "wpaOverall": 1.25, "occurrence": 2500}] if json.get("itemType") == 1 and json.get("itemSlots") == [1] else []
        )
        entries = fetch_build_stage(103, "mid", "1st")
        self.assertEqual(entries[0].identifier, "6657")
        self.assertEqual(entries[0].wpa, 1.25)
        self.assertEqual(entries[0].buys, 2500)

    @patch("bot_app.coachless._session.post")
    def test_empty_results_raise(self, post, _version):
        post.side_effect = lambda url, json, timeout: _response([])
        with self.assertRaises(CoachlessError):
            fetch_build_stage(103, "mid", "runes")

    def test_unsupported_role_or_stage_raises(self, _version):
        with self.assertRaises(CoachlessError):
            fetch_build_stage(103, "invalid-role", "runes")
        with self.assertRaises(CoachlessError):
            fetch_build_stage(103, "mid", "invalid-stage")


if __name__ == "__main__": unittest.main()

"""Saved winner recovery without external metadata sources."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from bot_app import worlds_game_details as details
from tests.test_worlds_stats import fixture


class GameDetailsTests(unittest.TestCase):
    def setUp(self):
        self.series, self.game, _, _ = fixture()
        self.series["score"] = [1, 1]
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        cache = patch.object(details, "CACHE_DIR", Path(self.temp.name))
        cache.start()
        self.addCleanup(cache.stop)

    def test_saved_sweep_recovers_both_games_without_network(self):
        self.series.update(score=[0, 2], state="completed")
        self.series["games"].append({"id": "124", "number": 2})
        with patch("bot_app.lolesports.requests.get") as get:
            for game in self.series["games"]:
                self.assertEqual(details.load_game_details(self.series, game)["winner"], "b")
            get.assert_not_called()

    def test_saved_score_delta_maps_reversed_teams_and_survives_missing_sources(self):
        self.series.update(score=[1, 2], state="completed")
        self.series["games"] += [{"id": "124", "number": 2}, {"id": "125", "number": 3}]
        previous = dict(self.series, teams=["b", "a"], score=[1, 0], state="inProgress")
        tied = dict(self.series, score=[1, 1], state="inProgress")
        state = {"channels": {"10": {"series": {self.series["id"]: {
            "stats_games": {"123": {"series": previous}, "124": {"series": tied}}}}}}}
        with patch.object(details, "read_json", side_effect=lambda path, default: state if path.name == "worlds.json" else None):
            self.assertEqual([details.load_game_details(self.series, game)["winner"]
                              for game in self.series["games"]], ["b", "a", "b"])

    def test_final_score_does_not_invent_earlier_winner(self):
        self.series.update(score=[2, 1], state="completed")
        with patch.object(details, "read_json", return_value={}):
            self.assertIsNone(details.load_game_details(self.series, self.game))

    def test_saved_metadata_is_reused_without_network(self):
        from bot_app.store import write_json
        saved = {"series_id": self.series["id"], "game_id": self.game["id"],
                 "game_number": 1, "teams": ["b", "a"], "winner": "b"}
        write_json(details.CACHE_DIR / (self.game["id"] + ".json"), saved)
        with patch("bot_app.lolesports.requests.get") as get:
            self.assertEqual(details.load_game_details(self.series, self.game), saved)
            get.assert_not_called()

"""Completed match cache and aggregate queries."""

import json
import sqlite3
import unittest
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

from bot_app.lane_matchups.extract import LaneOutcome
from bot_app.match_cache import MatchCache, lane_composite_scores


def _payload(end=2_000_000_000_000, *, remake=False, same_team=True):
    """Handle payload."""
    return {
        "info": {
            "gameEndTimestamp": end,
            "queueId": 420,
            "participants": [
                {
                    "puuid": "a",
                    "teamId": 100,
                    "championName": "Ahri",
                    "win": not remake,
                    "gameEndedInEarlySurrender": remake,
                    "kills": 5,
                    "deaths": 2,
                    "assists": 7,
                    "totalMinionsKilled": 100,
                    "neutralMinionsKilled": 5,
                    "totalDamageDealtToChampions": 10000,
                },
                {
                    "puuid": "b",
                    "teamId": 100 if same_team else 200,
                    "championName": "Lux",
                    "win": not remake if same_team else False,
                    "gameEndedInEarlySurrender": remake,
                    "kills": 3,
                    "deaths": 1,
                    "assists": 9,
                    "totalMinionsKilled": 50,
                    "neutralMinionsKilled": 0,
                    "totalDamageDealtToChampions": 8000,
                },
            ],
        }
    }


class MatchCacheTests(unittest.TestCase):
    def test_round_trip_rejects_unfinished_and_aggregates(self) -> None:
        """Verify that round trip rejects unfinished and aggregates."""
        with TemporaryDirectory() as directory:
            cache = MatchCache(Path(directory) / "matches.sqlite")
            payload = _payload()
            self.assertTrue(cache.put("NA1_1", "NA1", payload))
            self.assertEqual(cache.get("NA1_1"), payload)
            self.assertFalse(cache.put("NA1_2", "NA1", _payload(end=None)))
            stats = cache.champion_stats("a")[0]
            self.assertEqual((stats.champion, stats.games, stats.wins), ("Ahri", 1, 1))
            self.assertEqual(cache.duo_record("a", "b"), (1, 1))

    def test_timeline_round_trip_uses_its_own_cache_table(self) -> None:
        """Immutable timelines persist independently of match payloads."""
        with TemporaryDirectory() as directory:
            cache = MatchCache(Path(directory) / "matches.sqlite")
            timeline = {"info": {"frames": [{"timestamp": 0}]}}
            self.assertTrue(cache.put_timeline("NA1_1", timeline))
            self.assertEqual(cache.get_timeline("NA1_1"), timeline)

    def test_component_state_upserts_without_rewriting_other_records(self) -> None:
        """Incremental component persistence retains and updates each message."""
        with TemporaryDirectory() as directory:
            cache = MatchCache(Path(directory) / "matches.sqlite")
            self.assertTrue(
                cache.remember_embed_button_state(1, 10, "match", {"value": 1})
            )
            self.assertTrue(
                cache.remember_embed_button_state(2, 10, "live_game", {"value": 2})
            )
            self.assertTrue(
                cache.remember_embed_button_state(1, 11, "match", {"value": 3})
            )
            states = {row["message_id"]: row for row in cache.load_embed_button_states()}
            self.assertEqual(len(states), 2)
            self.assertEqual(states[1]["channel_id"], 11)
            self.assertEqual(states[1]["payload"], {"value": 3})

    def test_hot_path_indexes_are_created(self) -> None:
        """Pruning and role reads have indexes that match their leading filters."""
        with TemporaryDirectory() as directory:
            path = Path(directory) / "matches.sqlite"
            cache = MatchCache(path)
            cache.close()
            with closing(sqlite3.connect(path)) as db:
                indexes = {
                    row[0]
                    for row in db.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'index'"
                    )
                }
            self.assertIn("matches_game_end_ms", indexes)
            self.assertIn("lane_matchups_position_patch", indexes)

    def test_match_payload_iterator_crosses_batch_boundary(self) -> None:
        """Keyset streaming yields every match even when timestamps are equal."""
        with TemporaryDirectory() as directory:
            cache = MatchCache(Path(directory) / "matches.sqlite")
            for index in range(105):
                cache.put(f"NA1_{index:03d}", "NA1", _payload())
            self.assertEqual(len(list(cache.iter_match_payloads())), 105)

    def test_prune_removes_only_old_matches(self) -> None:
        """Verify that prune removes only old matches."""
        with TemporaryDirectory() as directory:
            cache = MatchCache(Path(directory) / "matches.sqlite")
            cache.put("old", "NA1", _payload(end=1))
            cache.put("new", "NA1", _payload())
            self.assertEqual(cache.prune(older_than_days=180), 1)
            self.assertIsNone(cache.get("old"))
            self.assertIsNotNone(cache.get("new"))

    def test_duo_requires_distinct_teammates_and_excludes_remakes(self) -> None:
        """Verify that duo requires distinct teammates and excludes remakes."""
        with TemporaryDirectory() as directory:
            cache = MatchCache(Path(directory) / "matches.sqlite")
            cache.put("opponents", "NA1", _payload(same_team=False))
            cache.put("remake", "NA1", _payload(remake=True))
            self.assertEqual(cache.duo_record("a", "b"), (0, 0))
            self.assertEqual(cache.duo_record("a", "a"), (0, 0))

    def test_corrupt_file_degrades_to_cache_miss(self) -> None:
        """Verify that corrupt file degrades to cache miss."""
        with TemporaryDirectory() as directory:
            path = Path(directory) / "matches.sqlite"
            path.write_text("not sqlite", encoding="utf-8")
            cache = MatchCache(path)
            self.assertIsNone(cache.get("NA1_1"))
            self.assertEqual(cache.prune(), 0)

    def test_v1_schema_migrates_team_ids_from_stored_payloads(self) -> None:
        """Verify that v1 schema migrates team ids from stored payloads."""
        with TemporaryDirectory() as directory:
            path = Path(directory) / "matches.sqlite"
            with closing(sqlite3.connect(path)) as db, db:
                db.executescript(
                    """
                    CREATE TABLE matches (
                        match_id TEXT PRIMARY KEY, platform TEXT NOT NULL,
                        queue_id INTEGER, game_end_ms INTEGER NOT NULL, payload TEXT NOT NULL
                    );
                    CREATE TABLE participants (
                        match_id TEXT NOT NULL, puuid TEXT NOT NULL, champion TEXT NOT NULL,
                        win INTEGER NOT NULL, kills INTEGER NOT NULL, deaths INTEGER NOT NULL,
                        assists INTEGER NOT NULL, cs INTEGER NOT NULL, damage INTEGER NOT NULL,
                        PRIMARY KEY (match_id, puuid)
                    );
                    PRAGMA user_version = 1;
                    """
                )
                payload = _payload()
                db.execute(
                    "INSERT INTO matches VALUES (?, ?, ?, ?, ?)",
                    ("NA1_1", "NA1", 420, 2_000_000_000_000, json.dumps(payload)),
                )
            cache = MatchCache(path)
            self.assertEqual(cache.duo_record("a", "b"), (1, 1))

    def test_v2_schema_gets_current_tables_without_losing_data(self) -> None:
        """A v2 database migrates to v5 in place, keeping its existing rows."""
        with TemporaryDirectory() as directory:
            path = Path(directory) / "matches.sqlite"
            cache_v2 = MatchCache(path)
            cache_v2.put("NA1_1", "NA1", _payload())
            cache_v2.close()

            with closing(sqlite3.connect(path)) as db, db:
                db.execute("PRAGMA user_version").fetchone()
                self.assertEqual(
                    db.execute("PRAGMA user_version").fetchone()[0], 5
                )

            cache = MatchCache(path)
            self.assertEqual(cache.get("NA1_1"), _payload())
            self.assertEqual(cache.lane_matchup_stats("TOP"), [])
            self.assertEqual(cache.lane_ratings("TOP"), {})
            self.assertEqual(cache.rating_baseline_stats(), [])


class LaneMatchupStatisticsTests(unittest.TestCase):
    def _make_outcome(self, champion_id, opponent_id, team_id, gold, patch="14.16"):
        """Handle outcome."""
        return LaneOutcome(
            patch=patch,
            platform="NA1",
            position="TOP",
            champion_id=champion_id,
            opponent_id=opponent_id,
            team_id=team_id,
            gold_diff=gold,
            xp_diff=gold / 2,
            cs_diff=gold / 50,
            solo_kill_diff=0.0,
        )

    def test_composite_scores_are_antisymmetric_for_a_mirrored_pair(self) -> None:
        """Verify that composite scores are antisymmetric for a mirrored pair."""
        outcomes = [self._make_outcome(1, 2, 100, 500.0), self._make_outcome(2, 1, 200, -500.0)]
        scores = lane_composite_scores(outcomes)
        self.assertAlmostEqual(scores[0], -scores[1])

    def test_record_lane_outcomes_stores_sufficient_statistics_not_per_game_rows(
        self,
    ) -> None:
        """Verify that record lane outcomes stores sufficient statistics not per-game rows."""
        with TemporaryDirectory() as directory:
            cache = MatchCache(Path(directory) / "matches.sqlite")
            outcomes = [
                self._make_outcome(1, 2, 100, 500.0),
                self._make_outcome(2, 1, 200, -500.0),
                self._make_outcome(1, 2, 200, 300.0),
            ]
            self.assertEqual(cache.record_lane_outcomes(outcomes), 3)
            stats = {
                (s.champion_id, s.opponent_id): s
                for s in cache.lane_matchup_stats("TOP")
            }
            self.assertEqual(stats[(1, 2)].games, 2)
            self.assertEqual(stats[(1, 2)].sum_gold, 800.0)
            self.assertEqual(stats[(2, 1)].games, 1)

    def test_lane_ratings_round_trip_and_prune_by_patch(self) -> None:
        """Verify that lane ratings round trip and prune by patch."""
        with TemporaryDirectory() as directory:
            cache = MatchCache(Path(directory) / "matches.sqlite")
            cache.record_lane_outcomes(
                [self._make_outcome(1, 2, 100, 500.0, patch="14.15")]
            )
            cache.write_lane_ratings("TOP", "14.16", {1: (0.4, 20), 2: (-0.4, 20)})
            self.assertEqual(cache.lane_ratings("TOP"), {1: 0.4, 2: -0.4})
            cache.prune_lane_statistics(["14.16"])
            self.assertEqual(cache.lane_matchup_stats("TOP"), [])
            self.assertEqual(cache.lane_ratings("TOP"), {1: 0.4, 2: -0.4})

"""Completed match cache and aggregate queries."""

import json
import sqlite3
import unittest
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

from bot_app.match_cache import MatchCache


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

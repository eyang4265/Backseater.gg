"""SQLite cache for immutable, completed match payloads and aggregates."""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
import weakref
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import JSON_DIR

LOGGER = logging.getLogger(__name__)
MATCH_CACHE_PATH = JSON_DIR / "matches.sqlite"


@dataclass(frozen=True)
class ChampionStats:
    champion: str
    games: int
    wins: int
    kills: int
    deaths: int
    assists: int
    cs: int
    damage: int


class MatchCache:
    def __init__(self, path: Path | str = MATCH_CACHE_PATH) -> None:
        """Initialize the instance."""
        self.path = str(path)
        self._lock = threading.RLock()
        self._usable = True
        self._connection: sqlite3.Connection | None = None
        self._connection_finalizer: weakref.finalize | None = None
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        """Handle connect."""
        if self._connection is None:
            self._connection = sqlite3.connect(self.path, check_same_thread=False)
            self._connection.row_factory = sqlite3.Row
            self._connection_finalizer = weakref.finalize(self, self._connection.close)
        return self._connection

    @contextmanager
    def _database(self):
        """Handle database."""
        connection = self._connect()
        with connection:
            yield connection

    def close(self) -> None:
        """Handle close."""
        with self._lock:
            if self._connection is not None:
                if self._connection_finalizer is not None:
                    self._connection_finalizer()
                    self._connection_finalizer = None
                else:
                    self._connection.close()
                self._connection = None

    @staticmethod
    def _participant_rows(match_id: str, info: dict[str, Any]) -> list[tuple[Any, ...]]:
        """Handle rows."""
        participants = info.get("participants", []) or []
        remake = int(
            any(
                participant.get("gameEndedInEarlySurrender")
                for participant in participants
            )
        )
        return [
            (
                match_id,
                participant.get("puuid", ""),
                int(participant.get("teamId", 0)),
                remake,
                participant.get("championName", "Unknown"),
                int(bool(participant.get("win"))),
                int(participant.get("kills", 0)),
                int(participant.get("deaths", 0)),
                int(participant.get("assists", 0)),
                int(participant.get("totalMinionsKilled", 0))
                + int(participant.get("neutralMinionsKilled", 0)),
                int(participant.get("totalDamageDealtToChampions", 0)),
            )
            for participant in participants
            if participant.get("puuid")
        ]

    def _initialize(self) -> None:
        """Handle initialize."""
        try:
            if self.path != ":memory:":
                Path(self.path).parent.mkdir(parents=True, exist_ok=True)
            with self._database() as db:
                db.execute(
                    """
                    CREATE TABLE IF NOT EXISTS matches (
                        match_id TEXT PRIMARY KEY,
                        platform TEXT NOT NULL,
                        queue_id INTEGER,
                        game_end_ms INTEGER NOT NULL,
                        payload TEXT NOT NULL
                    )
                    """
                )
                version = int(db.execute("PRAGMA user_version").fetchone()[0])
                if version < 2:
                    db.execute("DROP TABLE IF EXISTS participants")
                db.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS participants (
                        match_id TEXT NOT NULL,
                        puuid TEXT NOT NULL,
                        team_id INTEGER NOT NULL,
                        remake INTEGER NOT NULL,
                        champion TEXT NOT NULL,
                        win INTEGER NOT NULL,
                        kills INTEGER NOT NULL,
                        deaths INTEGER NOT NULL,
                        assists INTEGER NOT NULL,
                        cs INTEGER NOT NULL,
                        damage INTEGER NOT NULL,
                        PRIMARY KEY (match_id, puuid),
                        FOREIGN KEY (match_id) REFERENCES matches(match_id) ON DELETE CASCADE
                    );
                    CREATE INDEX IF NOT EXISTS participants_puuid ON participants(puuid);
                    PRAGMA user_version = 2;
                    """
                )
                if version < 2:
                    for row in db.execute(
                        "SELECT match_id, payload FROM matches"
                    ).fetchall():
                        try:
                            payload = json.loads(row["payload"])
                            db.executemany(
                                "INSERT OR REPLACE INTO participants VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                                self._participant_rows(
                                    row["match_id"], payload.get("info", {})
                                ),
                            )
                        except (json.JSONDecodeError, TypeError, ValueError):
                            LOGGER.warning(
                                "Skipping malformed cached match %s during migration",
                                row["match_id"],
                            )
        except sqlite3.DatabaseError as error:
            self._usable = False
            LOGGER.warning("Match cache is unavailable: %s", error)
            self.close()

    def get(self, match_id: str) -> dict[str, Any] | None:
        """Return get."""
        if not self._usable:
            return None
        try:
            with self._lock, self._database() as db:
                row = db.execute(
                    "SELECT payload FROM matches WHERE match_id = ?", (match_id,)
                ).fetchone()
            return json.loads(row["payload"]) if row else None
        except (sqlite3.DatabaseError, json.JSONDecodeError) as error:
            LOGGER.warning("Could not read match %s from cache: %s", match_id, error)
            return None

    def put(self, match_id: str, platform: str, payload: dict[str, Any]) -> bool:
        """Handle put."""
        info = payload.get("info", {})
        game_end_ms = info.get("gameEndTimestamp")
        if not game_end_ms:
            return False
        try:
            with self._lock, self._database() as db:
                db.execute(
                    "INSERT OR REPLACE INTO matches VALUES (?, ?, ?, ?, ?)",
                    (
                        match_id,
                        platform,
                        info.get("queueId"),
                        int(game_end_ms),
                        json.dumps(payload, separators=(",", ":")),
                    ),
                )
                db.execute("DELETE FROM participants WHERE match_id = ?", (match_id,))
                db.executemany(
                    "INSERT OR REPLACE INTO participants VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    self._participant_rows(match_id, info),
                )
            return True
        except (sqlite3.DatabaseError, TypeError, ValueError) as error:
            LOGGER.warning("Could not cache match %s: %s", match_id, error)
            return False

    def prune(self, *, older_than_days: int = 180) -> int:
        """Prune prune."""
        if not self._usable:
            return 0
        cutoff_ms = int((time.time() - older_than_days * 86400) * 1000)
        try:
            with self._lock, self._database() as db:
                ids = [
                    row[0]
                    for row in db.execute(
                        "SELECT match_id FROM matches WHERE game_end_ms < ?",
                        (cutoff_ms,),
                    )
                ]
                db.executemany(
                    "DELETE FROM participants WHERE match_id = ?",
                    [(item,) for item in ids],
                )
                db.executemany(
                    "DELETE FROM matches WHERE match_id = ?", [(item,) for item in ids]
                )
            return len(ids)
        except sqlite3.DatabaseError as error:
            LOGGER.warning("Could not prune match cache: %s", error)
            return 0

    def champion_stats(self, puuid: str) -> list[ChampionStats]:
        """Handle stats."""
        if not self._usable:
            return []
        try:
            with self._lock, self._database() as db:
                rows = db.execute(
                    """
                    SELECT champion, COUNT(*) games, SUM(win) wins, SUM(kills) kills,
                           SUM(deaths) deaths, SUM(assists) assists, SUM(cs) cs,
                           SUM(damage) damage
                    FROM participants WHERE puuid = ? AND remake = 0
                    GROUP BY champion ORDER BY games DESC, wins DESC, champion
                    """,
                    (puuid,),
                ).fetchall()
            return [ChampionStats(**dict(row)) for row in rows]
        except sqlite3.DatabaseError as error:
            LOGGER.warning("Could not query champion stats: %s", error)
            return []

    def duo_record(self, first_puuid: str, second_puuid: str) -> tuple[int, int]:
        """Handle record."""
        if not self._usable or first_puuid == second_puuid:
            return 0, 0
        try:
            with self._lock, self._database() as db:
                row = db.execute(
                    """
                    SELECT COUNT(*) games, COALESCE(SUM(a.win), 0) wins
                    FROM participants a JOIN participants b ON a.match_id = b.match_id
                    WHERE a.puuid = ? AND b.puuid = ?
                      AND a.team_id = b.team_id AND a.remake = 0 AND b.remake = 0
                    """,
                    (first_puuid, second_puuid),
                ).fetchone()
            return int(row["games"]), int(row["wins"])
        except sqlite3.DatabaseError as error:
            LOGGER.warning("Could not query duo record: %s", error)
            return 0, 0


_default_cache: MatchCache | None = None
_default_lock = threading.Lock()


def get_match_cache() -> MatchCache:
    """Return match cache."""
    global _default_cache
    if _default_cache is None:
        with _default_lock:
            if _default_cache is None:
                _default_cache = MatchCache()
    return _default_cache

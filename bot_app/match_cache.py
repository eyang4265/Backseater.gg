"""SQLite storage for immutable matches/timelines, aggregates, and component state."""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
import weakref
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from .config import DATA_DIR, JSON_DIR, migrate_legacy_sqlite_file
from .lane_matchups.extract import LaneOutcome
from .queues import queue_name

LOGGER = logging.getLogger(__name__)
MATCH_CACHE_PATH = DATA_DIR / "matches.sqlite"
LEGACY_MATCH_CACHE_PATH = JSON_DIR / "matches.sqlite"

CURRENT_SCHEMA_VERSION = 5

EMBED_BUTTON_STATE_LIMIT = 500


# Weights from PLAN.md §2.3. Gold dominates deliberately; the rest mostly
# break ties and stabilise games where a lead was taken as XP or tempo.
LANE_SCORE_WEIGHTS: dict[str, float] = {
    "gold_diff": 0.55,
    "xp_diff": 0.20,
    "cs_diff": 0.15,
    "solo_kill_diff": 0.10,
}


@dataclass(frozen=True)
class LaneMatchupStat:
    """One aggregated (patch, position, champion, opponent) row."""

    patch: str
    position: str
    champion_id: int
    opponent_id: int
    games: int
    sum_score: float
    sum_score_sq: float
    sum_gold: float
    sum_gold_sq: float
    blue_games: int


@dataclass(frozen=True)
class RatingSample:
    """One player's raw value for one rating metric in one match.

    Emitted by :func:`bot_app.rating.rating_samples` and folded straight
    into ``rating_baselines``' running sums; like ``lane_matchups``, no
    per-game row is ever stored.
    """

    patch: str
    position: str
    metric: str
    value: float


@dataclass(frozen=True)
class RatingBaselineStat:
    """One aggregated ``(patch, position, metric)`` row."""

    patch: str
    position: str
    metric: str
    samples: int
    sum_value: float
    sum_value_sq: float


def _population_stats(values: list[float | None]) -> tuple[float, float] | None:
    """Mean and standard deviation of the present values, or None if too few."""
    present = [value for value in values if value is not None]
    if len(present) < 2:
        return None
    mean = sum(present) / len(present)
    variance = sum((value - mean) ** 2 for value in present) / len(present)
    return mean, variance**0.5


def _z(value: float | None, stats: tuple[float, float] | None) -> float | None:
    """Handle z."""
    if value is None or stats is None:
        return None
    mean, deviation = stats
    if deviation <= 1e-9:
        return 0.0
    return (value - mean) / deviation


def lane_composite_scores(outcomes: Sequence[LaneOutcome]) -> list[float]:
    """Composite §2.3 lane score for each outcome, standardised against the batch.

    Each metric is z-scored against the population of outcomes sharing the
    same ``(patch, position)`` within this batch — "that role's population on
    the current patch" per PLAN.md §2.3. A row missing a metric (``cs_diff``
    is always None for UTILITY rows) renormalises the remaining weights
    rather than treating it as zero, the same bucket-and-renormalise idiom
    :mod:`bot_app.rating` uses.

    Returns scores in the same order as ``outcomes``.
    """
    grouped: dict[tuple[str, str], list[int]] = defaultdict(list)
    for index, outcome in enumerate(outcomes):
        grouped[(outcome.patch, outcome.position)].append(index)

    scores: list[float] = [0.0] * len(outcomes)
    for (patch, position), indices in grouped.items():
        rows = [outcomes[index] for index in indices]
        stats = {
            name: _population_stats([getattr(row, name) for row in rows])
            for name in LANE_SCORE_WEIGHTS
        }
        for index in indices:
            outcome = outcomes[index]
            total = 0.0
            weight = 0.0
            for name, metric_weight in LANE_SCORE_WEIGHTS.items():
                z_value = _z(getattr(outcome, name), stats[name])
                if z_value is None:
                    continue
                total += metric_weight * z_value
                weight += metric_weight
            scores[index] = total / weight if weight else 0.0
    return scores


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
        if Path(path) == MATCH_CACHE_PATH:
            migrate_legacy_sqlite_file(MATCH_CACHE_PATH, LEGACY_MATCH_CACHE_PATH)
        self.path = str(path)
        self._lock = threading.RLock()
        self._usable = True
        self._connection: sqlite3.Connection | None = None
        self._connection_finalizer: weakref.finalize | None = None
        self._lane_statistics_revision = 0
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

    @property
    def lane_statistics_revision(self) -> int:
        """In-process revision used to invalidate cached counter-model fits."""
        with self._lock:
            return self._lane_statistics_revision

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
                if version < 4:
                    # v4 introduced rating_baselines. Any table of that name
                    # in an older database predates the released schema, so
                    # drop rather than migrate it; the rows are pure
                    # aggregates and are rebuilt by
                    # bot_app.lane_matchups.collect.backfill_rating_baselines.
                    db.execute("DROP TABLE IF EXISTS rating_baselines")
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
                    CREATE INDEX IF NOT EXISTS matches_game_end_ms ON matches(game_end_ms);

                    CREATE TABLE IF NOT EXISTS lane_matchups (
                        patch        TEXT    NOT NULL,
                        position     TEXT    NOT NULL,
                        champion_id  INTEGER NOT NULL,
                        opponent_id  INTEGER NOT NULL,
                        games        INTEGER NOT NULL,
                        sum_score    REAL    NOT NULL,
                        sum_score_sq REAL    NOT NULL,
                        sum_gold     REAL    NOT NULL,
                        sum_gold_sq  REAL    NOT NULL,
                        blue_games   INTEGER NOT NULL,
                        PRIMARY KEY (patch, position, champion_id, opponent_id)
                    );
                    CREATE INDEX IF NOT EXISTS lane_matchups_position_patch
                        ON lane_matchups(position, patch);

                    CREATE TABLE IF NOT EXISTS lane_ratings (
                        patch       TEXT    NOT NULL,
                        position    TEXT    NOT NULL,
                        champion_id INTEGER NOT NULL,
                        rating      REAL    NOT NULL,
                        games       INTEGER NOT NULL,
                        PRIMARY KEY (patch, position, champion_id)
                    );
                    CREATE INDEX IF NOT EXISTS lane_ratings_position_patch
                        ON lane_ratings(position, patch);

                    CREATE TABLE IF NOT EXISTS rating_baselines (
                        patch        TEXT    NOT NULL,
                        position     TEXT    NOT NULL,
                        metric       TEXT    NOT NULL,
                        samples      INTEGER NOT NULL,
                        sum_value    REAL    NOT NULL,
                        sum_value_sq REAL    NOT NULL,
                        PRIMARY KEY (patch, position, metric)
                    );

                    CREATE TABLE IF NOT EXISTS match_timelines (
                        match_id TEXT PRIMARY KEY,
                        payload  TEXT NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS embed_button_states (
                        message_id INTEGER PRIMARY KEY,
                        channel_id INTEGER NOT NULL,
                        kind       TEXT    NOT NULL,
                        payload    TEXT    NOT NULL,
                        updated_at INTEGER NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS embed_button_states_updated_at
                        ON embed_button_states(updated_at);

                    PRAGMA user_version = 5;
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
            LOGGER.info("Match cache initialized at %s", self.path)
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
            LOGGER.debug("Match cache %s: %s", "hit" if row else "miss", match_id)
            return json.loads(row["payload"]) if row else None
        except (sqlite3.DatabaseError, json.JSONDecodeError) as error:
            LOGGER.warning("Could not read match %s from cache: %s", match_id, error)
            return None

    def put(self, match_id: str, platform: str, payload: dict[str, Any]) -> bool:
        """Handle put."""
        info = payload.get("info", {})
        game_end_ms = info.get("gameEndTimestamp")
        if not game_end_ms:
            LOGGER.debug("Skipping cache write for %s: no gameEndTimestamp", match_id)
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
            LOGGER.debug("Cached match %s (platform=%s)", match_id, platform)
            return True
        except (sqlite3.DatabaseError, TypeError, ValueError) as error:
            LOGGER.warning("Could not cache match %s: %s", match_id, error)
            return False

    def get_timeline(self, match_id: str) -> dict[str, Any] | None:
        """Return a cached immutable Match-V5 timeline, if present."""
        if not self._usable:
            return None
        try:
            with self._lock, self._database() as db:
                row = db.execute(
                    "SELECT payload FROM match_timelines WHERE match_id = ?", (match_id,)
                ).fetchone()
            LOGGER.debug("Timeline cache %s: %s", "hit" if row else "miss", match_id)
            return json.loads(row["payload"]) if row else None
        except (sqlite3.DatabaseError, json.JSONDecodeError) as error:
            LOGGER.warning("Could not read timeline %s from cache: %s", match_id, error)
            return None

    def put_timeline(self, match_id: str, payload: dict[str, Any]) -> bool:
        """Persist one immutable Match-V5 timeline."""
        if not self._usable or not match_id or not isinstance(payload, dict):
            return False
        try:
            encoded = json.dumps(payload, separators=(",", ":"))
            with self._lock, self._database() as db:
                db.execute(
                    "INSERT OR REPLACE INTO match_timelines VALUES (?, ?)",
                    (match_id, encoded),
                )
            LOGGER.debug("Cached timeline %s", match_id)
            return True
        except (sqlite3.DatabaseError, TypeError, ValueError) as error:
            LOGGER.warning("Could not cache timeline %s: %s", match_id, error)
            return False

    def load_embed_button_states(self) -> list[dict[str, Any]]:
        """Load persistent Discord component records, newest first."""
        if not self._usable:
            return []
        try:
            with self._lock, self._database() as db:
                rows = db.execute(
                    """
                    SELECT message_id, channel_id, kind, payload
                    FROM embed_button_states ORDER BY updated_at
                    """
                ).fetchall()
            states = []
            for row in rows:
                try:
                    payload = json.loads(row["payload"])
                except (json.JSONDecodeError, TypeError, ValueError):
                    LOGGER.warning(
                        "Skipping malformed persistent view state for message %s",
                        row["message_id"],
                    )
                    continue
                states.append(
                    {
                        "message_id": int(row["message_id"]),
                        "channel_id": int(row["channel_id"]),
                        "kind": str(row["kind"]),
                        "payload": payload,
                    }
                )
            return states
        except sqlite3.DatabaseError as error:
            LOGGER.warning("Could not load persistent view states: %s", error)
            return []

    def remember_embed_button_state(
        self, message_id: int, channel_id: int, kind: str, payload: dict[str, Any]
    ) -> bool:
        """Upsert one component record without rewriting every stored payload."""
        if not self._usable:
            return False
        try:
            encoded = json.dumps(payload, separators=(",", ":"))
            with self._lock, self._database() as db:
                db.execute(
                    """
                    INSERT INTO embed_button_states
                        (message_id, channel_id, kind, payload, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(message_id) DO UPDATE SET
                        channel_id = excluded.channel_id,
                        kind = excluded.kind,
                        payload = excluded.payload,
                        updated_at = excluded.updated_at
                    """,
                    (message_id, channel_id, kind, encoded, time.time_ns()),
                )
                db.execute(
                    """
                    DELETE FROM embed_button_states
                    WHERE message_id NOT IN (
                        SELECT message_id FROM embed_button_states
                        ORDER BY updated_at DESC LIMIT ?
                    )
                    """,
                    (EMBED_BUTTON_STATE_LIMIT,),
                )
            return True
        except (sqlite3.DatabaseError, TypeError, ValueError) as error:
            LOGGER.warning("Could not persist component state: %s", error)
            return False

    def import_embed_button_states(self, states: Sequence[dict[str, Any]]) -> int:
        """Bulk-import legacy JSON component records into the SQLite store."""
        rows = []
        timestamp = time.time_ns()
        for offset, state in enumerate(states[-EMBED_BUTTON_STATE_LIMIT:]):
            try:
                message_id = int(state["message_id"])
                channel_id = int(state["channel_id"])
                kind = str(state["kind"])
                payload = json.dumps(state["payload"], separators=(",", ":"))
            except (KeyError, TypeError, ValueError):
                continue
            rows.append((message_id, channel_id, kind, payload, timestamp + offset))
        if not rows or not self._usable:
            return 0
        try:
            with self._lock, self._database() as db:
                db.executemany(
                    """
                    INSERT INTO embed_button_states
                        (message_id, channel_id, kind, payload, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(message_id) DO UPDATE SET
                        channel_id = excluded.channel_id,
                        kind = excluded.kind,
                        payload = excluded.payload,
                        updated_at = excluded.updated_at
                    """,
                    rows,
                )
            return len(rows)
        except sqlite3.DatabaseError as error:
            LOGGER.warning("Could not import legacy component states: %s", error)
            return 0

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
                    "DELETE FROM match_timelines WHERE match_id = ?",
                    [(item,) for item in ids],
                )
                db.executemany(
                    "DELETE FROM matches WHERE match_id = ?", [(item,) for item in ids]
                )
            LOGGER.info("Pruned %d matches older than %d days", len(ids), older_than_days)
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

    def champion_match_payloads(self, puuid: str, champion: str) -> list[dict[str, Any]]:
        """Return relevant cached, non-remake Match-V5 payloads in game order.

        JSON decoding stays inside the cache boundary: one damaged row is
        logged and skipped rather than making a command fail for all history.
        """
        if not self._usable:
            return []
        try:
            with self._lock, self._database() as db:
                rows = db.execute(
                    """
                    SELECT m.payload
                    FROM participants AS p
                    JOIN matches AS m ON m.match_id = p.match_id
                    WHERE p.puuid = ? AND lower(p.champion) = lower(?) AND p.remake = 0
                    ORDER BY m.game_end_ms ASC
                    """,
                    (puuid, champion),
                ).fetchall()
        except sqlite3.DatabaseError as error:
            LOGGER.warning("Could not query cached champion matches: %s", error)
            return []

        payloads: list[dict[str, Any]] = []
        for row in rows:
            try:
                payload = json.loads(row["payload"])
            except (json.JSONDecodeError, TypeError, ValueError) as error:
                LOGGER.warning("Skipping malformed cached champion match: %s", error)
                continue
            if isinstance(payload, dict):
                payloads.append(payload)
        return payloads

    def duo_record(
        self, first_puuid: str, second_puuid: str, *, game_mode: str | None = None
    ) -> tuple[int, int]:
        """Handle record, optionally restricted to matches whose queue name matches ``game_mode``."""
        if not self._usable or first_puuid == second_puuid:
            return 0, 0
        try:
            with self._lock, self._database() as db:
                if game_mode:
                    rows = db.execute(
                        """
                        SELECT m.queue_id queue_id, a.win win
                        FROM participants a
                        JOIN participants b ON a.match_id = b.match_id
                        JOIN matches m ON m.match_id = a.match_id
                        WHERE a.puuid = ? AND b.puuid = ?
                          AND a.team_id = b.team_id AND a.remake = 0 AND b.remake = 0
                        """,
                        (first_puuid, second_puuid),
                    ).fetchall()
                    needle = game_mode.casefold()
                    games = 0
                    wins = 0
                    for row in rows:
                        if needle not in queue_name(row["queue_id"]).casefold():
                            continue
                        games += 1
                        wins += int(row["win"])
                    return games, wins
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

    def record_lane_outcomes(self, outcomes: Sequence[LaneOutcome]) -> int:
        """Upsert sufficient statistics for a batch of :class:`LaneOutcome` rows.

        Never stores a per-game row (PLAN.md §8): each ``(patch, position,
        champion, opponent)`` pair accumulates running sums instead. Because
        :func:`bot_app.lane_matchups.extract.lane_outcomes` already emits a
        row for both champions in every pair (mirrored TOP blue and TOP red
        rows, etc.), both directions land here automatically, which is what
        keeps ``effect(A, B) == -effect(B, A)`` a stored fact rather than
        something a reader has to derive.
        """
        if not self._usable or not outcomes:
            return 0
        scores = lane_composite_scores(outcomes)
        try:
            with self._lock, self._database() as db:
                for outcome, score in zip(outcomes, scores):
                    db.execute(
                        """
                        INSERT INTO lane_matchups (
                            patch, position, champion_id, opponent_id,
                            games, sum_score, sum_score_sq, sum_gold, sum_gold_sq, blue_games
                        ) VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, ?)
                        ON CONFLICT (patch, position, champion_id, opponent_id) DO UPDATE SET
                            games = games + 1,
                            sum_score = sum_score + excluded.sum_score,
                            sum_score_sq = sum_score_sq + excluded.sum_score_sq,
                            sum_gold = sum_gold + excluded.sum_gold,
                            sum_gold_sq = sum_gold_sq + excluded.sum_gold_sq,
                            blue_games = blue_games + excluded.blue_games
                        """,
                        (
                            outcome.patch,
                            outcome.position,
                            outcome.champion_id,
                            outcome.opponent_id,
                            score,
                            score * score,
                            outcome.gold_diff,
                            outcome.gold_diff**2,
                            1 if outcome.team_id == 100 else 0,
                        ),
                    )
            with self._lock:
                self._lane_statistics_revision += 1
            LOGGER.debug("Recorded %d lane outcomes", len(outcomes))
            return len(outcomes)
        except sqlite3.DatabaseError as error:
            LOGGER.warning("Could not record lane outcomes: %s", error)
            return 0

    def lane_matchup_stats(
        self, position: str, patches: Sequence[str] | None = None
    ) -> list[LaneMatchupStat]:
        """Every stored pair row for one role, optionally limited to ``patches``."""
        if not self._usable:
            return []
        query = (
            "SELECT patch, position, champion_id, opponent_id, games, sum_score, "
            "sum_score_sq, sum_gold, sum_gold_sq, blue_games FROM lane_matchups "
            "WHERE position = ?"
        )
        params: list[Any] = [position]
        if patches:
            query += f" AND patch IN ({','.join('?' for _ in patches)})"
            params.extend(patches)
        try:
            with self._lock, self._database() as db:
                rows = db.execute(query, params).fetchall()
            return [LaneMatchupStat(**dict(row)) for row in rows]
        except sqlite3.DatabaseError as error:
            LOGGER.warning("Could not query lane matchup stats: %s", error)
            return []

    def write_lane_ratings(
        self, position: str, patch: str, ratings: dict[int, tuple[float, int]]
    ) -> int:
        """Replace one (patch, position)'s rows in ``lane_ratings`` with a fresh fit.

        ``ratings`` maps champion id to ``(rating, games)``. Ratings are a
        full recompute per fit rather than an incremental upsert, since
        :mod:`bot_app.lane_matchups.model`'s alternating-projection solver
        already refits every champion in the role together.
        """
        if not self._usable:
            return 0
        try:
            with self._lock, self._database() as db:
                db.execute(
                    "DELETE FROM lane_ratings WHERE patch = ? AND position = ?",
                    (patch, position),
                )
                db.executemany(
                    "INSERT INTO lane_ratings VALUES (?, ?, ?, ?, ?)",
                    [
                        (patch, position, champion_id, rating, games)
                        for champion_id, (rating, games) in ratings.items()
                    ],
                )
            LOGGER.info(
                "Wrote %d lane ratings for position=%s patch=%s",
                len(ratings), position, patch,
            )
            return len(ratings)
        except sqlite3.DatabaseError as error:
            LOGGER.warning("Could not write lane ratings: %s", error)
            return 0

    def lane_ratings(
        self, position: str, patches: Sequence[str] | None = None
    ) -> dict[int, float]:
        """Champion id -> rating for one role, latest patch winning on overlap."""
        if not self._usable:
            return {}
        query = "SELECT champion_id, rating FROM lane_ratings WHERE position = ?"
        params: list[Any] = [position]
        if patches:
            query += f" AND patch IN ({','.join('?' for _ in patches)})"
            params.extend(patches)
        try:
            with self._lock, self._database() as db:
                rows = db.execute(query, params).fetchall()
            return {int(row["champion_id"]): float(row["rating"]) for row in rows}
        except sqlite3.DatabaseError as error:
            LOGGER.warning("Could not query lane ratings: %s", error)
            return {}

    def record_rating_samples(self, samples: Sequence[RatingSample]) -> int:
        """Upsert sufficient statistics for a batch of :class:`RatingSample` rows.

        Stores only ``games``/``sum_value``/``sum_value_sq`` per ``(patch,
        position, metric)`` — the same no-per-game-rows discipline
        :meth:`record_lane_outcomes` follows. That is all
        :mod:`bot_app.rating_baselines` needs to recover a population mean
        and standard deviation, which is what lets a rating be a statement
        about a player rather than a ranking within one lobby.
        """
        if not self._usable or not samples:
            return 0
        try:
            with self._lock, self._database() as db:
                db.executemany(
                    """
                    INSERT INTO rating_baselines (
                        patch, position, metric, samples, sum_value, sum_value_sq
                    ) VALUES (?, ?, ?, 1, ?, ?)
                    ON CONFLICT (patch, position, metric) DO UPDATE SET
                        samples = samples + 1,
                        sum_value = sum_value + excluded.sum_value,
                        sum_value_sq = sum_value_sq + excluded.sum_value_sq
                    """,
                    [
                        (
                            sample.patch,
                            sample.position,
                            sample.metric,
                            sample.value,
                            sample.value * sample.value,
                        )
                        for sample in samples
                    ],
                )
            LOGGER.debug("Recorded %d rating samples", len(samples))
            return len(samples)
        except sqlite3.DatabaseError as error:
            LOGGER.warning("Could not record rating samples: %s", error)
            return 0

    def replace_rating_baselines(self, stats: Sequence[RatingBaselineStat]) -> int:
        """Overwrite the given ``(patch, position, metric)`` rows wholesale.

        Unlike :meth:`record_rating_samples`, which accumulates one match at
        a time, this replaces each supplied key's totals outright — so a
        backfill recomputed from the cached match corpus can be re-run
        without double-counting the games it already contributed. Keys not
        present in ``stats`` are left alone, which is what keeps a
        box-score-only backfill from erasing timeline-derived baselines
        gathered during a harvest.
        """
        if not self._usable or not stats:
            return 0
        try:
            with self._lock, self._database() as db:
                db.executemany(
                    """
                    INSERT OR REPLACE INTO rating_baselines (
                        patch, position, metric, samples, sum_value, sum_value_sq
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            stat.patch,
                            stat.position,
                            stat.metric,
                            stat.samples,
                            stat.sum_value,
                            stat.sum_value_sq,
                        )
                        for stat in stats
                    ],
                )
            LOGGER.info("Replaced %d rating baseline rows", len(stats))
            return len(stats)
        except sqlite3.DatabaseError as error:
            LOGGER.warning("Could not replace rating baselines: %s", error)
            return 0

    def iter_match_payloads(self):
        """Yield every cached match payload, oldest first.

        Streams rather than materialising the corpus: the backfill in
        :func:`bot_app.lane_matchups.collect.backfill_rating_baselines` walks
        every stored match, and holding thousands of Match-V5 payloads in
        memory at once is avoidable.
        """
        if not self._usable:
            return
        last_end = -1
        last_match_id = ""
        batch_size = 100
        while True:
            try:
                with self._lock, self._database() as db:
                    rows = db.execute(
                        """
                        SELECT match_id, game_end_ms, payload FROM matches
                        WHERE game_end_ms > ?
                           OR (game_end_ms = ? AND match_id > ?)
                        ORDER BY game_end_ms, match_id LIMIT ?
                        """,
                        (last_end, last_end, last_match_id, batch_size),
                    ).fetchall()
            except sqlite3.DatabaseError as error:
                LOGGER.warning("Could not iterate cached matches: %s", error)
                return
            if not rows:
                return
            for row in rows:
                try:
                    yield json.loads(row["payload"])
                except (json.JSONDecodeError, TypeError, ValueError):
                    LOGGER.warning(
                        "Skipping malformed cached match %s", row["match_id"]
                    )
            last_end = int(rows[-1]["game_end_ms"])
            last_match_id = str(rows[-1]["match_id"])

    def rating_baseline_stats(
        self, patches: Sequence[str] | None = None
    ) -> list[RatingBaselineStat]:
        """Every stored baseline row, optionally limited to ``patches``.

        Returned unpooled, one row per patch: pooling across the patch
        window is :func:`bot_app.rating_baselines.load_baselines`' job, so
        callers that want a single patch can still have one.
        """
        if not self._usable:
            return []
        query = (
            "SELECT patch, position, metric, samples, sum_value, sum_value_sq "
            "FROM rating_baselines"
        )
        params: list[Any] = []
        if patches:
            query += f" WHERE patch IN ({','.join('?' for _ in patches)})"
            params.extend(patches)
        try:
            with self._lock, self._database() as db:
                rows = db.execute(query, params).fetchall()
            return [RatingBaselineStat(**dict(row)) for row in rows]
        except sqlite3.DatabaseError as error:
            LOGGER.warning("Could not query rating baselines: %s", error)
            return []

    def prune_lane_statistics(self, keep_patches: Sequence[str]) -> int:
        """Drop patch-keyed statistics rows outside the rolling patch window.

        Covers ``lane_matchups``, ``lane_ratings`` and ``rating_baselines``.
        """
        if not self._usable or not keep_patches:
            return 0
        placeholders = ",".join("?" for _ in keep_patches)
        try:
            removed = 0
            with self._lock, self._database() as db:
                for table in ("lane_matchups", "lane_ratings", "rating_baselines"):
                    cursor = db.execute(
                        f"DELETE FROM {table} WHERE patch NOT IN ({placeholders})",
                        tuple(keep_patches),
                    )
                    removed += max(cursor.rowcount, 0)
            if removed:
                with self._lock:
                    self._lane_statistics_revision += 1
            LOGGER.info("Pruned %d lane statistics rows outside patch window", removed)
            return removed
        except sqlite3.DatabaseError as error:
            LOGGER.warning("Could not prune lane statistics: %s", error)
            return 0


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

"""SQLite storage for meetups, their poll options, votes, and attendance.

This lives in its own database rather than ``matches.sqlite`` on purpose:
that file is a *cache* of Riot payloads which prunes expired rows and is
safe to delete wholesale, while a meetup is user-authored content that
must survive a cache wipe.

Only sufficient state is stored — a meetup row, its options, one row per
(user, axis, option) vote, and one row per attendance answer.  The
rendered embed is always derived, never persisted, so the layout can
change without a migration.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
import weakref
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

from ..config import JSON_DIR

LOGGER = logging.getLogger(__name__)

MEETUP_DB_PATH = JSON_DIR / "meetups.sqlite"
CURRENT_SCHEMA_VERSION = 1

AXIS_ACTIVITY = "activity"
AXIS_TIME = "time"
AXES = (AXIS_ACTIVITY, AXIS_TIME)

STATE_POLLING = "polling"
STATE_LOCKED = "locked"
STATE_CONFIRMING = "confirming"
STATE_CLOSED = "closed"

ANSWER_GOING = "going"
ANSWER_MAYBE = "maybe"
ANSWER_OUT = "out"
ANSWERS = (ANSWER_GOING, ANSWER_MAYBE, ANSWER_OUT)

CLOSE_GRACE_SECONDS = 6 * 60 * 60
"""How long after its start time a locked meetup stays open."""

STALE_POLL_SECONDS = 30 * 24 * 60 * 60
"""A poll nobody ever locked in is closed out after this long."""


@dataclass(frozen=True)
class MeetupOption:
    """One selectable choice on one axis."""

    axis: str
    key: str
    label: str
    position: int


@dataclass
class Meetup:
    """A meetup and everything needed to render or act on it."""

    meetup_id: int
    guild_id: int
    channel_id: int
    organizer_id: int
    title: str
    location: str
    timezone: str
    state: str
    created_at: int
    message_id: int | None = None
    thread_id: int | None = None
    locked_activity: str | None = None
    locked_time: int | None = None
    closed_at: int | None = None
    options: list[MeetupOption] = field(default_factory=list)
    votes: dict[str, dict[str, list[int]]] = field(default_factory=dict)
    answers: dict[int, str] = field(default_factory=dict)

    def axis_options(self, axis: str) -> list[MeetupOption]:
        """Return this meetup's options on one axis, in creation order."""
        return [option for option in self.options if option.axis == axis]

    def voters(self, axis: str, key: str) -> list[int]:
        """Return the ids of everyone who ticked one option."""
        return list(self.votes.get(axis, {}).get(key, ()))

    def selections(self, axis: str, user_id: int) -> set[str]:
        """Return the option keys one member ticked on one axis."""
        return {
            key
            for key, voters in self.votes.get(axis, {}).items()
            if user_id in voters
        }

    def expected_attendees(self) -> list[int]:
        """Members whose poll votes cover both locked choices.

        This is the headcount the poll implies, and — minus anyone who has
        since given an explicit answer — exactly who ``/meetup confirm``
        needs to chase.
        """
        if self.locked_activity is None or self.locked_time is None:
            return []
        activity = set(self.voters(AXIS_ACTIVITY, self.locked_activity))
        moment = set(self.voters(AXIS_TIME, str(self.locked_time)))
        return sorted(activity & moment)

    def unanswered(self) -> list[int]:
        """Expected attendees who have not confirmed either way yet."""
        return [user for user in self.expected_attendees() if user not in self.answers]

    def by_answer(self, answer: str) -> list[int]:
        """Return everyone who gave one explicit attendance answer."""
        return sorted(user for user, value in self.answers.items() if value == answer)


class MeetupStore:
    """Thread-safe SQLite access for the meetup feature."""

    def __init__(self, path: Path | str = MEETUP_DB_PATH) -> None:
        """Initialize the instance."""
        self.path = str(path)
        self._lock = threading.RLock()
        self._connection: sqlite3.Connection | None = None
        self._connection_finalizer: weakref.finalize | None = None
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        """Open the shared connection lazily."""
        if self._connection is None:
            self._connection = sqlite3.connect(self.path, check_same_thread=False)
            self._connection.row_factory = sqlite3.Row
            self._connection.execute("PRAGMA foreign_keys = ON")
            self._connection_finalizer = weakref.finalize(self, self._connection.close)
        return self._connection

    @contextmanager
    def _database(self):
        """Yield the shared connection inside a transaction."""
        connection = self._connect()
        with connection:
            yield connection

    def close(self) -> None:
        """Release the shared connection."""
        with self._lock:
            if self._connection is not None:
                if self._connection_finalizer is not None:
                    self._connection_finalizer()
                    self._connection_finalizer = None
                else:
                    self._connection.close()
                self._connection = None

    def _initialize(self) -> None:
        """Create the schema if it is missing."""
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self._lock, self._database() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS meetups (
                    meetup_id       INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id        INTEGER NOT NULL,
                    channel_id      INTEGER NOT NULL,
                    message_id      INTEGER,
                    thread_id       INTEGER,
                    organizer_id    INTEGER NOT NULL,
                    title           TEXT    NOT NULL,
                    location        TEXT    NOT NULL DEFAULT '',
                    timezone        TEXT    NOT NULL,
                    state           TEXT    NOT NULL,
                    locked_activity TEXT,
                    locked_time     INTEGER,
                    created_at      INTEGER NOT NULL,
                    closed_at       INTEGER
                );
                CREATE INDEX IF NOT EXISTS meetups_guild_state
                    ON meetups(guild_id, state);
                CREATE UNIQUE INDEX IF NOT EXISTS meetups_message
                    ON meetups(message_id) WHERE message_id IS NOT NULL;

                CREATE TABLE IF NOT EXISTS meetup_options (
                    meetup_id  INTEGER NOT NULL,
                    axis       TEXT    NOT NULL,
                    option_key TEXT    NOT NULL,
                    label      TEXT    NOT NULL,
                    position   INTEGER NOT NULL,
                    PRIMARY KEY (meetup_id, axis, option_key),
                    FOREIGN KEY (meetup_id) REFERENCES meetups(meetup_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS meetup_votes (
                    meetup_id  INTEGER NOT NULL,
                    user_id    INTEGER NOT NULL,
                    axis       TEXT    NOT NULL,
                    option_key TEXT    NOT NULL,
                    PRIMARY KEY (meetup_id, user_id, axis, option_key),
                    FOREIGN KEY (meetup_id) REFERENCES meetups(meetup_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS meetup_answers (
                    meetup_id   INTEGER NOT NULL,
                    user_id     INTEGER NOT NULL,
                    answer      TEXT    NOT NULL,
                    answered_at INTEGER NOT NULL,
                    PRIMARY KEY (meetup_id, user_id),
                    FOREIGN KEY (meetup_id) REFERENCES meetups(meetup_id) ON DELETE CASCADE
                );
                """
            )
            db.execute(f"PRAGMA user_version = {CURRENT_SCHEMA_VERSION}")

    # ----- writes -------------------------------------------------------

    def create(
        self,
        *,
        guild_id: int,
        channel_id: int,
        organizer_id: int,
        title: str,
        location: str,
        timezone: str,
        activities: Sequence[str],
        times: Sequence[tuple[int, str]],
        now: int | None = None,
    ) -> int:
        """Insert a new poll and return its id."""
        created = int(now if now is not None else time.time())
        with self._lock, self._database() as db:
            cursor = db.execute(
                """
                INSERT INTO meetups (
                    guild_id, channel_id, organizer_id, title, location,
                    timezone, state, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    guild_id,
                    channel_id,
                    organizer_id,
                    title,
                    location,
                    timezone,
                    STATE_POLLING,
                    created,
                ),
            )
            meetup_id = int(cursor.lastrowid)
            rows = [
                (meetup_id, AXIS_ACTIVITY, activity, activity, index)
                for index, activity in enumerate(activities)
            ]
            rows += [
                (meetup_id, AXIS_TIME, str(epoch), source, index)
                for index, (epoch, source) in enumerate(times)
            ]
            db.executemany(
                "INSERT INTO meetup_options VALUES (?, ?, ?, ?, ?)", rows
            )
        LOGGER.info("Created meetup %d (%s) in channel %d", meetup_id, title, channel_id)
        return meetup_id

    def attach_message(
        self, meetup_id: int, message_id: int, thread_id: int | None
    ) -> None:
        """Record the poll message (and its thread, when one was created)."""
        with self._lock, self._database() as db:
            db.execute(
                "UPDATE meetups SET message_id = ?, thread_id = ? WHERE meetup_id = ?",
                (message_id, thread_id, meetup_id),
            )

    def set_votes(
        self, meetup_id: int, user_id: int, axis: str, keys: Iterable[str]
    ) -> None:
        """Replace one member's votes on one axis.

        Replacement rather than accumulation is what makes "actually, not
        Friday" expressible, and an empty ``keys`` is a real answer.
        """
        with self._lock, self._database() as db:
            db.execute(
                "DELETE FROM meetup_votes WHERE meetup_id = ? AND user_id = ? AND axis = ?",
                (meetup_id, user_id, axis),
            )
            db.executemany(
                "INSERT INTO meetup_votes VALUES (?, ?, ?, ?)",
                [(meetup_id, user_id, axis, key) for key in keys],
            )

    def lock(self, meetup_id: int, activity: str, moment: int) -> None:
        """Fix the winning activity and time, moving the meetup to LOCKED."""
        with self._lock, self._database() as db:
            db.execute(
                """
                UPDATE meetups
                   SET state = ?, locked_activity = ?, locked_time = ?
                 WHERE meetup_id = ?
                """,
                (STATE_LOCKED, activity, int(moment), meetup_id),
            )
        LOGGER.info("Locked meetup %d to %s @ %d", meetup_id, activity, moment)

    def set_state(self, meetup_id: int, state: str) -> None:
        """Move a meetup to another lifecycle state."""
        closed_at = int(time.time()) if state == STATE_CLOSED else None
        with self._lock, self._database() as db:
            db.execute(
                "UPDATE meetups SET state = ?, closed_at = ? WHERE meetup_id = ?",
                (state, closed_at, meetup_id),
            )

    def record_answer(self, meetup_id: int, user_id: int, answer: str) -> None:
        """Store one member's explicit attendance answer."""
        with self._lock, self._database() as db:
            db.execute(
                """
                INSERT INTO meetup_answers VALUES (?, ?, ?, ?)
                ON CONFLICT(meetup_id, user_id)
                DO UPDATE SET answer = excluded.answer, answered_at = excluded.answered_at
                """,
                (meetup_id, user_id, answer, int(time.time())),
            )

    def delete(self, meetup_id: int) -> None:
        """Remove a meetup and everything hanging off it."""
        with self._lock, self._database() as db:
            db.execute("DELETE FROM meetups WHERE meetup_id = ?", (meetup_id,))

    # ----- reads --------------------------------------------------------

    def _hydrate(self, db: sqlite3.Connection, row: sqlite3.Row) -> Meetup:
        """Build a full :class:`Meetup` from its base row."""
        meetup = Meetup(
            meetup_id=int(row["meetup_id"]),
            guild_id=int(row["guild_id"]),
            channel_id=int(row["channel_id"]),
            organizer_id=int(row["organizer_id"]),
            title=str(row["title"]),
            location=str(row["location"]),
            timezone=str(row["timezone"]),
            state=str(row["state"]),
            created_at=int(row["created_at"]),
            message_id=row["message_id"],
            thread_id=row["thread_id"],
            locked_activity=row["locked_activity"],
            locked_time=row["locked_time"],
            closed_at=row["closed_at"],
        )
        meetup.options = [
            MeetupOption(
                axis=str(option["axis"]),
                key=str(option["option_key"]),
                label=str(option["label"]),
                position=int(option["position"]),
            )
            for option in db.execute(
                """
                SELECT axis, option_key, label, position FROM meetup_options
                 WHERE meetup_id = ? ORDER BY axis, position
                """,
                (meetup.meetup_id,),
            )
        ]
        votes: dict[str, dict[str, list[int]]] = {axis: {} for axis in AXES}
        for option in meetup.options:
            votes.setdefault(option.axis, {}).setdefault(option.key, [])
        for vote in db.execute(
            "SELECT user_id, axis, option_key FROM meetup_votes WHERE meetup_id = ?",
            (meetup.meetup_id,),
        ):
            votes.setdefault(str(vote["axis"]), {}).setdefault(
                str(vote["option_key"]), []
            ).append(int(vote["user_id"]))
        meetup.votes = votes
        meetup.answers = {
            int(answer["user_id"]): str(answer["answer"])
            for answer in db.execute(
                "SELECT user_id, answer FROM meetup_answers WHERE meetup_id = ?",
                (meetup.meetup_id,),
            )
        }
        return meetup

    def get(self, meetup_id: int) -> Meetup | None:
        """Return one meetup by id."""
        with self._lock, self._database() as db:
            row = db.execute(
                "SELECT * FROM meetups WHERE meetup_id = ?", (meetup_id,)
            ).fetchone()
            return self._hydrate(db, row) if row else None

    def for_message(self, message_id: int) -> Meetup | None:
        """Return the meetup a poll message belongs to."""
        with self._lock, self._database() as db:
            row = db.execute(
                "SELECT * FROM meetups WHERE message_id = ?", (message_id,)
            ).fetchone()
            return self._hydrate(db, row) if row else None

    def _query(self, sql: str, parameters: Sequence[object]) -> list[Meetup]:
        """Hydrate every meetup matching one query."""
        with self._lock, self._database() as db:
            rows = db.execute(sql, tuple(parameters)).fetchall()
            return [self._hydrate(db, row) for row in rows]

    def open_in_guild(self, guild_id: int) -> list[Meetup]:
        """Return every meetup in one guild that has not closed yet."""
        return self._query(
            """
            SELECT * FROM meetups
             WHERE guild_id = ? AND state != ?
             ORDER BY COALESCE(locked_time, created_at)
            """,
            (guild_id, STATE_CLOSED),
        )

    def latest_open_in_channel(self, channel_id: int) -> Meetup | None:
        """Return the newest open meetup posted in one channel."""
        found = self._query(
            """
            SELECT * FROM meetups
             WHERE channel_id = ? AND state != ?
             ORDER BY created_at DESC LIMIT 1
            """,
            (channel_id, STATE_CLOSED),
        )
        return found[0] if found else None

    def due_for_close(self, now: int | None = None) -> list[Meetup]:
        """Return meetups whose time has passed, or whose poll went stale."""
        moment = int(now if now is not None else time.time())
        return self._query(
            """
            SELECT * FROM meetups
             WHERE state != ?
               AND (
                    (locked_time IS NOT NULL AND locked_time + ? <= ?)
                 OR (locked_time IS NULL AND created_at + ? <= ?)
               )
            """,
            (STATE_CLOSED, CLOSE_GRACE_SECONDS, moment, STALE_POLL_SECONDS, moment),
        )


_default_store: MeetupStore | None = None
_default_lock = threading.Lock()


def get_meetup_store() -> MeetupStore:
    """Return the process-wide meetup store."""
    global _default_store
    if _default_store is None:
        with _default_lock:
            if _default_store is None:
                _default_store = MeetupStore()
    return _default_store

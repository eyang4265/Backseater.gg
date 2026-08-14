"""JSON-backed persistence for accounts and poller state.

Every writer goes through :func:`write_json`, which writes to a sibling temp
file and renames, so a crash mid-write can't truncate a state file.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .config import JSON_DIR
from .queues import FLEX_QUEUE_ID, SOLO_QUEUE_ID
from .ranks import RankSnapshot

LOGGER = logging.getLogger(__name__)

DATA_PATH = JSON_DIR / "data.json"
TRACKER_STATE_PATH = JSON_DIR / "match_tracker_state.json"
GUEST_STATE_PATH = JSON_DIR / "guest_tracker_state.json"
LIVE_GAME_STATE_PATH = JSON_DIR / "live_game_state.json"

#: How many match ids to remember per player before dropping the oldest.
MATCH_HISTORY_LIMIT = 100

#: Persisted key per ranked queue, kept for on-disk compatibility.
_QUEUE_STATE_KEYS = {SOLO_QUEUE_ID: "solo", FLEX_QUEUE_ID: "flex"}

_write_lock = threading.Lock()


def read_json(path: Path, default: Any) -> Any:
    try:
        with path.open(encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        return default
    except (json.JSONDecodeError, OSError, ValueError) as error:
        LOGGER.warning("Could not read %s (%s); using default", path, error)
        return default


def write_json(path: Path, payload: Any, *, indent: int = 2) -> None:
    """Atomically replace ``path`` with ``payload``."""
    temporary = path.with_suffix(path.suffix + ".tmp")
    with _write_lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=indent)
        temporary.replace(path)


def dedupe_tail(ids: Iterable[str], limit: int = MATCH_HISTORY_LIMIT) -> list[str]:
    """Keep the last ``limit`` ids, de-duplicated, preserving order.

    The previous implementation sliced a ``set``, whose iteration order is
    unrelated to recency — so trimming could discard the *newest* ids and let
    already-announced matches be re-announced later.
    """
    seen: set[str] = set()
    ordered: list[str] = []
    for match_id in ids:
        if match_id not in seen:
            seen.add(match_id)
            ordered.append(match_id)
    return ordered[-limit:]


# -- tracked accounts ------------------------------------------------------


@dataclass(frozen=True)
class Account:
    discord_id: str
    puuid: str
    server: str
    riot_id: str

    def to_json(self) -> dict[str, str]:
        return {"puuid": self.puuid, "server": self.server, "riotId": self.riot_id}


def load_accounts() -> dict[str, Account]:
    """Every tracked account, keyed by Discord id."""
    raw = read_json(DATA_PATH, {})
    accounts: dict[str, Account] = {}
    for discord_id, entry in raw.items() if isinstance(raw, dict) else ():
        if not isinstance(entry, dict) or not entry.get("puuid"):
            LOGGER.warning("Skipping malformed data.json entry for %s", discord_id)
            continue
        accounts[str(discord_id)] = Account(
            discord_id=str(discord_id),
            puuid=entry["puuid"],
            server=entry.get("server") or "NA1",
            riot_id=entry.get("riotId") or "Unknown player",
        )
    return accounts


def save_accounts(accounts: dict[str, Account]) -> None:
    write_json(
        DATA_PATH,
        {discord_id: account.to_json() for discord_id, account in accounts.items()},
        indent=4,
    )


class _TrackedPuuidCache:
    """Caches the tracked-puuid set, invalidated by data.json's mtime.

    Column builders ask for this once per participant; without the cache that
    was a JSON parse per player per embed.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._mtime: float | None = None
        self._puuids: frozenset[str] = frozenset()

    def get(self) -> frozenset[str]:
        try:
            mtime = DATA_PATH.stat().st_mtime
        except OSError:
            return frozenset()
        with self._lock:
            if mtime != self._mtime:
                self._puuids = frozenset(a.puuid for a in load_accounts().values())
                self._mtime = mtime
            return self._puuids


_tracked_puuid_cache = _TrackedPuuidCache()


def tracked_puuids() -> frozenset[str]:
    """Puuids of every account in data.json. Used to bold them in lobbies."""
    return _tracked_puuid_cache.get()


def puuid_for_discord_id(discord_id: int | str) -> str | None:
    account = load_accounts().get(str(discord_id))
    return account.puuid if account else None


def server_for_puuid(puuid: str) -> str | None:
    return next(
        (account.server for account in load_accounts().values() if account.puuid == puuid),
        None,
    )


# -- match tracker state ---------------------------------------------------


@dataclass
class PlayerState:
    """Per-player poller state: which matches were seen, and last known ranks."""

    matches: list[str] = field(default_factory=list)
    ranks: dict[int, RankSnapshot | None] = field(default_factory=dict)

    @classmethod
    def from_json(cls, raw: Any) -> "PlayerState":
        # The original format stored a bare list of match ids.
        if isinstance(raw, list):
            return cls(matches=dedupe_tail(raw))
        if not isinstance(raw, dict):
            return cls()
        return cls(
            matches=dedupe_tail(raw.get("matches") or []),
            ranks={
                queue_id: RankSnapshot.from_state(raw.get(key))
                for queue_id, key in _QUEUE_STATE_KEYS.items()
            },
        )

    def to_json(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"matches": self.matches}
        for queue_id, key in _QUEUE_STATE_KEYS.items():
            snapshot = self.ranks.get(queue_id)
            payload[key] = snapshot.to_state() if snapshot else None
        return payload

    def remember(self, match_ids: Iterable[str]) -> None:
        self.matches = dedupe_tail([*self.matches, *match_ids])


def load_tracker_state() -> dict[str, PlayerState]:
    raw = read_json(TRACKER_STATE_PATH, {})
    if not isinstance(raw, dict):
        return {}
    return {str(key): PlayerState.from_json(value) for key, value in raw.items()}


def save_tracker_state(state: dict[str, PlayerState]) -> None:
    write_json(TRACKER_STATE_PATH, {key: value.to_json() for key, value in state.items()})


# -- guest tracker state ---------------------------------------------------


def load_guest_matches() -> list[str]:
    raw = read_json(GUEST_STATE_PATH, {})
    matches = raw.get("matches") if isinstance(raw, dict) else None
    return dedupe_tail(matches or [])


def save_guest_matches(match_ids: Iterable[str]) -> None:
    write_json(GUEST_STATE_PATH, {"matches": dedupe_tail(match_ids)})


# -- live-game announcement state ----------------------------------------


def load_live_game_state() -> dict[str, str]:
    """Tracked Discord id -> the live game most recently announced for it."""
    raw = read_json(LIVE_GAME_STATE_PATH, {})
    if not isinstance(raw, dict):
        return {}
    return {str(discord_id): str(game_id) for discord_id, game_id in raw.items() if game_id}


def save_live_game_state(state: dict[str, str]) -> None:
    write_json(LIVE_GAME_STATE_PATH, state)

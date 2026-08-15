"""JSON-backed persistence for accounts and poller state.

Every writer goes through :func:`write_json`, which writes to a sibling temp
file and renames, so a crash mid-write can't truncate a state file.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable
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
GUILD_STATE_PATH = JSON_DIR / "guilds.json"


MATCH_HISTORY_LIMIT = 100
RANK_HISTORY_LIMIT = 500


_QUEUE_STATE_KEYS = {SOLO_QUEUE_ID: "solo", FLEX_QUEUE_ID: "flex"}

_write_lock = threading.RLock()


def read_json(path: Path, default: Any) -> Any:
    """Read json."""
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


@dataclass(frozen=True)
class Account:
    discord_id: str
    puuid: str
    server: str
    riot_id: str

    def to_json(self) -> dict[str, str]:
        """Handle json."""
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
    """Save accounts."""
    write_json(
        DATA_PATH,
        {discord_id: account.to_json() for discord_id, account in accounts.items()},
        indent=4,
    )
    _tracked_puuid_cache.invalidate()


def update_accounts(mutate: Callable[[dict[str, Account]], None]) -> dict[str, Account]:
    """Atomically read, mutate, and persist the tracked-account registry."""
    with _write_lock:
        accounts = load_accounts()
        mutate(accounts)
        save_accounts(accounts)
        return accounts


class _TrackedPuuidCache:
    """Caches the tracked-puuid set, invalidated by data.json's mtime.

    Column builders ask for this once per participant; without the cache that
    was a JSON parse per player per embed.
    """

    def __init__(self) -> None:
        """Initialize the instance."""
        self._lock = threading.Lock()
        self._mtime: float | None = None
        self._puuids: frozenset[str] = frozenset()

    def get(self) -> frozenset[str]:
        """Return get."""
        try:
            mtime = DATA_PATH.stat().st_mtime
        except OSError:
            return frozenset()
        with self._lock:
            if mtime != self._mtime:
                self._puuids = frozenset(a.puuid for a in load_accounts().values())
                self._mtime = mtime
            return self._puuids

    def invalidate(self) -> None:
        """Handle invalidate."""
        with self._lock:
            self._mtime = None
            self._puuids = frozenset()


_tracked_puuid_cache = _TrackedPuuidCache()


def tracked_puuids() -> frozenset[str]:
    """Puuids of every account in data.json. Used to bold them in lobbies."""
    return _tracked_puuid_cache.get()


def puuid_for_discord_id(discord_id: int | str) -> str | None:
    """Handle for discord id."""
    account = load_accounts().get(str(discord_id))
    return account.puuid if account else None


def server_for_puuid(puuid: str) -> str | None:
    """Handle for puuid."""
    return next(
        (
            account.server
            for account in load_accounts().values()
            if account.puuid == puuid
        ),
        None,
    )


@dataclass
class PlayerState:
    """Per-player poller state: which matches were seen, and last known ranks."""

    matches: list[str] = field(default_factory=list)
    ranks: dict[int, RankSnapshot | None] = field(default_factory=dict)
    history: dict[int, list[dict[str, Any]]] = field(default_factory=dict)

    @classmethod
    def from_json(cls, raw: Any) -> "PlayerState":
        """Handle json."""
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
            history={
                int(queue_id): [entry for entry in entries if isinstance(entry, dict)][
                    -RANK_HISTORY_LIMIT:
                ]
                for queue_id, entries in (raw.get("history") or {}).items()
                if str(queue_id).isdigit() and isinstance(entries, list)
            },
        )

    def to_json(self) -> dict[str, Any]:
        """Handle json."""
        payload: dict[str, Any] = {
            "matches": self.matches,
            "history": {
                str(key): value[-RANK_HISTORY_LIMIT:]
                for key, value in self.history.items()
            },
        }
        for queue_id, key in _QUEUE_STATE_KEYS.items():
            snapshot = self.ranks.get(queue_id)
            payload[key] = snapshot.to_state() if snapshot else None
        return payload

    def remember(self, match_ids: Iterable[str]) -> None:
        """Handle remember."""
        self.matches = dedupe_tail([*self.matches, *match_ids])

    def record_rank(
        self,
        queue_id: int,
        snapshot: RankSnapshot,
        *,
        match_id: str | None,
        won: bool | None,
        attributable: bool = True,
        timestamp: int | None = None,
    ) -> bool:
        """Store a rank point, using null delta for baselines and resyncs."""
        source_id = match_id
        if source_id and any(
            entry.get("s", entry.get("m")) == source_id
            for entry in self.history.get(queue_id, [])
        ):
            return False
        value = snapshot.value
        if value is None:
            changed = self.ranks.get(queue_id) != snapshot
            self.ranks[queue_id] = snapshot
            return changed
        previous = self.ranks.get(queue_id)
        previous_value = previous.value if previous else None

        reset = bool(previous and snapshot.games < previous.games)
        delta = (
            value - previous_value
            if attributable and previous_value is not None and not reset
            else None
        )
        entry = {
            "t": int(timestamp if timestamp is not None else time.time()),
            "v": value,
            "d": delta,
            "m": match_id if attributable else None,
            "s": source_id,
            "w": won if attributable else None,
        }
        self.history.setdefault(queue_id, []).append(entry)
        self.history[queue_id] = self.history[queue_id][-RANK_HISTORY_LIMIT:]
        self.ranks[queue_id] = snapshot
        return True


def load_tracker_state() -> dict[str, PlayerState]:
    """Load tracker state."""
    raw = read_json(TRACKER_STATE_PATH, {})
    if not isinstance(raw, dict):
        return {}
    return {str(key): PlayerState.from_json(value) for key, value in raw.items()}


def save_tracker_state(state: dict[str, PlayerState]) -> None:
    """Save tracker state."""
    write_json(
        TRACKER_STATE_PATH, {key: value.to_json() for key, value in state.items()}
    )


def load_guest_matches() -> list[str]:
    """Load guest matches."""
    raw = read_json(GUEST_STATE_PATH, {})
    matches = raw.get("matches") if isinstance(raw, dict) else None
    return dedupe_tail(matches or [])


def save_guest_matches(match_ids: Iterable[str]) -> None:
    """Save guest matches."""
    write_json(GUEST_STATE_PATH, {"matches": dedupe_tail(match_ids)})


def load_live_game_state() -> dict[str, str]:
    """Tracked Discord id -> the live game most recently announced for it."""
    raw = read_json(LIVE_GAME_STATE_PATH, {})
    if not isinstance(raw, dict):
        return {}
    return {
        str(discord_id): str(game_id) for discord_id, game_id in raw.items() if game_id
    }


def save_live_game_state(state: dict[str, str]) -> None:
    """Save live game state."""
    write_json(LIVE_GAME_STATE_PATH, state)


def load_guild_channels() -> dict[str, int]:
    """Load guild channels."""
    raw = read_json(GUILD_STATE_PATH, {})
    if not isinstance(raw, dict):
        return {}
    result: dict[str, int] = {}
    for guild_id, entry in raw.items():
        channel_id = (
            entry.get("announcement_channel_id") if isinstance(entry, dict) else None
        )
        if isinstance(channel_id, int) and channel_id > 0:
            result[str(guild_id)] = channel_id
    return result


def save_guild_channels(channels: dict[str, int]) -> None:
    """Save guild channels."""
    write_json(
        GUILD_STATE_PATH,
        {
            guild_id: {"announcement_channel_id": channel_id}
            for guild_id, channel_id in channels.items()
        },
    )


def update_guild_channels(
    mutate: Callable[[dict[str, int]], None],
) -> dict[str, int]:
    """Atomically read, mutate, and persist per-guild announcement routes."""
    with _write_lock:
        channels = load_guild_channels()
        mutate(channels)
        save_guild_channels(channels)
        return channels

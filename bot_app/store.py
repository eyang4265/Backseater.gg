"""Private runtime persistence for accounts and poller state.

Every writer goes through :func:`write_json`, which writes to a sibling temp
file and renames, so a crash mid-write can't truncate a state file.
Persistent Discord component records are delegated to incremental SQLite
storage. Legacy ``json/`` files are copied into the gitignored runtime directory
on first use and retained only as migration backups.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .config import DATA_DIR, JSON_DIR, migrate_legacy_runtime_file
from .queues import FLEX_QUEUE_ID, SOLO_QUEUE_ID
from .ranks import RankSnapshot

LOGGER = logging.getLogger(__name__)

DATA_PATH = DATA_DIR / "accounts.json"
TFT_DATA_PATH = DATA_DIR / "tft_accounts.json"
TEAMMATE_DATA_PATH = DATA_DIR / "teammates.json"
TRACKER_STATE_PATH = DATA_DIR / "match_tracker_state.json"
TFT_TRACKER_STATE_PATH = DATA_DIR / "tft_match_tracker_state.json"
LIVE_GAME_STATE_PATH = DATA_DIR / "live_game_state.json"
LIVE_GAME_MESSAGE_PATH = DATA_DIR / "live_game_messages.json"
TFT_LIVE_GAME_STATE_PATH = DATA_DIR / "tft_live_game_state.json"
GUILD_STATE_PATH = DATA_DIR / "guilds.json"
FLAKE_RANKS_PATH = DATA_DIR / "flake_ranks.json"
EMBED_BUTTON_STATE_PATH = DATA_DIR / "embed_button_state.json"

_LEGACY_RUNTIME_PATHS = {
    DATA_PATH: JSON_DIR / "data.json",
    TFT_DATA_PATH: JSON_DIR / "tft_data.json",
    TEAMMATE_DATA_PATH: JSON_DIR / "teammates.json",
    TRACKER_STATE_PATH: JSON_DIR / "match_tracker_state.json",
    TFT_TRACKER_STATE_PATH: JSON_DIR / "tft_match_tracker_state.json",
    LIVE_GAME_STATE_PATH: JSON_DIR / "live_game_state.json",
    LIVE_GAME_MESSAGE_PATH: JSON_DIR / "live_game_messages.json",
    TFT_LIVE_GAME_STATE_PATH: JSON_DIR / "tft_live_game_state.json",
    GUILD_STATE_PATH: JSON_DIR / "guilds.json",
    FLAKE_RANKS_PATH: JSON_DIR / "flake_ranks.json",
    EMBED_BUTTON_STATE_PATH: JSON_DIR / "embed_button_state.json",
}


MATCH_HISTORY_LIMIT = 100
RANK_HISTORY_LIMIT = 500
EMBED_BUTTON_STATE_LIMIT = 500
# How many still-open live-game lobbies we keep posted-message records for. A
# lobby is normally removed the moment its match is announced or the players
# leave it; this cap only bounds the file if the bot misses that completion.
LIVE_GAME_MESSAGE_LIMIT = 200


_QUEUE_STATE_KEYS = {SOLO_QUEUE_ID: "solo", FLEX_QUEUE_ID: "flex"}

_write_lock = threading.RLock()
_flake_write_lock = threading.RLock()
_league_state_lock = threading.RLock()
_tft_state_lock = threading.RLock()


@contextmanager
def league_state_transaction():
    """Serialize a complete League state read-modify-write operation."""
    with _league_state_lock:
        yield


@contextmanager
def tft_state_transaction():
    """Serialize a complete TFT state read-modify-write operation."""
    with _tft_state_lock:
        yield


def read_json(path: Path, default: Any) -> Any:
    """Read json."""
    legacy = _LEGACY_RUNTIME_PATHS.get(path)
    if legacy is not None:
        migrate_legacy_runtime_file(path, legacy)
    try:
        with path.open(encoding="utf-8") as handle:
            payload = json.load(handle)
            LOGGER.debug("Read %s", path)
            return payload
    except FileNotFoundError:
        LOGGER.debug("%s does not exist; using default", path)
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
        LOGGER.debug("Wrote %s", path)


def load_flake_ranks() -> dict[str, dict[str, dict[str, str]]]:
    """Load server-scoped Discord-user flake rankings."""
    raw = read_json(FLAKE_RANKS_PATH, {})
    if not isinstance(raw, dict):
        return {}
    return {
        str(guild_id): {
            str(user_id): entry
            for user_id, entry in entries.items()
            if isinstance(entry, dict)
            and entry.get("tier") in {"S", "A", "B", "C", "D", "F", "Unknown"}
        }
        for guild_id, entries in raw.items()
        if isinstance(entries, dict)
    }


def set_flake_rank(
    guild_id: int | str, user_id: int | str, display_name: str, tier: str
) -> dict[str, dict[str, str]]:
    """Atomically assign one Discord user to one flake tier for a server."""
    return set_flake_ranks(guild_id, ((user_id, display_name),), tier)


def set_flake_ranks(
    guild_id: int | str,
    users: Iterable[tuple[int | str, str]],
    tier: str,
) -> dict[str, dict[str, str]]:
    """Atomically assign multiple Discord users to one server flake tier."""
    normalized_tier = "Unknown" if tier.casefold() == "unknown" else tier.upper()
    if normalized_tier not in {"S", "A", "B", "C", "D", "F", "Unknown"}:
        raise ValueError(f"Unknown flake tier: {tier}")
    with _flake_write_lock:
        rankings = load_flake_ranks()
        server_rankings = rankings.setdefault(str(guild_id), {})
        for user_id, display_name in users:
            server_rankings[str(user_id)] = {
                "display_name": display_name,
                "tier": normalized_tier,
            }
        temporary = FLAKE_RANKS_PATH.with_suffix(FLAKE_RANKS_PATH.suffix + ".tmp")
        FLAKE_RANKS_PATH.parent.mkdir(parents=True, exist_ok=True)
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(rankings, handle, indent=2)
        temporary.replace(FLAKE_RANKS_PATH)
        LOGGER.debug("Wrote %s", FLAKE_RANKS_PATH)
        return dict(server_rankings)


def load_embed_button_states() -> list[dict[str, Any]]:
    """Load persistent views from SQLite, importing the legacy JSON once.

    The former JSON implementation rewrote every full Match-V5 payload for
    every new message.  Existing installations retain that file as a
    backward-compatible import source, while all current reads and writes are
    incremental SQLite operations.
    """
    from .match_cache import get_match_cache

    cache = get_match_cache()
    stored = cache.load_embed_button_states()
    if stored:
        return stored

    raw = read_json(EMBED_BUTTON_STATE_PATH, [])
    if not isinstance(raw, list):
        return []
    legacy = [
        entry
        for entry in raw
        if isinstance(entry, dict)
        and isinstance(entry.get("message_id"), int)
        and isinstance(entry.get("kind"), str)
        and isinstance(entry.get("payload"), dict)
    ]
    if legacy:
        imported = cache.import_embed_button_states(legacy)
        if imported:
            LOGGER.info("Imported %d legacy persistent view records into SQLite", imported)
            stored = cache.load_embed_button_states()
            if stored:
                return stored
    return legacy


def remember_embed_button_state(
    message_id: int, channel_id: int, kind: str, payload: dict[str, Any]
) -> None:
    """Incrementally remember one persistent component view in SQLite."""
    from .match_cache import get_match_cache

    if get_match_cache().remember_embed_button_state(
        message_id, channel_id, kind, payload
    ):
        return

    # Preserve the old atomic JSON path as a degradation mode if SQLite is
    # corrupt or unavailable; persistence should fail soft, not kill a command.
    with _write_lock:
        states = load_embed_button_states()
        states = [entry for entry in states if entry.get("message_id") != message_id]
        states.append(
            {
                "message_id": message_id,
                "channel_id": channel_id,
                "kind": kind,
                "payload": payload,
            }
        )
        states = states[-EMBED_BUTTON_STATE_LIMIT:]
        write_json(EMBED_BUTTON_STATE_PATH, states, indent=2)


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
            LOGGER.warning("Skipping malformed account entry for %s", discord_id)
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
    LOGGER.info("Saved %d tracked accounts", len(accounts))
    _tracked_puuid_cache.invalidate()


def load_teammates() -> dict[str, Account]:
    """Every ``/teammate`` PUUID, keyed by PUUID.

    Teammates are not tracked accounts: the pollers never fetch their games
    and they never trigger an announcement of their own.  They exist only so
    that a completed-match announcement or ``/match`` for a tracked account
    account also bolds a teammate who happened to share that lobby.  The
    ``discord_id`` field is optional — it is only set when ``/teammate`` was
    told which Discord user the PUUID belongs to — and is left blank
    otherwise.
    """
    raw = read_json(TEAMMATE_DATA_PATH, {})
    teammates: dict[str, Account] = {}
    for puuid, entry in raw.items() if isinstance(raw, dict) else ():
        if not isinstance(entry, dict) or not puuid:
            LOGGER.warning("Skipping malformed teammates.json entry for %s", puuid)
            continue
        discord_id = entry.get("discordId")
        teammates[str(puuid)] = Account(
            discord_id=str(discord_id) if discord_id else "",
            puuid=str(puuid),
            server=entry.get("server") or "NA1",
            riot_id=entry.get("riotId") or "Unknown teammate",
        )
    return teammates


def save_teammates(teammates: dict[str, Account]) -> None:
    """Atomically persist the ``/teammate`` PUUID registry."""
    write_json(
        TEAMMATE_DATA_PATH,
        {
            account.puuid: {
                "server": account.server,
                "riotId": account.riot_id,
                **({"discordId": account.discord_id} if account.discord_id else {}),
            }
            for account in teammates.values()
        },
        indent=4,
    )
    LOGGER.info("Saved %d teammate PUUIDs", len(teammates))
    _teammate_puuid_cache.invalidate()


def update_teammates(
    mutate: Callable[[dict[str, Account]], None],
) -> dict[str, Account]:
    """Atomically read, mutate, and persist the teammate PUUID registry."""
    with _league_state_lock, _write_lock:
        teammates = load_teammates()
        mutate(teammates)
        save_teammates(teammates)
        return teammates


def load_tft_accounts() -> dict[str, Account]:
    """Every separately linked TFT account, keyed by Discord id."""
    raw = read_json(TFT_DATA_PATH, {})
    accounts: dict[str, Account] = {}
    for discord_id, entry in raw.items() if isinstance(raw, dict) else ():
        if not isinstance(entry, dict) or not entry.get("puuid"):
            LOGGER.warning("Skipping malformed TFT account entry for %s", discord_id)
            continue
        accounts[str(discord_id)] = Account(
            discord_id=str(discord_id),
            puuid=entry["puuid"],
            server=entry.get("server") or "NA1",
            riot_id=entry.get("riotId") or "Unknown TFT player",
        )
    return accounts


def save_tft_accounts(accounts: dict[str, Account]) -> None:
    """Atomically save the independent TFT account registry."""
    write_json(
        TFT_DATA_PATH,
        {discord_id: account.to_json() for discord_id, account in accounts.items()},
        indent=4,
    )
    LOGGER.info("Saved %d tracked TFT accounts", len(accounts))


def update_tft_accounts(
    mutate: Callable[[dict[str, Account]], None],
) -> dict[str, Account]:
    """Atomically read, mutate, and persist the TFT account registry."""
    with _tft_state_lock, _write_lock:
        accounts = load_tft_accounts()
        mutate(accounts)
        save_tft_accounts(accounts)
        return accounts


def update_accounts(mutate: Callable[[dict[str, Account]], None]) -> dict[str, Account]:
    """Atomically read, mutate, and persist the tracked-account registry."""
    with _league_state_lock, _write_lock:
        accounts = load_accounts()
        mutate(accounts)
        save_accounts(accounts)
        return accounts


class _TrackedPuuidCache:
    """Caches the tracked-puuid set, invalidated by the registry's mtime.

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


class _TeammatePuuidCache:
    """Caches the teammate-puuid set, invalidated by teammates.json's mtime."""

    def __init__(self) -> None:
        """Initialize the instance."""
        self._lock = threading.Lock()
        self._mtime: float | None = None
        self._puuids: frozenset[str] = frozenset()

    def get(self) -> frozenset[str]:
        """Return the cached teammate puuid set, refreshing on file change."""
        try:
            mtime = TEAMMATE_DATA_PATH.stat().st_mtime
        except OSError:
            return frozenset()
        with self._lock:
            if mtime != self._mtime:
                self._puuids = frozenset(load_teammates())
                self._mtime = mtime
            return self._puuids

    def invalidate(self) -> None:
        """Drop the cached set so the next read reloads from disk."""
        with self._lock:
            self._mtime = None
            self._puuids = frozenset()


_teammate_puuid_cache = _TeammatePuuidCache()


def tracked_puuids() -> frozenset[str]:
    """PUUIDs of every tracked account, used to bold them in lobbies."""
    return _tracked_puuid_cache.get()


def teammate_puuids() -> frozenset[str]:
    """Puuids added through ``/teammate``.

    Bolded in completed-match announcements and ``/match`` alongside real
    tracked players, but only when the same lobby also holds a registered
    account — a teammate never surfaces a match on their own.
    """
    return _teammate_puuid_cache.get()


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
        LOGGER.debug(
            "Recorded rank point for queue %s: value=%s delta=%s match=%s",
            queue_id,
            value,
            delta,
            match_id,
        )
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


@dataclass
class TftPlayerState:
    """Per-TFT-account match history, first-poll migration, and last TFT rank.

    ``rank`` is the account's most recently seen Ranked TFT standing, kept only
    so a completed-match announcement can show the LP delta since the previous
    ranked game the same way League announcements do.
    """

    matches: list[str] = field(default_factory=list)
    initialized: bool = False
    rank: RankSnapshot | None = None

    @classmethod
    def from_json(cls, raw: Any) -> "TftPlayerState":
        """Decode one TFT tracker record."""
        if not isinstance(raw, dict):
            return cls()
        return cls(
            matches=dedupe_tail(raw.get("matches") or []),
            initialized=bool(raw.get("initialized", False)),
            rank=RankSnapshot.from_state(raw.get("rank")),
        )

    def to_json(self) -> dict[str, Any]:
        """Encode one TFT tracker record."""
        return {
            "matches": self.matches,
            "initialized": self.initialized,
            "rank": self.rank.to_state() if self.rank else None,
        }

    def remember(self, match_ids: Iterable[str]) -> None:
        """Remember completed TFT match ids."""
        self.matches = dedupe_tail([*self.matches, *match_ids])
        self.initialized = True


def load_tft_tracker_state() -> dict[str, TftPlayerState]:
    """Load independent TFT completed-match deduplication state."""
    raw = read_json(TFT_TRACKER_STATE_PATH, {})
    if not isinstance(raw, dict):
        return {}
    return {str(key): TftPlayerState.from_json(value) for key, value in raw.items()}


def save_tft_tracker_state(state: dict[str, TftPlayerState]) -> None:
    """Save independent TFT completed-match deduplication state."""
    write_json(
        TFT_TRACKER_STATE_PATH,
        {key: value.to_json() for key, value in state.items()},
    )


def load_live_game_state() -> dict[str, str]:
    """Tracked Discord id -> the live game it is currently in.

    The live poller retires an entry as soon as the player is confirmed to be
    in no game, so this maps only to lobbies still in progress.
    """
    raw = read_json(LIVE_GAME_STATE_PATH, {})
    if not isinstance(raw, dict):
        return {}
    return {
        str(discord_id): str(game_id) for discord_id, game_id in raw.items() if game_id
    }


def save_live_game_state(state: dict[str, str]) -> None:
    """Save live game state."""
    write_json(LIVE_GAME_STATE_PATH, state)


def load_live_game_messages() -> dict[str, list[list[int]]]:
    """Live-game key (``platform:gameId``) -> the ``[channel_id, message_id]``
    pairs of every automatic live-game announcement posted for that lobby.

    These records let the match poller delete the "in a live game" post once the
    game is over. ``/livegame`` responses are deliberately never recorded here.
    """
    raw = read_json(LIVE_GAME_MESSAGE_PATH, {})
    if not isinstance(raw, dict):
        return {}
    cleaned: dict[str, list[list[int]]] = {}
    for key, pairs in raw.items():
        if not isinstance(pairs, list):
            continue
        valid = [
            [int(pair[0]), int(pair[1])]
            for pair in pairs
            if isinstance(pair, (list, tuple))
            and len(pair) == 2
            and all(isinstance(part, int) for part in pair)
        ]
        if valid:
            cleaned[str(key)] = valid
    return cleaned


def save_live_game_messages(records: dict[str, list[list[int]]]) -> None:
    """Atomically persist the posted live-game announcement message records."""
    write_json(LIVE_GAME_MESSAGE_PATH, records)


def remember_live_game_message(
    game_key: str, channel_id: int, message_id: int
) -> None:
    """Record one posted live-game announcement message under its lobby key."""
    if not (isinstance(channel_id, int) and isinstance(message_id, int)):
        return
    with _write_lock:
        records = load_live_game_messages()
        pairs = records.pop(game_key, [])
        if [channel_id, message_id] not in pairs:
            pairs.append([channel_id, message_id])
        records[game_key] = pairs
        if len(records) > LIVE_GAME_MESSAGE_LIMIT:
            for stale in list(records)[: len(records) - LIVE_GAME_MESSAGE_LIMIT]:
                del records[stale]
        save_live_game_messages(records)


def pop_live_game_messages(game_keys: Iterable[str]) -> list[tuple[int, int]]:
    """Remove and return every ``(channel_id, message_id)`` recorded for ``game_keys``."""
    wanted = {str(key) for key in game_keys}
    if not wanted:
        return []
    with _write_lock:
        records = load_live_game_messages()
        popped: list[tuple[int, int]] = []
        changed = False
        for key in wanted:
            for channel_id, message_id in records.pop(key, []):
                popped.append((channel_id, message_id))
                changed = True
        if changed:
            save_live_game_messages(records)
        return popped


def load_tft_live_game_state() -> dict[str, str]:
    """Tracked Discord id -> the TFT lobby most recently announced for it."""
    raw = read_json(TFT_LIVE_GAME_STATE_PATH, {})
    if not isinstance(raw, dict):
        return {}
    return {
        str(discord_id): str(game_id) for discord_id, game_id in raw.items() if game_id
    }


def save_tft_live_game_state(state: dict[str, str]) -> None:
    """Atomically save TFT live-lobby deduplication state."""
    write_json(TFT_LIVE_GAME_STATE_PATH, state)


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

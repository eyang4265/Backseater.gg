"""Riot API client: rate limiting, bounded retries, caching, typed endpoints.

Replaces the three near-identical ``requests.get`` wrappers the previous
layout carried (``core.riot_get``, ``tracker._riot_get``,
``live_game._active_game``), each of which retried 429s in an *unbounded*
loop — a persistent 429 or a malformed ``Retry-After`` parked the polling
thread forever.

Three changes carry most of the throughput win:

* one pooled :class:`requests.Session` instead of a fresh connection (and TLS
  handshake) per call;
* short-TTL response caching, so the ten-player column builders stop
  re-resolving the same riot ids and league entries on every announcement;
* one league-entries request serving both Solo/Duo and Flex, instead of the
  two identical requests ``update_all_rank_snapshots`` used to issue per
  account.
"""

from __future__ import annotations

import logging
import random
import threading
import time
from collections import deque
from typing import Any, Callable, TypeVar
from urllib.parse import quote

import requests
from requests.adapters import HTTPAdapter

from .config import get_settings
from .match_cache import MatchCache, get_match_cache
from .routing import DEFAULT_PLATFORM, account_route, match_route

LOGGER = logging.getLogger(__name__)

T = TypeVar("T")


_NOT_FOUND = object()


class RiotAPIError(RuntimeError):
    """A Riot API request failed after exhausting its retries."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        """Initialize the instance."""
        super().__init__(message)
        self.status_code = status_code


class RateLimiter:
    """Sliding-window limiter enforcing several (requests, seconds) budgets."""

    def __init__(self, limits: tuple[tuple[int, float], ...]) -> None:
        """Initialize the instance."""
        self._limits = limits
        self._timestamps: list[deque[float]] = [deque() for _ in limits]
        self._lock = threading.Lock()

    def acquire(self) -> None:
        """Block until every budget has room, then record the request."""
        while True:
            with self._lock:
                now = time.monotonic()
                wait_for = 0.0

                for (max_requests, window), stamps in zip(
                    self._limits, self._timestamps
                ):
                    cutoff = now - window
                    while stamps and stamps[0] <= cutoff:
                        stamps.popleft()
                    if len(stamps) >= max_requests:
                        wait_for = max(wait_for, window - (now - stamps[0]))

                if wait_for <= 0:
                    for stamps in self._timestamps:
                        stamps.append(now)
                    return

            time.sleep(wait_for + 0.01)


class TTLCache:
    """Small thread-safe TTL cache.

    Sized rather than unbounded so a long-running process can't accumulate an
    entry per puuid it has ever seen.
    """

    def __init__(self, ttl_seconds: float, max_entries: int = 2048) -> None:
        """Initialize the instance."""
        self._ttl = ttl_seconds
        self._max_entries = max_entries
        self._entries: dict[Any, tuple[float, Any]] = {}
        self._lock = threading.Lock()

    def get_or_set(
        self, key: Any, produce: Callable[[], T], *, cache_none: bool = False
    ) -> T:
        """Return or set."""
        now = time.monotonic()
        with self._lock:
            hit = self._entries.get(key)
            if hit is not None and hit[0] > now:
                return hit[1]

        value = produce()

        if value is None and not cache_none:
            return value

        with self._lock:
            if len(self._entries) >= self._max_entries:
                self._evict_expired(now)
            self._entries[key] = (now + self._ttl, value)
        return value

    def _evict_expired(self, now: float) -> None:
        """Handle expired."""
        expired = [
            key for key, (deadline, _) in self._entries.items() if deadline <= now
        ]
        for key in expired:
            del self._entries[key]
        if len(self._entries) >= self._max_entries:
            for key in list(self._entries)[: self._max_entries // 4]:
                del self._entries[key]

    def invalidate(self, key: Any) -> None:
        """Handle invalidate."""
        with self._lock:
            self._entries.pop(key, None)

    def clear(self) -> None:
        """Handle clear."""
        with self._lock:
            self._entries.clear()


def _retry_after_seconds(response: requests.Response, default: float) -> float:
    """Handle after seconds."""
    raw = response.headers.get("Retry-After")
    if raw is None:
        return default
    try:
        return min(max(float(raw), 0.0), 120.0)
    except ValueError:
        return default


class RiotClient:
    """Thread-safe client for the Riot Games API."""

    def __init__(self, match_cache: MatchCache | None = None) -> None:
        """Initialize the instance."""
        settings = get_settings()
        self._api_key = settings.riot_api_key
        self._timeout = settings.request_timeout_seconds
        self._max_retries = settings.max_retries
        self._limiter = RateLimiter(settings.riot_rate_limits)
        self._match_cache = (
            match_cache or get_match_cache() if settings.match_cache_enabled else None
        )

        self._session = requests.Session()

        adapter = HTTPAdapter(pool_connections=8, pool_maxsize=16)
        self._session.mount("https://", adapter)
        self._session.headers.update(
            {"X-Riot-Token": self._api_key, "Accept": "application/json"}
        )

        self._riot_id_cache = TTLCache(ttl_seconds=6 * 3600)
        self._summoner_cache = TTLCache(ttl_seconds=600)
        self._league_cache = TTLCache(ttl_seconds=60)

    def _get(
        self,
        host: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        none_on_404: bool = False,
    ) -> Any:
        """GET a Riot endpoint, honouring rate limits and retrying transient failures.

        Raises :class:`RiotAPIError` once the retry budget is spent, rather
        than looping indefinitely.
        """
        url = f"https://{host}.api.riotgames.com{path}"
        last_error: str = "no attempt made"

        for attempt in range(self._max_retries):
            self._limiter.acquire()
            try:
                response = self._session.get(url, params=params, timeout=self._timeout)
            except requests.RequestException as error:
                last_error = str(error)
                LOGGER.debug("GET %s failed (attempt %d): %s", path, attempt + 1, error)
                time.sleep(self._backoff(attempt))
                continue

            if response.status_code == 404 and none_on_404:
                return _NOT_FOUND

            if response.status_code == 429:
                delay = _retry_after_seconds(response, self._backoff(attempt))
                LOGGER.info("Rate limited on %s; retrying in %.1fs", url, delay)
                time.sleep(delay)
                continue

            if response.status_code >= 500:
                last_error = f"HTTP {response.status_code}"
                time.sleep(self._backoff(attempt))
                continue

            if response.status_code >= 400:
                raise RiotAPIError(
                    f"GET {path} returned HTTP {response.status_code}",
                    status_code=response.status_code,
                )

            try:
                return response.json()
            except ValueError as error:
                raise RiotAPIError(
                    f"GET {path} returned malformed JSON: {error}"
                ) from error

        raise RiotAPIError(
            f"GET {path} failed after {self._max_retries} attempts: {last_error}"
        )

    @staticmethod
    def _backoff(attempt: int) -> float:
        """Exponential backoff with jitter, capped so a retry never stalls a poll."""
        return min(0.5 * (2**attempt), 8.0) * (0.75 + random.random() * 0.5)

    def riot_id(
        self, puuid: str, server: str = DEFAULT_PLATFORM, *, refresh: bool = False
    ) -> str | None:
        """``Name#Tag`` for a puuid. Cached — riot ids change rarely.

        ``refresh`` bypasses the cache, for the roster-refresh command whose
        entire job is to notice name changes.
        """
        if not puuid:
            return None
        if refresh:
            self._riot_id_cache.invalidate(("riot_id", puuid))

        def fetch() -> str | None:
            """Fetch one item for the enclosing operation."""
            route = account_route(server)
            if route is None:
                LOGGER.warning(
                    "Unknown platform %r; cannot resolve puuid %s", server, puuid
                )
                return None
            try:
                account = self._get(
                    route, f"/riot/account/v1/accounts/by-puuid/{puuid}"
                )
            except RiotAPIError as error:
                LOGGER.warning("Could not resolve puuid %s: %s", puuid, error)
                return None
            game_name = account.get("gameName")
            tag_line = account.get("tagLine")
            return f"{game_name}#{tag_line}" if game_name and tag_line else None

        return self._riot_id_cache.get_or_set(("riot_id", puuid), fetch)

    def puuid(self, summoner: str, tag: str, server: str) -> str | None:
        """Resolve a ``Name``/``Tag`` pair to a puuid."""
        route = account_route(server)
        if route is None:
            LOGGER.warning(
                "Unknown platform %r; cannot resolve %s#%s", server, summoner, tag
            )
            return None
        try:
            account = self._get(
                route,
                f"/riot/account/v1/accounts/by-riot-id/{quote(summoner)}/{quote(tag)}",
            )
        except RiotAPIError as error:
            LOGGER.warning(
                "Could not resolve %s#%s on %s: %s", summoner, tag, server, error
            )
            return None
        return account.get("puuid")

    def summoner(self, puuid: str, server: str) -> dict[str, Any] | None:
        """Summoner record (level, profile icon). Cached briefly."""
        if not puuid:
            return None

        def fetch() -> dict[str, Any] | None:
            """Fetch one item for the enclosing operation."""
            try:
                return self._get(server, f"/lol/summoner/v4/summoners/by-puuid/{puuid}")
            except RiotAPIError as error:
                LOGGER.warning("Could not fetch summoner %s: %s", puuid, error)
                return None

        return self._summoner_cache.get_or_set(("summoner", server, puuid), fetch)

    def summoner_level(self, puuid: str, server: str) -> int | None:
        """Handle level."""
        record = self.summoner(puuid, server)
        return record.get("summonerLevel") if record else None

    def profile_icon_id(self, puuid: str, server: str) -> int | None:
        """Handle icon id."""
        record = self.summoner(puuid, server)
        return record.get("profileIconId") if record else None

    def league_entries(self, puuid: str, server: str) -> list[dict[str, Any]] | None:
        """Every ranked entry for an account, or None if the fetch failed.

        Cached for a minute so building a ten-player rank column costs one
        request per player instead of one per player *per queue*.
        """
        if not puuid:
            return None

        def fetch() -> list[dict[str, Any]] | None:
            """Fetch one item for the enclosing operation."""
            try:
                return self._get(server, f"/lol/league/v4/entries/by-puuid/{puuid}")
            except RiotAPIError as error:
                LOGGER.warning(
                    "Could not fetch league entries for %s: %s", puuid, error
                )
                return None

        return self._league_cache.get_or_set(("league", server, puuid), fetch)

    def match_ids(self, puuid: str, server: str, *, count: int = 20) -> list[str]:
        """Handle ids."""
        route = match_route(server)
        if route is None:
            LOGGER.warning("Unknown platform %r; cannot list matches", server)
            return []
        return self._get(
            route,
            f"/lol/match/v5/matches/by-puuid/{puuid}/ids",
            params={"start": 0, "count": count},
        )

    def match(self, match_id: str, server: str) -> dict[str, Any]:
        """Handle match."""
        if self._match_cache is not None:
            cached = self._match_cache.get(match_id)
            if cached is not None:
                return cached
        route = match_route(server) or match_route(DEFAULT_PLATFORM)
        if route is None:
            raise RiotAPIError(f"No match-v5 route is configured for {server!r}.")
        payload = self._get(route, f"/lol/match/v5/matches/{match_id}")
        if self._match_cache is not None:
            self._match_cache.put(match_id, server, payload)
        return payload

    def match_timeline(self, match_id: str, server: str) -> dict[str, Any]:
        """Handle timeline."""
        route = match_route(server) or match_route(DEFAULT_PLATFORM)
        if route is None:
            raise RiotAPIError(f"No match-v5 route is configured for {server!r}.")
        return self._get(route, f"/lol/match/v5/matches/{match_id}/timeline")

    def match_replays(self, puuid: str, server: str) -> Any:
        """Return replay metadata for a player from Match-V5."""
        route = match_route(server) or match_route(DEFAULT_PLATFORM)
        if route is None:
            raise RiotAPIError(f"No match-v5 route is configured for {server!r}.")
        return self._get(route, f"/lol/match/v5/matches/by-puuid/{puuid}/replays")

    def active_game(self, puuid: str, server: str) -> dict[str, Any] | None:
        """The player's live game, or None when they aren't in one."""
        result = self._get(
            server,
            f"/lol/spectator/v5/active-games/by-summoner/{puuid}",
            none_on_404=True,
        )
        return None if result is _NOT_FOUND else result

    def champion_masteries(self, puuid: str, server: str) -> list[dict[str, Any]]:
        """Handle masteries."""
        return self._get(
            server, f"/lol/champion-mastery/v4/champion-masteries/by-puuid/{puuid}"
        )

    def top_champion_masteries(
        self, puuid: str, server: str, *, count: int = 3
    ) -> list[dict[str, Any]]:
        """Handle champion masteries."""
        return self._get(
            server,
            f"/lol/champion-mastery/v4/champion-masteries/by-puuid/{puuid}/top",
            params={"count": count},
        )

    def champion_mastery(
        self, puuid: str, server: str, champion_id: int
    ) -> dict[str, Any] | None:
        """Handle mastery."""
        result = self._get(
            server,
            f"/lol/champion-mastery/v4/champion-masteries/by-puuid/{puuid}/by-champion/{champion_id}",
            none_on_404=True,
        )
        return None if result is _NOT_FOUND else result

    def champion_rotation(self, server: str = DEFAULT_PLATFORM) -> list[int]:
        """Handle rotation."""
        payload = self._get(server, "/lol/platform/v3/champion-rotations")
        return payload.get("freeChampionIds", [])

    def platform_status(self, server: str) -> dict[str, Any]:
        """Handle status."""
        return self._get(server, "/lol/status/v4/platform-data")


_client: RiotClient | None = None
_client_lock = threading.Lock()


def get_client() -> RiotClient:
    """The process-wide client, created on first use."""
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                _client = RiotClient()
    return _client

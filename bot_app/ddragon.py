"""Data Dragon champion catalog and CDN asset URLs.

The previous conversion helpers (``champIdtoName``, ``champIdToInternalId``,
``nameToChampId``) each walked all ~170 champions on every call, and every
one of those calls went through ``getChampData()``. Rendering a ten-player
lobby did that twenty-plus times per embed. Here the payload is indexed once
per patch into dicts, so every lookup is O(1).
"""

from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass
from typing import Any, Iterable

import requests

LOGGER = logging.getLogger(__name__)

_VERSIONS_URL = "https://ddragon.leagueoflegends.com/api/versions.json"
_CDN = "https://ddragon.leagueoflegends.com/cdn"


_REFRESH_SECONDS = 6 * 3600
_FETCH_ATTEMPTS = 3
_TIMEOUT = 10.0


BLANK_PROFILE_ICON_ID = 29


_ALIASES: dict[str, str] = {
    "asol": "AurelionSol",
    "cait": "Caitlyn",
    "cass": "Cassiopeia",
    "cassio": "Cassiopeia",
    "cho": "Chogath",
    "ez": "Ezreal",
    "fiddle": "Fiddlesticks",
    "fiddles": "Fiddlesticks",
    "gp": "Gangplank",
    "hec": "Hecarim",
    "j4": "JarvanIV",
    "jarvan": "JarvanIV",
    "kass": "Kassadin",
    "kassa": "Kassadin",
    "kata": "Katarina",
    "kha": "Khazix",
    "kog": "KogMaw",
    "lb": "Leblanc",
    "lee": "LeeSin",
    "malz": "Malzahar",
    "mf": "MissFortune",
    "morde": "Mordekaiser",
    "mundo": "DrMundo",
    "naut": "Nautilus",
    "ori": "Orianna",
    "panth": "Pantheon",
    "rek": "RekSai",
    "seju": "Sejuani",
    "shyv": "Shyvana",
    "tahm": "TahmKench",
    "tf": "TwistedFate",
    "tryn": "Tryndamere",
    "trynda": "Tryndamere",
    "vel": "Velkoz",
    "vlad": "Vladimir",
    "voli": "Volibear",
    "wukong": "MonkeyKing",
    "ww": "Warwick",
    "xin": "XinZhao",
    "yas": "Yasuo",
    "yi": "MasterYi",
    "zil": "Zilean",
}


def normalize(text: str) -> str:
    """Fold user input so punctuation and spacing don't matter (``Vel'Koz`` → ``velkoz``)."""
    return re.sub(r"[^a-z0-9]", "", text.lower())


@dataclass(frozen=True)
class Champion:
    key: int

    internal_id: str

    name: str
    tags: tuple[str, ...]


class ChampionCatalog:
    """Indexed view over one patch's champion.json."""

    def __init__(self, version: str, champions: Iterable[Champion]) -> None:
        """Initialize the instance."""
        self.version = version
        self.champions: tuple[Champion, ...] = tuple(champions)
        self._by_key: dict[int, Champion] = {c.key: c for c in self.champions}
        self._by_internal_id: dict[str, Champion] = {
            c.internal_id: c for c in self.champions
        }

        self._by_query: dict[str, Champion] = {}
        for champion in self.champions:
            self._by_query.setdefault(normalize(champion.name), champion)
            self._by_query.setdefault(normalize(champion.internal_id), champion)
        for alias, internal_id in _ALIASES.items():
            aliased = self._by_internal_id.get(internal_id)
            if aliased is not None:
                self._by_query.setdefault(normalize(alias), aliased)

    def by_key(self, champion_key: int | str | None) -> Champion | None:
        """Handle key."""
        if champion_key is None:
            return None
        try:
            return self._by_key.get(int(champion_key))
        except (TypeError, ValueError):
            return None

    def by_internal_id(self, internal_id: str | None) -> Champion | None:
        """Handle internal id."""
        return self._by_internal_id.get(internal_id) if internal_id else None

    def by_query(self, text: str | None) -> Champion | None:
        """Resolve free-typed user input to a champion, or None."""
        if not text:
            return None
        return self._by_query.get(normalize(text))

    @property
    def tags_by_internal_id(self) -> dict[str, tuple[str, ...]]:
        """Handle by internal id."""
        return {c.internal_id: c.tags for c in self.champions}


class _CatalogLoader:
    """Loads and caches the catalog, serving stale data rather than failing."""

    def __init__(self) -> None:
        """Initialize the instance."""
        self._lock = threading.Lock()
        self._catalog: ChampionCatalog | None = None
        self._loaded_at = 0.0
        self._version: str | None = None
        self._version_checked_at = 0.0

    def version(self) -> str | None:
        """Current live patch version, re-checked at most once per refresh window."""
        now = time.monotonic()
        with self._lock:
            if self._version and now - self._version_checked_at < _REFRESH_SECONDS:
                return self._version

        version = self._fetch_version()

        with self._lock:
            if version:
                self._version = version
                self._version_checked_at = now
            return self._version

    def catalog(self) -> ChampionCatalog | None:
        """Handle catalog."""
        now = time.monotonic()
        with self._lock:
            fresh = (
                self._catalog is not None and now - self._loaded_at < _REFRESH_SECONDS
            )
            if fresh:
                return self._catalog

        version = self.version()
        if not version:
            return self._stale("could not determine the current patch version")

        payload = self._fetch_champions(version)
        if payload is None:
            return self._stale(f"could not fetch champion data for {version}")

        try:
            catalog = ChampionCatalog(
                version,
                (
                    Champion(
                        key=int(entry["key"]),
                        internal_id=entry["id"],
                        name=entry["name"],
                        tags=tuple(entry.get("tags", [])),
                    )
                    for entry in payload["data"].values()
                ),
            )
        except (KeyError, TypeError, ValueError) as error:
            return self._stale(f"champion data for {version} was malformed: {error}")

        with self._lock:
            self._catalog = catalog
            self._loaded_at = now
        return catalog

    def _stale(self, reason: str) -> ChampionCatalog | None:
        """Fall back to the last good catalog.

        A transient CDN failure used to return None here, which silently
        emptied the champion-tag table and broke lobby role ordering for the
        whole match.
        """
        with self._lock:
            if self._catalog is not None:
                LOGGER.warning(
                    "Data Dragon: %s; serving cached patch %s",
                    reason,
                    self._catalog.version,
                )
                return self._catalog
        LOGGER.error("Data Dragon: %s, and no cached copy is available", reason)
        return None

    @staticmethod
    def _fetch_version() -> str | None:
        """Fetch version."""
        try:
            response = requests.get(_VERSIONS_URL, timeout=_TIMEOUT)
            response.raise_for_status()
            return response.json()[0]
        except (
            requests.RequestException,
            IndexError,
            KeyError,
            TypeError,
            ValueError,
        ) as error:
            LOGGER.warning("Could not fetch the current League version: %s", error)
            return None

    @staticmethod
    def _fetch_champions(version: str) -> dict[str, Any] | None:
        """Fetch champions."""
        url = f"{_CDN}/{version}/data/en_US/champion.json"
        for attempt in range(_FETCH_ATTEMPTS):
            try:
                response = requests.get(url, timeout=_TIMEOUT)
                response.raise_for_status()
                return response.json()
            except (requests.RequestException, ValueError) as error:
                LOGGER.warning(
                    "champion.json attempt %d/%d failed: %s",
                    attempt + 1,
                    _FETCH_ATTEMPTS,
                    error,
                )
                time.sleep(0.5 * (attempt + 1))
        return None


_loader = _CatalogLoader()


_map_images: dict[str, bytes] = {}
_map_image_lock = threading.Lock()

_asset_names: dict[str, dict[str, str]] = {}
_asset_names_lock = threading.Lock()

# Per cache-key locks so concurrent misses on the same Data Dragon asset file
# coalesce into one fetch instead of each firing a redundant request.
_asset_names_fetch_locks: dict[str, threading.Lock] = {}
_asset_names_fetch_locks_guard = threading.Lock()


def _asset_names_fetch_lock(cache_key: str) -> threading.Lock:
    """Handle lock."""
    with _asset_names_fetch_locks_guard:
        lock = _asset_names_fetch_locks.get(cache_key)
        if lock is None:
            lock = threading.Lock()
            _asset_names_fetch_locks[cache_key] = lock
        return lock


def catalog() -> ChampionCatalog | None:
    """The champion catalog for the current patch, or None if it was never loadable."""
    return _loader.catalog()


def current_version() -> str | None:
    """Handle version."""
    return _loader.version()


def champion_name(
    champion_key: int | str | None, default: str | None = None
) -> str | None:
    """Display name for a numeric champion key (e.g. 36 → "Dr. Mundo")."""
    active = catalog()
    champion = active.by_key(champion_key) if active else None
    return champion.name if champion else default


def champion_internal_id(
    champion_key: int | str | None, default: str | None = None
) -> str | None:
    """Data Dragon internal id for a numeric key (e.g. 36 → "DrMundo")."""
    active = catalog()
    champion = active.by_key(champion_key) if active else None
    return champion.internal_id if champion else default


def champion_key_for_name(text: str | None) -> int | None:
    """Numeric key for user-typed input, tolerating punctuation and nicknames."""
    active = catalog()
    champion = active.by_query(text) if active else None
    return champion.key if champion else None


def champion_tags_by_internal_id() -> dict[str, tuple[str, ...]]:
    """Handle tags by internal id."""
    active = catalog()
    return active.tags_by_internal_id if active else {}


def map_image(map_id: int | None, version: str | None = None) -> bytes | None:
    """Minimap art for a map id (11 = Summoner's Rift), as PNG bytes.

    Cached for the lifetime of the process, keyed by patch: the art is a few
    hundred kilobytes and every kill map would otherwise re-download it.
    """
    if map_id is None:
        return None
    version = version or current_version()
    if not version:
        return None

    key = f"{version}/{map_id}"
    with _map_image_lock:
        if key in _map_images:
            return _map_images[key]

    url = f"{_CDN}/{version}/img/map/map{map_id}.png"
    try:
        response = requests.get(url, timeout=_TIMEOUT)
        response.raise_for_status()
    except requests.RequestException as error:
        LOGGER.warning("Could not fetch map art %s: %s", url, error)
        return None

    with _map_image_lock:
        _map_images[key] = response.content
    return response.content


def profile_icon_url(icon_id: int | None, version: str | None = None) -> str | None:
    """CDN URL for a profile icon, falling back to the blank League avatar."""
    version = version or current_version()
    if not version:
        return None
    return f"{_CDN}/{version}/img/profileicon/{icon_id if icon_id is not None else BLANK_PROFILE_ICON_ID}.png"


def _asset_name_map(cache_key: str, url: str, builder: Any) -> dict[str, str]:
    """Fetch and cache one Data Dragon id-to-name table, keyed by patch version."""
    with _asset_names_lock:
        cached = _asset_names.get(cache_key)
    if cached is not None:
        return cached
    with _asset_names_fetch_lock(cache_key):
        with _asset_names_lock:
            cached = _asset_names.get(cache_key)
        if cached is not None:
            return cached
        try:
            response = requests.get(url, timeout=_TIMEOUT)
            response.raise_for_status()
            mapping = builder(response.json())
        except (requests.RequestException, ValueError, KeyError, TypeError) as error:
            LOGGER.warning("Could not fetch Data Dragon asset names from %s: %s", url, error)
            return {}
        with _asset_names_lock:
            _asset_names[cache_key] = mapping
        return mapping


def item_name(item_id: int | str | None) -> str | None:
    """Display name for a Data Dragon item id (e.g. 3020 → "Sorcerer's Shoes")."""
    version = current_version()
    if item_id is None or not version:
        return None
    names = _asset_name_map(
        f"items:{version}",
        f"{_CDN}/{version}/data/en_US/item.json",
        lambda payload: {
            item_key: entry.get("name", item_key) for item_key, entry in payload["data"].items()
        },
    )
    return names.get(str(item_id))


def rune_name(rune_id: int | str | None) -> str | None:
    """Display name for a Data Dragon rune id (e.g. 8112 → "Electrocute")."""
    version = current_version()
    if rune_id is None or not version:
        return None

    def build(payload: Any) -> dict[str, str]:
        names: dict[str, str] = {}
        for tree in payload:
            for slot in tree.get("slots", []):
                for rune in slot.get("runes", []):
                    names[str(rune["id"])] = rune["name"]
        return names

    names = _asset_name_map(f"runes:{version}", f"{_CDN}/{version}/data/en_US/runesReforged.json", build)
    return names.get(str(rune_id))


def summoner_spell_name(spell_id: int | str | None) -> str | None:
    """Display name for a numeric summoner-spell key (e.g. 4 → "Flash")."""
    version = current_version()
    if spell_id is None or not version:
        return None
    names = _asset_name_map(
        f"summoners:{version}",
        f"{_CDN}/{version}/data/en_US/summoner.json",
        lambda payload: {
            entry["key"]: entry["name"] for entry in payload["data"].values() if "key" in entry
        },
    )
    return names.get(str(spell_id))

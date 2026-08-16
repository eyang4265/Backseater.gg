"""Coachless.gg build statistics via its JSON API, with cached per-champion/role results."""
from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import requests

from . import ddragon

LOGGER = logging.getLogger(__name__)
_API = "https://api.coachless.gg/api"
_CACHE_SECONDS = 300.0
_PATCH_CACHE_SECONDS = 3600.0
_TIMEOUT = 10.0
_LEAGUE_TIERS = (5, 6, 7)  # Emerald, Diamond, Master+ — Coachless's own free-tier default.
_ROLES = {"top": 0, "jungle": 1, "mid": 2, "adc": 3, "support": 4}
_ITEM_STAGES = {"starter": (6, None), "boots": (2, None), "1st": (1, [1]), "2nd": (1, [2]), "3rd": (1, [3]), "4th+": (1, [4, 5, 6])}
_STAGES = {"runes", "summs", *_ITEM_STAGES}


class CoachlessError(RuntimeError):
    """Coachless.gg could not supply usable build statistics."""


@dataclass(frozen=True)
class CoachlessEntry:
    identifier: str
    name: str
    wpa: float
    buys: int


_cache: dict[tuple[int, str], tuple[float, dict[str, tuple[CoachlessEntry, ...]]]] = {}
_cache_lock = threading.Lock()
_patch_cache: tuple[float, dict[str, int]] | None = None
_patch_lock = threading.Lock()
_session = requests.Session()

# Per (champion_id, role) locks so concurrent cache misses on the same key
# coalesce into one 8-request fetch instead of each firing its own.
_fetch_locks: dict[tuple[int, str], threading.Lock] = {}
_fetch_locks_guard = threading.Lock()


def _fetch_lock(key: tuple[int, str]) -> threading.Lock:
    """Handle lock."""
    with _fetch_locks_guard:
        lock = _fetch_locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _fetch_locks[key] = lock
        return lock


def _current_patch() -> dict[str, int]:
    """The patch Coachless itself defaults to for free/anonymous users.

    Coachless gates its single newest patch behind a paywall (visible in its
    own Filters panel as a locked slider step); free users are always pinned
    one patch behind that. This is not about sample size — a patch's match
    count keeps climbing after it stops being newest, so picking by match
    count drifts to whichever old patch has accumulated the most games.
    """
    global _patch_cache
    now = time.monotonic()
    with _patch_lock:
        if _patch_cache and now - _patch_cache[0] < _PATCH_CACHE_SECONDS:
            return _patch_cache[1]
    try:
        response = _session.get(f"{_API}/ChampionWinprob/GetPatches", timeout=_TIMEOUT)
        response.raise_for_status()
        patches = response.json()
    except (requests.RequestException, ValueError) as error:
        LOGGER.error("Coachless patch lookup failed: %s", error)
        raise CoachlessError("Coachless did not return build data") from None
    if len(patches) < 2:
        raise CoachlessError("Coachless did not return build data")
    best = patches[-2]
    patch = {"major": best["major"], "patch": best["patch"], "patchAdditions": 0}
    with _patch_lock:
        _patch_cache = (now, patch)
    return patch


def _common_filters(champion_id: int, role: str) -> dict[str, object]:
    return {
        "patch": _current_patch(),
        "championIds": [champion_id],
        "matchupChampionIds": None,
        "leagueTiers": list(_LEAGUE_TIERS),
        "regions": None,
        "role": _ROLES[role],
    }


def _post(endpoint: str, body: dict[str, object]) -> list[dict[str, object]]:
    try:
        response = _session.post(f"{_API}/{endpoint}", json=body, timeout=_TIMEOUT)
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as error:
        LOGGER.error("Coachless request failed: endpoint=%s error=%s", endpoint, error)
        raise CoachlessError("Coachless page rendering failed") from None
    if not isinstance(payload, list):
        LOGGER.error("Coachless request returned unexpected payload: endpoint=%s", endpoint)
        raise CoachlessError("Coachless did not return build data")
    return payload


def _entries(rows: list[dict[str, object]], id_field: str, name_lookup) -> tuple[CoachlessEntry, ...]:
    entries = []
    for row in rows:
        identifier = row.get(id_field)
        if identifier is None:
            continue
        name = name_lookup(identifier) or str(identifier)
        entries.append(CoachlessEntry(str(identifier), name, float(row.get("wpaOverall", 0.0)), round(float(row.get("occurrence", 0)))))
    return tuple(sorted(entries, key=lambda entry: entry.wpa, reverse=True))


def _item_body(filters: dict[str, object], stage: str, item_type: int, item_slots: list[int] | None) -> dict[str, object]:
    return {
        "commonFilters": filters,
        "itemSlots": item_slots,
        "itemType": item_type,
        "keystone": None,
        "starterId": None,
        "firstPurchaseId": None,
        "firstLegendaryId": None,
        "secondLegendaryId": None,
        "loadFirstEpicPurchase": False,
        "includeSupportItems": stage == "1st",
    }


def _fetch_stages(champion_id: int, role: str) -> dict[str, tuple[CoachlessEntry, ...]]:
    """Fetch every build stage for one champion/role. Runs the 8 independent API
    calls concurrently — sequential requests otherwise cost ~1.2s each, ~10s total."""
    filters = _common_filters(champion_id, role)
    jobs: dict[str, tuple[str, dict[str, object]]] = {
        "runes": ("Rune/GetKeystoneData", {"commonFilters": filters}),
        "summs": ("ChampionWinprob/GetGlobalSummonerSpellStatistics", {"commonFilters": filters, "pairedSpell": None}),
    }
    for stage, (item_type, item_slots) in _ITEM_STAGES.items():
        jobs[stage] = ("ChampionWinprob/GetGlobalItemStatistics", _item_body(filters, stage, item_type, item_slots))

    with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        raw = dict(zip(jobs, pool.map(lambda job: _post(*job), jobs.values())))

    output = {
        "runes": _entries(raw["runes"], "rune", ddragon.rune_name),
        "summs": _entries(raw["summs"], "summonerSpell", ddragon.summoner_spell_name),
    }
    for stage in _ITEM_STAGES:
        output[stage] = _entries(raw[stage], "itemId", ddragon.item_name)
    if not any(output.values()):
        LOGGER.error("Coachless parser: no data champion_id=%s role=%s", champion_id, role)
        raise CoachlessError("Coachless did not return build data")
    return output


def fetch_build_stage(champion_id: int, role: str, stage: str, *, champion_slug: str | None = None) -> tuple[CoachlessEntry, ...]:
    if role not in _ROLES or stage not in _STAGES:
        raise CoachlessError("Unsupported Coachless build stage or role")
    key, now = (champion_id, role), time.monotonic()
    with _cache_lock:
        cached = _cache.get(key)
        if cached and now - cached[0] < _CACHE_SECONDS:
            LOGGER.info("Coachless cache: hit champion_id=%s role=%s stage=%s age=%.1fs", champion_id, role, stage, now - cached[0])
            return cached[1].get(stage, ())
    with _fetch_lock(key):
        now = time.monotonic()
        with _cache_lock:
            cached = _cache.get(key)
            if cached and now - cached[0] < _CACHE_SECONDS:
                LOGGER.info("Coachless cache: hit champion_id=%s role=%s stage=%s age=%.1fs", champion_id, role, stage, now - cached[0])
                return cached[1].get(stage, ())
        LOGGER.info("Coachless cache: miss champion_id=%s role=%s stage=%s", champion_id, role, stage)
        result = _fetch_stages(champion_id, role)
        with _cache_lock:
            _cache[key] = (now, result)
        return result.get(stage, ())

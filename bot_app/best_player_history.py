"""Fetch tracked League accounts' Riot history from the last 180 days."""

import logging
import time
from dataclasses import dataclass

from .best_player import aggregate_best_players
from .match_cache import get_match_cache
from .riot import RiotAPIError, get_client
from .store import load_accounts

LOGGER = logging.getLogger(__name__)
PAGE_SIZE = 100
HISTORY_DAYS = 180


@dataclass
class BestPlayerHistory:
    """Ranking and coverage counters; partial histories must remain visible."""

    rows: list
    names: dict
    accounts: int
    incomplete_accounts: int = 0
    unavailable_matches: int = 0
    matches: int = 0


def fetch_best_players(min_games=10, queue="All Games", *, progress=None):
    """Stream cached and paginated Riot matches from a fixed 180-day window.

    RiotClient owns transport, rate limits, retries, and cache writes. A fixed
    end time prevents newly completed matches shifting pagination. Shared match
    IDs and duplicate account identities are processed once. Failures retain
    cached results and disclose incomplete coverage; API history is not lifetime
    history. Match start timestamps enforce the same inclusive window on both
    cached and fetched payloads. Undated matches are excluded.
    """
    accounts = {account.puuid: account for account in load_accounts().values()}
    result = BestPlayerHistory([], {p: a.riot_id for p, a in accounts.items()}, len(accounts))
    if not accounts:
        return result
    client = get_client()
    cache = get_match_cache()
    seen = set()
    unavailable = set()
    incomplete = set()
    end_time = int(time.time())
    start_time = end_time - HISTORY_DAYS * 86400

    def in_window(payload):
        info = payload.get("info", {})
        started = info.get("gameStartTimestamp") or info.get("gameCreation")
        return isinstance(started, (int, float)) and start_time * 1000 <= started <= end_time * 1000

    def report(done):
        if progress:
            progress(done, len(accounts), result.matches)

    def payloads():
        for payload in cache.iter_match_payloads():
            if not in_window(payload):
                continue
            match_id = payload.get("metadata", {}).get("matchId")
            if match_id and match_id not in seen:
                seen.add(match_id)
                result.matches += 1
                yield payload
        for done, account in enumerate(accounts.values(), start=1):
            start = 0
            listed = set()
            report(done - 1)
            while True:
                try:
                    page = client.match_ids(account.puuid, account.server,
                        start=start, count=PAGE_SIZE, start_time=start_time, end_time=end_time)
                except RiotAPIError:
                    LOGGER.warning("Bestplayer history failed for %s", account.riot_id, exc_info=True)
                    incomplete.add(account.puuid)
                    break
                if not page:
                    break
                if not set(page) - listed:
                    LOGGER.warning("Repeated bestplayer history page for %s", account.riot_id)
                    incomplete.add(account.puuid)
                    break
                listed.update(page)
                for match_id in page:
                    if match_id in unavailable:
                        incomplete.add(account.puuid)
                        continue
                    if match_id in seen:
                        continue
                    seen.add(match_id)
                    try:
                        payload = client.match(match_id, account.server)
                    except RiotAPIError:
                        LOGGER.warning("Bestplayer match unavailable: %s", match_id, exc_info=True)
                        unavailable.add(match_id)
                        incomplete.add(account.puuid)
                        continue
                    if not in_window(payload):
                        continue
                    result.matches += 1
                    yield payload
                    if result.matches % 25 == 0:
                        report(done - 1)
                report(done - 1)
                start += len(page)
                if len(page) < PAGE_SIZE:
                    break
            report(done)

    result.rows = aggregate_best_players(payloads(), accounts, min_games, queue)
    result.incomplete_accounts = len(incomplete)
    result.unavailable_matches = len(unavailable)
    return result

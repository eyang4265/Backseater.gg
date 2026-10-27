"""Refresh partners only for current live teams with qualifying prior history."""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from dataclasses import dataclass
from typing import Callable

from .premades import eligible_participants, participant_riot_id, qualifying_partymate_puuids, remember_game_partymates
from .queues import ARAM_MAYHEM_QUEUE_ID
from .riot import RiotAPIError, get_client
from .store import Account, league_state_transaction, load_accounts, load_partymates, remember_partymates

LOGGER = logging.getLogger(__name__)
PAGE_SIZE = 100
WEEK_SECONDS = 7 * 86400


@dataclass(frozen=True)
class PartymateUpdateResult:
    """Counts for the private owner report, including incomplete coverage."""

    accounts: int
    incomplete_accounts: int
    matches: int
    unavailable_matches: int
    added: int
    total: int


def update_partymates(*, now: int | None = None, progress: Callable | None = None) -> PartymateUpdateResult:
    """Fetch all tracked League histories in a fixed seven-day window and merge.

    Paginate time-bounded Match-V5 IDs through RiotClient, which owns caching,
    retries, and rate limits. Shared matches are fetched once. Complete tracked
    histories anchor candidate pairs; incomplete accounts cannot independently
    introduce partners. A current same-team live lobby and two prior completed
    same-queue/team games must meet the shared frequency rule using each participant's last ten observed non-remake games. No partner
    histories are fetched separately, so untracked-player evidence remains partial.
    Existing entries survive; the storage boundary excludes all registered IDs.
    """
    previous = set(load_partymates())
    before = int(time.time()) if now is None else now
    after = before - WEEK_SECONDS
    accounts = {a.puuid: a for a in load_accounts().values()}
    matches = {}
    unavailable = set()
    incomplete = set()
    client = get_client() if accounts else None

    for done, account in enumerate(accounts.values(), start=1):
        start = 0
        seen = set()
        while True:
            try:
                page = client.match_ids(
                    account.puuid, account.server, start=start, count=PAGE_SIZE,
                    start_time=after, end_time=before,
                )
            except RiotAPIError:
                LOGGER.warning("Partymate history failed for %s", account.riot_id, exc_info=True)
                incomplete.add(account.puuid)
                break
            if not page:
                break
            if not set(page) - seen:
                LOGGER.warning("Repeated partymate history page for %s", account.riot_id)
                incomplete.add(account.puuid)
                break
            seen.update(page)
            for match_id in page:
                if match_id in unavailable:
                    incomplete.add(account.puuid)
                    continue
                if match_id in matches:
                    continue
                try:
                    matches[match_id] = client.match(match_id, account.server)
                except RiotAPIError:
                    LOGGER.warning("Partymate scan could not fetch %s", match_id, exc_info=True)
                    unavailable.add(match_id)
                    incomplete.add(account.puuid)
            if progress:
                progress(done - 1, len(accounts), len(matches))
            start += len(page)
            if len(page) < PAGE_SIZE:
                break
        if progress:
            progress(done, len(accounts), len(matches))

    # Completed history supplies evidence; only a current live roster admits pairs.
    histories = defaultdict(list)
    for match_id, payload in matches.items():
        info = payload.get("info", {})
        start_ms, end_ms = info.get("gameStartTimestamp"), info.get("gameEndTimestamp")
        if not isinstance(start_ms, (int, float)) or not isinstance(end_ms, (int, float)):
            continue
        if not after * 1000 <= start_ms < end_ms <= before * 1000:
            continue
        if info.get("resultUnavailable") or any(
            p.get("gameEndedInEarlySurrender") for p in info.get("participants", []) or []
        ):
            continue
        queue = info.get("queueId")
        if queue == ARAM_MAYHEM_QUEUE_ID:
            continue
        participants = eligible_participants(info, queue)
        if not participants:
            continue
        for puuid, participant in participants.items():
            histories[puuid, queue].append((end_ms, match_id, participant["teamId"]))
    candidates = {}
    anchors = set(accounts) - incomplete
    for puuid in sorted(anchors):
        account = accounts[puuid]
        try:
            live = client.active_game(puuid, account.server)
        except RiotAPIError:
            LOGGER.warning("Partymate live lookup failed for %s", account.riot_id, exc_info=True)
            incomplete.add(puuid)
            continue
        if not live:
            continue
        start_ms = live.get("gameStartTime")
        queue = live.get("gameQueueConfigId")
        participants = eligible_participants(live, queue)
        if not isinstance(start_ms, (int, float)) or start_ms <= 0 or puuid not in participants:
            continue
        if queue == ARAM_MAYHEM_QUEUE_ID:
            remember_game_partymates(dict(live, platformId=live.get("platformId") or account.server), live=True)
            continue
        recent = {
            p: {mid: team for end, mid, team in sorted(histories[p, queue], reverse=True)
                if start_ms - WEEK_SECONDS * 1000 <= end < start_ms}
            for p in participants
        }
        recent = {p: dict(list(rows.items())[:10]) for p, rows in recent.items()}
        detected = qualifying_partymate_puuids(participants, recent, tracked={puuid})
        platform = live.get("platformId") or account.server
        for candidate in detected:
            previous_identity = candidates.get(candidate)
            riot_id = participant_riot_id(participants[candidate]) or (
                previous_identity.riot_id if previous_identity else ""
            )
            candidates[candidate] = Account("", candidate, str(platform).upper(), riot_id)
    with league_state_transaction():
        merged = remember_partymates(candidates.values())
        added = len(set(merged) - previous)
    return PartymateUpdateResult(len(accounts), len(incomplete), len(matches), len(unavailable), added, len(merged))

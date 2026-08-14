"""Background ranked-match polling for every tracked account."""

from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

from .announce import (
    LiveGameAnnouncement,
    MatchAnnouncement,
    TrackedPlayer,
    format_live_game,
    format_match,
    publish,
    publish_live_games,
)
from .queues import RANKED_QUEUE_IDS
from .ranks import RankSnapshot, fetch_ranks, lp_change
from .riot import RiotAPIError, get_client
from .store import (
    Account,
    PlayerState,
    load_accounts,
    load_live_game_state,
    load_tracker_state,
    save_live_game_state,
    save_tracker_state,
)

LOGGER = logging.getLogger(__name__)

#: Recent matches to examine per account per poll.
MATCH_LOOKBACK = 20

#: Accounts and matches fetched at once. Requests are rate-limited centrally,
#: so this bounds thread count rather than request rate.
_FETCH_WORKERS = 8


@dataclass
class _AccountPoll:
    account: Account
    state: PlayerState
    new_match_ids: list[str] = field(default_factory=list)


def update_all_rank_snapshots() -> tuple[int, int]:
    """Refresh every tracked account's stored ranks.

    Run at startup to establish the baseline that LP changes are measured
    against. An account counts as failed only when its ranks couldn't be
    fetched at all — being unranked in a queue is fine.

    Both queues come from one request per account; the previous version issued
    two identical requests, one per queue.
    """
    accounts = load_accounts()
    state = load_tracker_state()
    updated = 0
    failed = 0

    def fetch(account: Account) -> tuple[Account, dict[int, RankSnapshot] | None]:
        return account, fetch_ranks(account.puuid, account.server)

    with ThreadPoolExecutor(max_workers=min(_FETCH_WORKERS, max(len(accounts), 1))) as pool:
        for account, ranks in pool.map(fetch, accounts.values()):
            if ranks is None:
                failed += 1
                LOGGER.warning("Could not fetch ranks for %s", account.riot_id)
                continue
            entry = state.setdefault(account.discord_id, PlayerState())
            entry.ranks.update(ranks)
            updated += 1

    save_tracker_state(state)
    return updated, failed


def _poll_accounts(accounts: dict[str, Account], state: dict[str, PlayerState]) -> list[_AccountPoll]:
    """List each account's unseen match ids, oldest first."""
    client = get_client()

    def fetch(account: Account) -> _AccountPoll:
        entry = state.setdefault(account.discord_id, PlayerState())
        poll = _AccountPoll(account=account, state=entry)
        try:
            match_ids = client.match_ids(account.puuid, account.server, count=MATCH_LOOKBACK)
        except RiotAPIError as error:
            LOGGER.warning("Could not fetch match ids for %s: %s", account.riot_id, error)
            return poll
        known = set(entry.matches)
        poll.new_match_ids = [
            match_id for match_id in reversed(match_ids) if match_id not in known
        ]
        return poll

    if not accounts:
        return []
    with ThreadPoolExecutor(max_workers=min(_FETCH_WORKERS, len(accounts))) as pool:
        return list(pool.map(fetch, accounts.values()))


def _fetch_matches(polls: list[_AccountPoll]) -> dict[str, dict[str, Any]]:
    """Fetch each newly-seen match exactly once, even when several accounts shared it."""
    servers: dict[str, str] = {}
    for poll in polls:
        for match_id in poll.new_match_ids:
            servers.setdefault(match_id, poll.account.server)

    if not servers:
        return {}

    def fetch(item: tuple[str, str]) -> tuple[str, dict[str, Any] | None]:
        match_id, server = item
        try:
            return match_id, get_client().match(match_id, server)
        except RiotAPIError as error:
            LOGGER.warning("Could not fetch match %s: %s", match_id, error)
            return match_id, None

    with ThreadPoolExecutor(max_workers=min(_FETCH_WORKERS, len(servers))) as pool:
        results = pool.map(fetch, servers.items())
        return {match_id: match for match_id, match in results if match is not None}


def _ranked_matches_per_player(
    match_order: list[str],
    matches: dict[str, dict[str, Any]],
    participants: dict[str, list[_AccountPoll]],
) -> dict[tuple[str, int], list[str]]:
    """Each (player, ranked queue) pair's new match ids this poll, in order.

    With exactly one new game in a queue the LP swing is unambiguous. With
    more than one there's no way to attribute LP to any single game, so those
    only resync the stored baseline.
    """
    grouped: dict[tuple[str, int], list[str]] = {}
    for match_id in match_order:
        queue_id = matches[match_id].get("info", {}).get("queueId")
        if queue_id not in RANKED_QUEUE_IDS:
            continue
        for poll in participants[match_id]:
            grouped.setdefault((poll.account.discord_id, queue_id), []).append(match_id)
    return grouped


def collect_new_matches() -> list[MatchAnnouncement]:
    """Collect announcements for matches not yet seen. Blocking; call in a thread.

    Each player's stored rank for the match's queue is the pre-match baseline.
    When a ranked match is announced, the current rank is fetched, the swing is
    reported, and the new rank becomes the next baseline — committed only if
    the match actually produced an announcement, so the baseline always
    corresponds to the last announced game.
    """
    accounts = load_accounts()
    state = load_tracker_state()

    polls = _poll_accounts(accounts, state)
    matches = _fetch_matches(polls)

    participants: dict[str, list[_AccountPoll]] = {}
    match_order: list[str] = []
    for poll in polls:
        for match_id in poll.new_match_ids:
            if match_id not in matches:
                continue
            if match_id not in participants:
                participants[match_id] = []
                match_order.append(match_id)
            participants[match_id].append(poll)

    # Announce chronologically.
    match_order.sort(key=lambda mid: matches[mid].get("info", {}).get("gameEndTimestamp", 0))
    ranked_by_player = _ranked_matches_per_player(match_order, matches, participants)

    announcements: list[MatchAnnouncement] = []
    dirty = False

    for match_id in match_order:
        match = matches[match_id]
        queue_id = match.get("info", {}).get("queueId")
        players: list[TrackedPlayer] = []
        pending_ranks: list[tuple[PlayerState, int, RankSnapshot]] = []

        for poll in participants[match_id]:
            account = poll.account
            if queue_id not in RANKED_QUEUE_IDS:
                players.append(
                    TrackedPlayer(
                        puuid=account.puuid,
                        riot_id=account.riot_id,
                        server=account.server,
                    )
                )
                continue

            in_queue = ranked_by_player.get((account.discord_id, queue_id), [])
            is_only_game = len(in_queue) == 1
            is_last_game = bool(in_queue) and in_queue[-1] == match_id

            if not is_only_game and not is_last_game:
                continue

            current = (fetch_ranks(account.puuid, account.server) or {}).get(queue_id)
            if current is not None:
                pending_ranks.append((poll.state, queue_id, current))

            if not is_only_game:
                # Several new games in this queue: resync the baseline only.
                continue

            players.append(
                TrackedPlayer(
                    puuid=account.puuid,
                    riot_id=account.riot_id,
                    server=account.server,
                    lp_change=lp_change(poll.state.ranks.get(queue_id), current),
                    rank=current,
                )
            )

        announcement = format_match(match, players)
        if announcement is None:
            continue

        announcements.append(announcement)
        for player_state, ranked_queue, snapshot in pending_ranks:
            player_state.ranks[ranked_queue] = snapshot
            dirty = True

    # Only matches whose details actually arrived are marked as seen. Marking
    # a failed fetch as seen would drop that game permanently; leaving it
    # unmarked lets the next poll retry it, bounded by the lookback window.
    for poll in polls:
        fetched = [match_id for match_id in poll.new_match_ids if match_id in matches]
        if fetched:
            poll.state.remember(fetched)
            dirty = True

    if dirty:
        save_tracker_state(state)

    return announcements


async def poll_and_announce(bot: Any) -> None:
    """Background loop entry point."""
    await publish(bot, await asyncio.to_thread(collect_new_matches))


def collect_new_live_games() -> list[LiveGameAnnouncement]:
    """Find games that tracked accounts entered since the previous poll."""
    accounts = load_accounts()
    previous = load_live_game_state()
    current: dict[str, str] = {}
    grouped: dict[str, tuple[dict[str, Any], list[TrackedPlayer]]] = {}

    def fetch(account: Account) -> tuple[Account, dict[str, Any] | None]:
        try:
            return account, get_client().active_game(account.puuid, account.server)
        except RiotAPIError as error:
            LOGGER.warning("Could not fetch active game for %s: %s", account.riot_id, error)
            return account, None

    if not accounts:
        return []
    with ThreadPoolExecutor(max_workers=min(_FETCH_WORKERS, len(accounts))) as pool:
        games = pool.map(fetch, accounts.values())
        for account, game in games:
            if not game:
                continue
            game_id = game.get("gameId")
            if game_id is None:
                LOGGER.warning("Active game for %s had no gameId", account.riot_id)
                continue
            key = f"{account.server}:{game_id}"
            current[account.discord_id] = key
            if previous.get(account.discord_id) == key:
                continue
            if key not in grouped:
                grouped[key] = (game, [])
            grouped[key][1].append(
                TrackedPlayer(
                    puuid=account.puuid,
                    riot_id=account.riot_id,
                    server=account.server,
                )
            )

    if current != previous:
        save_live_game_state(current)

    return [
        announcement
        for game, players in grouped.values()
        if (announcement := format_live_game(game, players)) is not None
    ]


async def poll_live_games_and_announce(bot: Any) -> None:
    """Background loop entry point for first-seen live-game announcements."""
    await publish_live_games(bot, await asyncio.to_thread(collect_new_live_games))

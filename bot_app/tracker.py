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
    resolve_announcement_channel,
)
from .guest_policy import GUEST_PUUID, guest_is_in_game_with_crispy
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


MATCH_LOOKBACK = 20


_FETCH_WORKERS = 8


@dataclass
class _AccountPoll:
    account: Account
    state: PlayerState
    new_match_ids: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class _PendingRank:
    poll: _AccountPoll
    queue_id: int
    snapshot: RankSnapshot
    participant: dict[str, Any]
    attributable: bool


def fetch_all_rank_snapshots() -> tuple[dict[str, dict[int, RankSnapshot]], int]:
    """Fetch current ranks without mutating LP-attribution baselines."""
    accounts = load_accounts()
    snapshots: dict[str, dict[int, RankSnapshot]] = {}
    failed = 0

    def fetch(account: Account) -> tuple[Account, dict[int, RankSnapshot] | None]:
        """Fetch one item for the enclosing operation."""
        return account, fetch_ranks(account.puuid, account.server)

    with ThreadPoolExecutor(
        max_workers=min(_FETCH_WORKERS, max(len(accounts), 1))
    ) as pool:
        for account, ranks in pool.map(fetch, accounts.values()):
            if ranks is None:
                failed += 1
                LOGGER.warning("Could not fetch ranks for %s", account.riot_id)
                continue
            snapshots[account.discord_id] = ranks
    return snapshots, failed


def update_all_rank_snapshots() -> tuple[int, int]:
    """Fill missing rank baselines for every tracked account.

    Run at startup to establish baselines for new state. Once a baseline
    exists, only :meth:`PlayerState.record_rank` may move it; overwriting it
    here could erase an unprocessed game's LP change.

    Both queues come from one request per account; the previous version issued
    two identical requests, one per queue.
    """
    state = load_tracker_state()
    snapshots, failed = fetch_all_rank_snapshots()
    dirty = False
    for discord_id, ranks in snapshots.items():
        entry = state.setdefault(discord_id, PlayerState())
        for queue_id, snapshot in ranks.items():
            if entry.ranks.get(queue_id) is None:
                entry.ranks[queue_id] = snapshot
                dirty = True
    if dirty:
        save_tracker_state(state)
    return len(snapshots), failed


def _poll_accounts(
    accounts: dict[str, Account], state: dict[str, PlayerState]
) -> list[_AccountPoll]:
    """List each account's unseen match ids, oldest first."""
    client = get_client()

    def fetch(account: Account) -> _AccountPoll:
        """Fetch one item for the enclosing operation."""
        entry = state.setdefault(account.discord_id, PlayerState())
        poll = _AccountPoll(account=account, state=entry)
        try:
            match_ids = client.match_ids(
                account.puuid, account.server, count=MATCH_LOOKBACK
            )
        except RiotAPIError as error:
            LOGGER.warning(
                "Could not fetch match ids for %s: %s", account.riot_id, error
            )
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
        """Fetch one item for the enclosing operation."""
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
    reported, and the new rank becomes the next baseline. Multi-game batches
    are still announced, but receive one unattributed resync point because an
    individual LP change cannot be assigned safely.
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

    match_order.sort(
        key=lambda mid: matches[mid].get("info", {}).get("gameEndTimestamp", 0)
    )
    ranked_by_player = _ranked_matches_per_player(match_order, matches, participants)

    announcements: list[MatchAnnouncement] = []
    announced_ids: set[str] = set()
    silently_processed: set[tuple[str, str]] = set()
    dirty = False

    for match_id in match_order:
        match = matches[match_id]
        queue_id = match.get("info", {}).get("queueId")
        players: list[TrackedPlayer] = []
        pending_ranks: list[_PendingRank] = []
        match_participants = match.get("info", {}).get("participants", []) or []
        guest_can_be_announced = guest_is_in_game_with_crispy(match_participants)

        for poll in participants[match_id]:
            account = poll.account
            visible = account.puuid != GUEST_PUUID or guest_can_be_announced
            if not visible:
                silently_processed.add((account.discord_id, match_id))
            if queue_id not in RANKED_QUEUE_IDS:
                if visible:
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
            participant = next(
                (
                    item
                    for item in match_participants
                    if item.get("puuid") == account.puuid
                ),
                None,
            )
            if participant is None:
                continue

            current = None
            if is_only_game or is_last_game:
                current = (fetch_ranks(account.puuid, account.server) or {}).get(
                    queue_id
                )
                if current is not None:
                    pending_ranks.append(
                        _PendingRank(
                            poll=poll,
                            queue_id=queue_id,
                            snapshot=current,
                            participant=participant,
                            attributable=is_only_game and visible,
                        )
                    )

            if not visible:
                continue
            players.append(
                TrackedPlayer(
                    puuid=account.puuid,
                    riot_id=account.riot_id,
                    server=account.server,
                    lp_change=(
                        lp_change(poll.state.ranks.get(queue_id), current)
                        if is_only_game
                        else None
                    ),
                    rank=current if is_only_game else poll.state.ranks.get(queue_id),
                )
            )

        announcement = format_match(match, players)

        finished = bool(match.get("info", {}).get("gameEndTimestamp"))
        for pending in pending_ranks:
            if not finished or (announcement is None and pending.attributable):
                continue
            changed = pending.poll.state.record_rank(
                pending.queue_id,
                pending.snapshot,
                match_id=match_id,
                won=pending.participant.get("win"),
                attributable=pending.attributable,
                timestamp=int(match.get("info", {}).get("gameEndTimestamp", 0) / 1000)
                or None,
            )
            dirty = dirty or changed
        if announcement is None:
            continue

        announcements.append(announcement)
        announced_ids.add(match_id)

    for poll in polls:
        fetched = [
            match_id
            for match_id in poll.new_match_ids
            if match_id in matches
            and (
                matches[match_id].get("info", {}).get("queueId") not in RANKED_QUEUE_IDS
                or match_id in announced_ids
                or (poll.account.discord_id, match_id) in silently_processed
            )
        ]
        if fetched:
            poll.state.remember(fetched)
            dirty = True

    if dirty:
        save_tracker_state(state)

    return announcements


async def poll_and_announce(bot: Any) -> None:
    """Background loop entry point."""
    LOGGER.info("Checking for completed match announcements")
    channel = await resolve_announcement_channel(bot)
    if channel is None:
        LOGGER.warning(
            "Skipping match collection until the fallback channel is available"
        )
        return
    await publish(
        bot,
        await asyncio.to_thread(collect_new_matches),
        global_channel=channel,
    )


def collect_new_live_games() -> list[LiveGameAnnouncement]:
    """Find games that tracked accounts entered since the previous poll."""
    accounts = load_accounts()
    previous = load_live_game_state()

    current = {
        discord_id: previous[discord_id]
        for discord_id in accounts
        if discord_id in previous
    }
    grouped: dict[str, tuple[dict[str, Any], list[TrackedPlayer]]] = {}

    def fetch(account: Account) -> tuple[Account, dict[str, Any] | None]:
        """Fetch one item for the enclosing operation."""
        try:
            return account, get_client().active_game(account.puuid, account.server)
        except RiotAPIError as error:
            LOGGER.warning(
                "Could not fetch active game for %s: %s", account.riot_id, error
            )
            return account, None

    if not accounts:
        return []
    with ThreadPoolExecutor(max_workers=min(_FETCH_WORKERS, len(accounts))) as pool:
        games = pool.map(fetch, accounts.values())
        for account, game in games:
            if not game:
                continue
            if account.puuid == GUEST_PUUID and not guest_is_in_game_with_crispy(
                game.get("participants", []) or []
            ):
                continue
            game_id = game.get("gameId")
            if game_id is None:
                LOGGER.warning("Active game for %s had no gameId", account.riot_id)
                continue
            key = f"{account.server}:{game_id}"
            current[account.discord_id] = key

            if key in previous.values():
                continue

            # The spectator endpoint can briefly return a lobby after the
            # game has ended.  If the bot was offline during that window,
            # posting this as a live game would duplicate the match update.
            # Let the match poll announce the completed game instead.
            try:
                match = get_client().match(f"{account.server}_{game_id}", account.server)
            except RiotAPIError as error:
                LOGGER.debug(
                    "Could not verify whether live game %s has finished: %s", key, error
                )
                match = None
            if isinstance(match, dict) and match.get("info", {}).get("gameEndTimestamp"):
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
    LOGGER.info("Checking for live-game announcements")
    channel = await resolve_announcement_channel(bot)
    if channel is None:
        LOGGER.warning(
            "Skipping live-game collection until the fallback channel is available"
        )
        return
    await publish_live_games(
        bot,
        await asyncio.to_thread(collect_new_live_games),
        global_channel=channel,
    )

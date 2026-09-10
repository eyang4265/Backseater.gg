"""Background League and Teamfight Tactics polling for tracked accounts."""

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
    delete_live_game_messages,
    format_live_game,
    format_match,
    live_game_key_from_match_id,
    publish,
    publish_live_games,
    resolve_announcement_channel,
)
from .queues import (
    RANKED_QUEUE_IDS,
    TFT_RANKED_QUEUE_ID,
)
from .ranks import RankSnapshot, fetch_ranks, fetch_tft_rank, lp_change
from .riot import RiotAPIError, get_client
from .store import (
    Account,
    PlayerState,
    TftPlayerState,
    load_accounts,
    load_live_game_messages,
    load_live_game_state,
    load_tft_live_game_state,
    load_tft_accounts,
    load_tft_tracker_state,
    load_tracker_state,
    save_live_game_state,
    save_tft_live_game_state,
    save_tft_tracker_state,
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


def update_all_tft_rank_snapshots() -> tuple[int, int]:
    """Fill the missing Ranked TFT baseline for every tracked TFT account.

    The TFT completed-match poller only ever learns an account's Ranked TFT
    standing as a side effect of announcing a ranked game, and it compares that
    game against the *previous* baseline — so with no baseline the first ranked
    game after start shows no LP delta.  This mirrors
    :func:`update_all_rank_snapshots` for League: run once at startup, it seeds
    ``TftPlayerState.rank`` wherever it is still ``None`` without disturbing a
    baseline a real game has already moved.
    """
    accounts = load_tft_accounts()
    if not accounts:
        return 0, 0
    state = load_tft_tracker_state()

    def fetch(account: Account) -> tuple[str, RankSnapshot | None]:
        """Fetch one account's current Ranked TFT standing."""
        return account.discord_id, fetch_tft_rank(account.puuid, account.server)

    with ThreadPoolExecutor(
        max_workers=min(_FETCH_WORKERS, max(len(accounts), 1))
    ) as pool:
        results = list(pool.map(fetch, accounts.values()))

    seeded = 0
    failed = 0
    dirty = False
    for discord_id, snapshot in results:
        if snapshot is None:
            failed += 1
            continue
        entry = state.setdefault(discord_id, TftPlayerState())
        if entry.rank is None:
            entry.rank = snapshot
            dirty = True
            seeded += 1
    if dirty:
        save_tft_tracker_state(state)
    return seeded, failed


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
        polls = list(pool.map(fetch, accounts.values()))
    total_new = sum(len(poll.new_match_ids) for poll in polls)
    LOGGER.debug(
        "Polled %d accounts; %d unseen match ids found", len(accounts), total_new
    )
    return polls


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
        fetched = {match_id: match for match_id, match in results if match is not None}
    LOGGER.debug("Fetched %d/%d new matches", len(fetched), len(servers))
    return fetched


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

    Every completed queue a tracked player finishes is rendered through the
    shared match announcement renderer; there is no queue filter. Only Ranked
    Solo/Duo and Ranked Flex (``RANKED_QUEUE_IDS``) fetch ranks and record LP
    changes — every other queue is announced without a rank lookup.

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
    dirty = False

    for match_id in match_order:
        match = matches[match_id]
        match_info = match.get("info", {})
        queue_id = match_info.get("queueId")
        players: list[TrackedPlayer] = []
        pending_ranks: list[_PendingRank] = []
        match_participants = match_info.get("participants", []) or []
        participants_by_puuid = {
            item.get("puuid"): item
            for item in match_participants
            if item.get("puuid") is not None
        }
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
            participant = participants_by_puuid.get(account.puuid)
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
                            attributable=is_only_game,
                        )
                    )

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

        announcement = format_match(
            match,
            players,
            require_ranked_queue=False,
        )

        game_end_timestamp = match_info.get("gameEndTimestamp")
        finished = bool(game_end_timestamp)
        for pending in pending_ranks:
            if not finished or (announcement is None and pending.attributable):
                continue
            changed = pending.poll.state.record_rank(
                pending.queue_id,
                pending.snapshot,
                match_id=match_id,
                won=pending.participant.get("win"),
                attributable=pending.attributable,
                timestamp=int((game_end_timestamp or 0) / 1000) or None,
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
            )
        ]
        if fetched:
            poll.state.remember(fetched)
            dirty = True

    if dirty:
        save_tracker_state(state)

    if announcements:
        LOGGER.info("Collected %d new match announcement(s)", len(announcements))
    return announcements


async def poll_and_announce(bot: Any) -> None:
    """Publish newly completed League and TFT matches through the shared renderer.

    For each finished League match the live-game announcement posted when the
    players entered that lobby is deleted first, then the completed-match
    announcement is posted in its place.
    """
    LOGGER.info("Checking for completed League and TFT match announcements")
    channel = await resolve_announcement_channel(bot)
    if channel is None:
        LOGGER.warning(
            "Skipping match collection until the fallback channel is available"
        )
        return
    league, tft = await asyncio.gather(
        asyncio.to_thread(collect_new_matches),
        asyncio.to_thread(collect_new_tft_matches),
    )
    # The game behind any completed League match is over, so first delete the
    # live-game announcement that was posted when the players entered it, then
    # post the completed-match announcement in its place.
    finished_keys: set[str] = set()
    for announcement in league:
        if announcement.game_type != "lol":
            continue
        match_id = announcement.match.get("metadata", {}).get("matchId", "")
        key = live_game_key_from_match_id(match_id)
        if key is not None:
            finished_keys.add(key)
    if finished_keys:
        await delete_live_game_messages(bot, sorted(finished_keys))
    await publish(bot, [*league, *tft], global_channel=channel)


def collect_new_tft_matches() -> list[MatchAnnouncement]:
    """Collect unseen TFT matches without replaying history on first deployment."""
    accounts = load_tft_accounts()
    state = load_tft_tracker_state()
    if not accounts:
        return []

    def fetch_ids(account: Account) -> tuple[Account, list[str] | None]:
        try:
            return account, get_client().tft_match_ids(
                account.puuid, account.server, count=MATCH_LOOKBACK
            )
        except RiotAPIError as error:
            LOGGER.warning("Could not fetch TFT match ids for %s: %s", account.riot_id, error)
            return account, None

    with ThreadPoolExecutor(max_workers=min(_FETCH_WORKERS, len(accounts))) as pool:
        histories = list(pool.map(fetch_ids, accounts.values()))

    pending: dict[str, list[Account]] = {}
    dirty = False
    for account, match_ids in histories:
        if match_ids is None:
            continue
        player_state = state.setdefault(account.discord_id, TftPlayerState())
        if not player_state.initialized:
            player_state.remember(match_ids)
            dirty = True
            continue
        known = set(player_state.matches)
        for match_id in reversed(match_ids):
            if match_id not in known:
                pending.setdefault(match_id, []).append(account)

    def fetch_match(item: tuple[str, list[Account]]) -> tuple[str, dict[str, Any] | None]:
        match_id, match_accounts = item
        try:
            return match_id, get_client().tft_match(match_id, match_accounts[0].server)
        except RiotAPIError as error:
            LOGGER.warning("Could not fetch TFT match %s: %s", match_id, error)
            return match_id, None

    with ThreadPoolExecutor(max_workers=min(_FETCH_WORKERS, max(len(pending), 1))) as pool:
        fetched = dict(pool.map(fetch_match, pending.items())) if pending else {}

    order = sorted(
        (match_id for match_id, match in fetched.items() if match is not None),
        key=lambda match_id: fetched[match_id].get("info", {}).get("game_datetime", 0),
    )

    def _tft_queue_id(match: dict[str, Any]) -> Any:
        info = match.get("info", {})
        return info.get("queue_id") or info.get("queueId")

    # Every new Ranked TFT match per account, oldest first, so LP is attributed
    # to a single game only when it is that account's lone ranked game this poll
    # (mirroring the League match poller).
    ranked_by_account: dict[str, list[str]] = {}
    for match_id in order:
        if _tft_queue_id(fetched[match_id]) != TFT_RANKED_QUEUE_ID:
            continue
        for account in pending[match_id]:
            ranked_by_account.setdefault(account.discord_id, []).append(match_id)

    announcements: list[MatchAnnouncement] = []
    for match_id in order:
        match = fetched[match_id]
        is_ranked = _tft_queue_id(match) == TFT_RANKED_QUEUE_ID
        players: list[TrackedPlayer] = []
        fresh_ranks: list[tuple[str, RankSnapshot]] = []
        for account in pending[match_id]:
            player_state = state.setdefault(account.discord_id, TftPlayerState())
            lp_delta: str | None = None
            if is_ranked:
                in_queue = ranked_by_account.get(account.discord_id, [])
                is_only_game = len(in_queue) == 1
                is_last_game = bool(in_queue) and in_queue[-1] == match_id
                if is_only_game or is_last_game:
                    current = fetch_tft_rank(account.puuid, account.server)
                    if current is not None:
                        fresh_ranks.append((account.discord_id, current))
                        if is_only_game:
                            lp_delta = lp_change(player_state.rank, current)
            players.append(
                TrackedPlayer(
                    puuid=account.puuid,
                    riot_id=account.riot_id,
                    server=account.server,
                    lp_change=lp_delta,
                )
            )
        announcement = format_match(
            match,
            players,
            require_ranked_queue=False,
            game_type="tft",
        )
        # Remember every match that actually came back, announced or not, so an
        # unformattable game is not re-fetched on every subsequent poll.  Only a
        # failed fetch stays pending, since that failure is transient.
        for account in pending[match_id]:
            state.setdefault(account.discord_id, TftPlayerState()).remember([match_id])
        for discord_id, snapshot in fresh_ranks:
            state.setdefault(discord_id, TftPlayerState()).rank = snapshot
        dirty = True
        if announcement is None:
            continue
        announcements.append(announcement)

    if dirty:
        save_tft_tracker_state(state)
    if announcements:
        LOGGER.info("Collected %d new TFT match announcement(s)", len(announcements))
    return announcements


def collect_new_live_games() -> list[LiveGameAnnouncement]:
    """Find games that tracked accounts entered since the previous poll.

    An account's stored live-game key is also retired here the moment
    ``active_game`` confirms — without erroring — that the player is in no game,
    so the saved live state only ever names lobbies that are still in progress
    and :func:`_stale_live_game_message_keys` can spot finished ones.
    """
    accounts = load_accounts()
    previous = load_live_game_state()

    current = {
        discord_id: previous[discord_id]
        for discord_id in accounts
        if discord_id in previous
    }
    grouped: dict[
        str, tuple[dict[str, Any], list[TrackedPlayer], str, int]
    ] = {}
    previous_keys = set(previous.values())

    def fetch(account: Account) -> tuple[Account, dict[str, Any] | None, bool]:
        """Fetch one item for the enclosing operation.

        The third element is False only when the lookup failed transiently; a
        clean "not in a game" answer returns ``(account, None, True)`` so the
        caller can retire that account's stale live-game key.
        """
        try:
            return (
                account,
                get_client().active_game(account.puuid, account.server),
                True,
            )
        except RiotAPIError as error:
            LOGGER.warning(
                "Could not fetch active game for %s: %s", account.riot_id, error
            )
            return account, None, False

    if not accounts:
        return []
    with ThreadPoolExecutor(max_workers=min(_FETCH_WORKERS, len(accounts))) as pool:
        games = list(pool.map(fetch, accounts.values()))

    for account, game, ok in games:
        if not game:
            # Confirmed out of any game: forget the finished lobby so the live
            # state keeps reflecting only games actually in progress.
            if ok:
                current.pop(account.discord_id, None)
            continue
        game_id = game.get("gameId")
        if game_id is None:
            LOGGER.warning("Active game for %s had no gameId", account.riot_id)
            continue
        game_id = int(game_id)
        key = f"{account.server}:{game_id}"
        current[account.discord_id] = key

        if key in previous_keys:
            continue
        if key not in grouped:
            grouped[key] = (game, [], account.server, game_id)
        grouped[key][1].append(
            TrackedPlayer(
                puuid=account.puuid,
                riot_id=account.riot_id,
                server=account.server,
            )
        )

    if current != previous:
        save_live_game_state(current)

    LOGGER.debug("%d new live-game lobbies to verify", len(grouped))
    announcements: list[LiveGameAnnouncement] = []
    for key, (game, players, server, game_id) in grouped.items():
        # Verify each distinct lobby once even when several tracked accounts
        # share it. Ongoing games are deliberately not stored in MatchCache.
        try:
            match = get_client().match(f"{server}_{game_id}", server)
        except RiotAPIError as error:
            LOGGER.debug(
                "Could not verify whether live game %s has finished: %s", key, error
            )
            match = None
        if isinstance(match, dict) and match.get("info", {}).get("gameEndTimestamp"):
            continue
        announcement = format_live_game(game, players)
        if announcement is not None:
            announcements.append(announcement)
    if announcements:
        LOGGER.info("Collected %d new live-game announcement(s)", len(announcements))
    return announcements


def _stale_live_game_message_keys() -> list[str]:
    """Recorded live-game post keys whose lobby is no longer in the live state."""
    live = set(load_live_game_state().values())
    return [key for key in load_live_game_messages() if key not in live]


async def poll_live_games_and_announce(bot: Any) -> None:
    """Publish first-seen League live lobbies through the shared renderer.

    TFT live lobbies are disabled: Riot's Spectator-TFT-V5 endpoint returns
    HTTP 403 for this key, so ``collect_new_tft_live_games`` never succeeded.
    The collector and its shared renderers are kept so re-enabling it once the
    key is authorized is a one-line change here.
    """
    LOGGER.info("Checking for League live-game announcements")
    channel = await resolve_announcement_channel(bot)
    if channel is None:
        LOGGER.warning(
            "Skipping live-game collection until the fallback channel is available"
        )
        return
    league = await asyncio.to_thread(collect_new_live_games)
    await publish_live_games(bot, league, global_channel=channel)
    # A recorded live-game post whose lobby no longer appears in the live state
    # belongs to a game that ended (or was dodged) without a completed-match
    # announcement to clear it; delete it here instead.
    stale = await asyncio.to_thread(_stale_live_game_message_keys)
    if stale:
        await delete_live_game_messages(bot, stale)


def collect_new_tft_live_games() -> list[LiveGameAnnouncement]:
    """Find TFT lobbies that tracked accounts entered since the previous poll."""
    accounts = load_tft_accounts()
    previous = load_tft_live_game_state()
    current = {
        discord_id: previous[discord_id]
        for discord_id in accounts
        if discord_id in previous
    }
    previous_keys = set(previous.values())
    grouped: dict[str, tuple[dict[str, Any], list[TrackedPlayer], str, str]] = {}

    def fetch(account: Account) -> tuple[Account, dict[str, Any] | None]:
        try:
            return account, get_client().tft_active_game(account.puuid, account.server)
        except RiotAPIError as error:
            LOGGER.warning("Could not fetch active TFT game for %s: %s", account.riot_id, error)
            return account, None

    if not accounts:
        return []
    with ThreadPoolExecutor(max_workers=min(_FETCH_WORKERS, len(accounts))) as pool:
        games = list(pool.map(fetch, accounts.values()))

    for account, game in games:
        if not game:
            continue
        game_id = game.get("gameId")
        if game_id is None:
            LOGGER.warning("Active TFT game for %s had no gameId", account.riot_id)
            continue
        match_id = str(game_id)
        if "_" not in match_id:
            match_id = f"{account.server}_{match_id}"
        key = f"tft:{match_id}"
        current[account.discord_id] = key
        if key in previous_keys:
            continue
        if key not in grouped:
            grouped[key] = (game, [], account.server, match_id)
        grouped[key][1].append(
            TrackedPlayer(account.puuid, account.riot_id, account.server)
        )

    if current != previous:
        save_tft_live_game_state(current)

    announcements: list[LiveGameAnnouncement] = []
    for key, (game, players, server, match_id) in grouped.items():
        try:
            completed = get_client().tft_match(match_id, server)
        except RiotAPIError:
            completed = None
        if isinstance(completed, dict) and completed.get("info"):
            continue
        announcement = format_live_game(game, players, game_type="tft")
        if announcement is not None:
            announcements.append(announcement)
    if announcements:
        LOGGER.info("Collected %d new TFT live-game announcement(s)", len(announcements))
    return announcements

# Code Review — uncommitted working tree vs `0887eb0`

Scope: 26 modified files (+577/−118) and 17 new files implementing the account
registry, LP history, leaderboard, per-guild routing, and SQLite match cache.

Suite status: **123 tests pass** (`.venv/bin/python -m unittest discover -s tests -t .`).

Overall this is a solid implementation of the plan. The Phase 0 correctness
fixes (D1 baseline resync, `GUILD_IDS or None`, `summoner1Id` Smite detection,
negative-cache policy, live-state pruning, `teamPosition` None guard, poller
stagger, dropped `message_content` intent) all landed and are individually
tested. The findings below are concentrated in the two areas where new features
interact with existing state: **rank-baseline ownership** and **announcement
delivery**.

---

## P0 — Critical

### P0-1 `/leaderboard refresh:true` silently corrupts LP attribution and history

`bot_app/commands/rankings.py:115-120` → `bot_app/tracker.py:50`

`update_all_rank_snapshots()` writes `entry.ranks.update(ranks)` directly,
bypassing `PlayerState.record_rank`. That is safe at startup (its documented
purpose is to establish a baseline), but this PR makes it reachable on demand
from a slash command.

Failure scenario:

1. A tracked player finishes a ranked game at 20:00:10.
2. The owner runs `/leaderboard queue:solo refresh:true` at 20:00:30.
   `update_all_rank_snapshots` overwrites the **pre-match** baseline with the
   **post-match** rank.
3. The match poller runs at 20:02:00, computes
   `lp_change(post_game_rank, post_game_rank)`, and announces **"+0 LP"**.
4. `record_rank` persists `{"d": 0}` into `history`, which is now permanent —
   the LP graph and `/today` are wrong for that game forever.

This is worse than the pre-existing startup race because it is user-triggered
and repeatable, and because this PR added a durable history that records the
bad value rather than just displaying it.

Fix options, in order of preference:

- Have `update_all_rank_snapshots` only fill baselines that are **missing**
  (`entry.ranks.setdefault(...)`), and give `/leaderboard refresh` a separate
  read-only path that returns fresh snapshots for display without writing them
  to `match_tracker_state.json`; or
- gate the refresh on "no unprocessed matches pending" — but that requires a
  poll cycle to prove, so the first option is cleaner.

Also add the invariant to the docstring: **only `record_rank` may move a
baseline once one exists.**

### P0-2 Per-guild routing can drop announcements permanently, with no fallback and no retry

`bot_app/announce.py:207-245`, `bot_app/announce.py:349-380`

```python
configured = load_guild_channels()
if not configured:
    channel = await resolve_announcement_channel(bot)
    return [channel] if channel is not None else []
```

The legacy `settings.announcement_channel_id` is used **only when no guild is
configured at all**. The moment anyone runs `/setchannel` in any one guild, the
global channel stops receiving anything, including announcements for players who
are not members of that guild. The README still documents
`ANNOUNCEMENT_CHANNEL_ID` as required, so this is a silent behaviour change for
the current deployment.

Worse, the drop is unrecoverable. `collect_new_matches()` marks matches as seen
and saves state **before** `publish()` runs (`bot_app/tracker.py:266-285`), so an
announcement that resolves to zero channels is gone — the next poll will not
retry it. Membership resolution is the likely trigger:
`guild.get_member()` returns `None` on a cache miss and the `fetch_member`
fallback swallows every `discord.DiscordException` (`announce.py:232-235`), so a
transient Discord error looks identical to "not a member" and produces an empty
channel list.

Fix:

- Always include the global `announcement_channel_id` as a fallback, or at
  minimum when the guild-routed set resolves to empty.
- Distinguish `NotFound` (genuinely not a member) from other
  `DiscordException`s; on a transient failure, log and include the channel
  rather than skipping it.
- Log at WARNING when an announcement resolves to zero channels — right now
  that path is completely silent.

---

## P1 — Important

### P1-1 `publish()` re-renders the entire embed once per destination channel

`bot_app/announce.py:353-364`

```python
for channel in channels:
    try:
        # discord.File objects are single-use, so render a fresh
        # attachment for every destination.
        embed, chart = await build_announcement_embed(announcement)
```

The comment justifies rebuilding the **File**, but the loop rebuilds the whole
embed — and `build_announcement_embed` calls `build_match_columns`, which fans
out **10 `league-v4` + 10 `account-v1` requests** (`bot_app/render.py:343`).
Two configured guilds therefore cost ~40 Riot requests for one announcement,
against a 100-per-2-minute budget that idle polling already spends ~33 of. The
rate limiter responds by blocking poller threads, so this shows up as poll
cycles stretching past their interval.

Fix: build the embed and render the PNG to `bytes` **once**, then construct a
fresh `discord.File(io.BytesIO(png), filename=...)` per channel. `discord.Embed`
objects are safe to send to multiple channels.

### P1-2 A ranked match that never produces an announcement is re-fetched and re-recorded every poll

`bot_app/tracker.py:229-262` and `bot_app/tracker.py:266-277`

The mark-as-seen predicate now requires `match_id in announced_ids` for ranked
queues, but `record_rank` runs **before** the `if announcement is None: continue`
guard and is not gated on the announcement succeeding. So when `format_match`
returns `None` for a finished ranked match (its `lines` list is empty — e.g. the
tracked puuid is absent from the payload's `participants`, or a malformed
payload), each poll cycle:

- re-fetches the match (1 `match-v5` request),
- re-fetches ranks (1 `league-v4` request),
- appends **another** history row for the same match, and
- sets `dirty = True`, rewriting `match_tracker_state.json`.

It self-heals only once the id falls out of the 20-match lookback window, so a
low-traffic account can accumulate dozens of duplicate rows for one game — in the
history that `/lpgraph` and `/today` read.

Fix: make `record_rank` idempotent per `(queue_id, match_id)` (skip if the last
entry already references that match id), and/or move the `record_rank` loop below
the announcement check while still committing the resync — the two concerns can
be separated with an explicit `resynced_ids` set.

The existing regression test does not cover this: `tests/test_tracker.py:38`
patches `bot_app.tracker.format_match` with a stub that always returns an
announcement, so the `None` branch is never exercised.

### P1-3 `duo_record` cannot distinguish teammates from opponents

`bot_app/match_cache.py:169-181`

```sql
FROM participants a JOIN participants b ON a.match_id = b.match_id
WHERE a.puuid = ? AND b.puuid = ? AND a.win = b.win
```

The `participants` table has no `team_id` column, so `a.win = b.win` is used as a
proxy for "same team". It is wrong in two reachable cases:

- **Remakes**: `win` is `false` for every participant, so an *opponent* is
  counted as a duo partner, and the game is counted as a loss.
- **`/duo` with yourself as the teammate**: the self-join matches every row, so
  every solo game is reported as a duo game with a 100%-correct-looking win rate.

Arena (queue 1700/1710) has eight two-player teams and will also mis-join.

Fix: add `team_id INTEGER NOT NULL` to the `participants` schema (bump
`PRAGMA user_version` to 2 and rebuild, or `ALTER TABLE ... ADD COLUMN`), join on
`a.team_id = b.team_id`, and reject `first_puuid == second_puuid` in
`duo_record`. Consider excluding remakes via
`gameEndedInEarlySurrender` / a short-duration filter.

### P1-4 The match cache grows without bound

`bot_app/match_cache.py:145-151`

`prune()` is implemented and tested but **never called** from application code —
only from `tests/test_match_cache.py:40`. Every completed match fetched by the
poller or by `/matchhistory` is stored as a full JSON payload (match-v5 payloads
run ~100–200 KB), and `riot.match()` now writes to the cache on every miss
(`bot_app/riot.py:335-345`). At 16 accounts this is on the order of a gigabyte a
year in `json/matches.sqlite`, on a path that is gitignored but never swept.

Fix: call `prune()` on startup (in `on_ready`, off the event loop) and/or once
per N poll cycles. Add `_usable` and `sqlite3.DatabaseError` handling to `prune`
— unlike `get`/`put`/`champion_stats`/`duo_record` it has neither, so it will
raise on a corrupt database instead of degrading.

### P1-5 The `record_rank` block in `collect_new_matches` is unnecessarily complex and identity-dependent

`bot_app/tracker.py:229-262`

```python
participant = next(
    (item for item in match.get("info", {}).get("participants", []) or []
     if any(item.get("puuid") == poll.account.puuid and poll.state is player_state
            for poll in participants[match_id])),
    None,
)
```

This is an O(participants × polls) scan that reconstructs information the outer
loop already had, and its correctness depends on `PlayerState` **object
identity** (`poll.state is player_state`) surviving unchanged — which holds today
only because `state.setdefault(...)` hands out one object per account. `only_game`
below it re-derives the same fact a third time. A future refactor that copies or
reloads state anywhere in this function will break it silently, with no test
catching it.

Fix: carry the data forward instead of re-deriving it. Change `pending_ranks` to
hold a small record — `(_AccountPoll, queue_id, snapshot, participant, is_only_game)`
— populated in the loop above where all four values are already in scope. That
removes both comprehensions and the identity assumption.

### P1-6 `resolve_announcement_channels` does disk and network I/O per announcement

`bot_app/announce.py:207-245`, called at `announce.py:353` and `announce.py:373`

Each call re-reads `guilds.json` and `data.json` from disk and may issue a
`guild.fetch_member` HTTP request per configured guild. It is called once per
announcement inside the publish loop, so a batch of five announcements repeats
all of it five times with identical inputs.

Fix: resolve the channel set once per `publish()` call. If per-announcement
membership filtering is needed, load accounts and guild config once and pass them
in; cache negative `fetch_member` results for the life of the call.

---

## P2 — Improvement

### Correctness / robustness

| # | Location | Finding |
| --- | --- | --- |
| P2-1 | `bot_app/commands/guilds.py:15-26` | `set_guild_channel` / `unset_guild_channel` do an unguarded read-modify-write on `guilds.json`. The PR added `store.update_accounts` precisely to fix this class of race for `data.json`, then reintroduced it here. Two concurrent `/setchannel` calls, or `/setchannel` racing `on_guild_remove` (`main.py:99-101`), can lose an entry. Add a `store.update_guild_channels(mutate)` on the same `_write_lock`. |
| P2-2 | `bot_app/config.py:171-177` | `_timezone` catches only `ZoneInfoNotFoundError`. `ZoneInfo` raises `ValueError` for structurally invalid keys (leading `/`, `..` segments), which escapes as an unhandled crash at startup instead of a `ConfigError`. Catch `(ZoneInfoNotFoundError, ValueError)`. Also note `zoneinfo` needs the `tzdata` package on Windows; `requirements.txt` doesn't list it. |
| P2-3 | `bot_app/commands/guilds.py:34,46` | `@commands.has_permissions(manage_guild=True)` raises `MissingPermissions`, and there is no `on_application_command_error` handler anywhere in the project. A non-admin sees Discord's generic "The application did not respond." Add a handler that converts check failures into an ephemeral embed. |
| P2-4 | `bot_app/account_registry.py:71-73` | The owner reassign path pops the previous owner from `accounts` and `tracker_state` but leaves their `live_game_state.json` entry, which then permanently suppresses that game id. Purge it alongside, as `untrack_account` does. |
| P2-5 | `bot_app/account_registry.py:25-30` | `@dataclass(frozen=True)` on an exception subclass leaves `BaseException.args` empty, so `repr()`, `logging.exception`, and pickling all lose the payload. Use a plain `__init__` that calls `super().__init__(message)`. |
| P2-6 | `bot_app/riot.py:336,345` | `assert route is not None` is stripped under `python -O`, turning a documented invariant into a silent `None` host in the URL. Raise `RiotAPIError` instead. |
| P2-7 | `bot_app/commands/shared.py:89` | `str(getattr(user, "id", user)).strip().lstrip("<@!").rstrip(">")` keeps the string-mention parsing path even though the option is now typed. `lstrip("<@!")` strips a *set* of characters, so any leading `!`/`@`/`<` combination is silently eaten. Since `/track` and `/duo` declare `discord.User` options, either type all `user` options and drop the string path, or keep one documented helper — not both. |

### Simplification / dead code

| # | Location | Finding |
| --- | --- | --- |
| P2-8 | `bot_app/paginator.py` vs `bot_app/commands/mastery.py:52-155` | The reusable `Paginator` was added, but `MasteryPaginator` still carries its own full copy — four button handlers, `_sync_buttons`, `on_timeout`, `interaction_check`. Two implementations of the same widget with differing copy ("This isn't your mastery list!" vs "Run the command yourself…"). Migrate mastery onto `Paginator`; its existing tests should not need to change. |
| P2-9 | `bot_app/commands/shared.py:50-57` | `_puuid_for_mention` is now unreachable — the only caller was replaced at line 89. Delete it. |
| P2-10 | `bot_app/store.py:198,207,247` | The history cap `500` is hardcoded in three places (`from_json`, `to_json`, `record_rank`). Promote to a module constant next to `MATCH_HISTORY_LIMIT`. |
| P2-11 | `bot_app/commands/rankings.py:35` | `enumerate(items, start=page * 10 + 1)` hardcodes the page size while `Paginator(...)` at line 127 relies on the default `page_size=10`. Changing one silently corrupts leaderboard numbering. Pass the page size through, or have `render_page` receive the absolute start index. |
| P2-12 | `bot_app/match_cache.py:45-52` | `_database()` opens and closes a fresh `sqlite3` connection per query, so `/matchhistory` performs 10 connect/close cycles under a lock. Hold one connection per thread (`threading.local`) or one shared connection with `check_same_thread=False`. |
| P2-13 | `bot_app/riot.py:335-339` | `from .match_cache import get_match_cache` inside the method inverts the layering (adapter depends on a sibling cache) and hides the dependency from the new AST architecture test. Injecting the cache into `RiotClient.__init__` would keep the boundary explicit. |

### UX / documentation

| # | Location | Finding |
| --- | --- | --- |
| P2-14 | `bot_app/commands/stats.py:47` | `/championstats` prints `row.champion`, which stores match-v5's `championName` — the Data Dragon **internal id**. Users see "MonkeyKing", "DrMundo", "Velkoz" instead of "Wukong", "Dr. Mundo", "Vel'Koz". Every other command maps through `ddragon.catalog().by_key`/`by_internal_id`. Resolve for display (the query path at line 38 already resolves correctly). |
| P2-15 | `bot_app/commands/registry.py:74-85` | `/accounts` is public and renders the full Discord-id → Riot-id mapping for every tracked member. That data was previously owner-only (`/data`). Embed content does not ping, so there is no notification issue, but this is a real change in who can deanonymize whom — worth confirming it is intended. Note also that `/data` passes `allowed_mentions=discord.AllowedMentions.none()` and `/accounts` does not; make them consistent. |
| P2-16 | `bot_app/commands/shared.py:90-92` | When `user` is supplied but untracked, `resolve_target` now returns `None` (previously it fell back to the caller). The resulting message is `not_found_embed(None, …)` → "That account could not be found on NA1, EUW1, and KR", which is misleading — the account was never looked up. Add a distinct "that user has no tracked account" response. |
| P2-17 | `bot_app/commands/rankings.py:39-45` | The "Oldest snapshot: Nh ago" footer is computed from the **current page only**, so page 2 can report a different, younger "oldest" than page 1. Compute it once across all rows. |
| P2-18 | `bot_app/commands/rankings.py:113`, `registry.py:63,75` | `/leaderboard`, `/accounts`, and `/untrack` respond without `ctx.defer()`. They do file I/O (and `/untrack` a multi-file read-modify-write) inside Discord's 3-second initial-response window. Defer them, as every other command in the codebase does. |
| P2-19 | `README.md:43`, `tests/test_architecture.py:33-48` | The README layout table still lists `ranks` under "Domain — pure logic, no I/O", but `ranks.py` imports `riot` for `fetch_ranks`, which is why the new `test_pure_modules_do_not_import_io_layers` quietly omits it. Either split `fetch_ranks` out into the adapter layer or correct the table so the exception is documented rather than implied. |

---

## Missing tests

The new suite is good where it exists — the D1 back-to-back regression, the
history cap, the timezone boundary, the concurrent `update_accounts`, and the
corrupt-database fallback are all well chosen. Gaps, roughly in priority order:

1. **`publish()` / channel fan-out** — nothing covers the publish loop. Needed:
   distinct `discord.File` objects per channel; embed built once (guards P1-1);
   an announcement that resolves to zero channels is logged, not silently
   dropped (P0-2).
2. **Guild routing fallback** — `tests/test_guild_routing.py:24` proves the
   legacy channel is used when *nothing* is configured. There is no test for the
   case that actually matters: guilds configured, but this announcement matches
   none of them (P0-2).
3. **`update_all_rank_snapshots` vs. an unprocessed match** — no test asserts
   that refreshing snapshots cannot clobber a pending baseline (P0-1).
4. **`format_match` returning `None` for a ranked match** —
   `tests/test_tracker.py:38` stubs `format_match` to always succeed, so the
   re-record/re-fetch loop in P1-2 is invisible to the suite. Add a case with the
   real `format_match` and a payload whose `participants` omit the tracked puuid,
   asserting exactly one history row after two consecutive polls.
5. **`duo_record` team semantics** — `tests/test_match_cache.py:33` only covers
   two winners on the same team. Add: opponents in a decided game (expect 0
   games), both participants in a remake (expect 0), and `duo_record("a", "a")`.
6. **`riot.match()` cache integration** — no test that a cache hit skips the HTTP
   call, that `put` is invoked on a miss, or that `match_cache_enabled=False`
   bypasses the cache entirely.
7. **`RateLimiter`** — `tests/test_riot_client.py` is 20 lines covering only the
   `None` caching policy. The sliding-window budget and the retry-exhaustion path
   (`RiotAPIError` rather than an unbounded loop) remain untested, and they are
   what protect the poll loop.
8. **`resolve_target` / `target_for`** — the no-summoner branch was rewritten
   (`shared.py:85-97`) with a behaviour change, and has no test. Cover: caller's
   own account; supplied tracked `user`; supplied untracked `user`; combined
   `Name#Tag`; platform code in the `tag` field.
9. **`build_lp_chart`** — untested, including the `MATPLOTLIB_AVAILABLE = False`
   path that `/lpgraph` depends on for its text fallback.
10. **`guilds.set_guild_channel` / `unset_guild_channel`** — untested, including
    the concurrent case in P2-1.

---

## Not defects — verified during review

- `Paginator.on_timeout` calling `self.message.edit(...)` is safe:
  `ctx.respond()` returns an `Interaction` for non-deferred commands, and
  py-cord 2.8.1's `discord.Interaction` does expose `.edit`.
- `match_cache.put` correctly refuses payloads without `gameEndTimestamp`
  (`match_cache.py:104-106`), so in-progress matches never enter the cache and
  `riot.match()` cannot serve a stale unfinished payload to the poller.
- `store.update_accounts` is genuinely atomic: `_write_lock` was correctly
  upgraded to an `RLock` so the nested `save_accounts` → `write_json` acquisition
  re-enters rather than deadlocking.
- `track_account` orders its writes state-first, so a failure between the two
  saves leaves an orphaned state entry (harmless) rather than an unseeded account
  (which would backfill announcements).

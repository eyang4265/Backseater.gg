# VibeCode Bot — Feature Design & Implementation Plan

Status: design document. Nothing in here has been implemented.
Scope: analysis of the repository as of `0887eb0`, plus a costed design for the
next set of features.

---

## 1. What the project is

A single-process Discord bot (py-cord 2.8) that tracks a fixed roster of League
of Legends accounts (16 today, in `json/data.json`) for one friend group:

- three background pollers announce finished ranked matches, newly detected live
  lobbies, and a special-cased "guest" pairing;
- eleven public slash commands and six owner-only ones answer profile, mastery,
  match-history, recap, and timeline lookups;
- all state is JSON files on disk; all Riot traffic goes through one client.

The suite is 100 tests, all network-free, and passes:

```bash
.venv/bin/python -m unittest discover -s tests -t .
```

---

## 2. Architecture as built

### 2.1 Layers

The README documents a strict layering, and the runtime modules do respect it:

| Layer | Modules | Notes |
| --- | --- | --- |
| Config | [config.py](bot_app/config.py), [runtime.py](bot_app/runtime.py) | env → `json/secrets.json` → default; `lru_cache`d |
| Domain (pure) | [routing.py](bot_app/routing.py), [queues.py](bot_app/queues.py), [ranks.py](bot_app/ranks.py), [positions.py](bot_app/positions.py), [timeline.py](bot_app/timeline.py), [history.py](bot_app/history.py) | no I/O; this is where the tests live |
| Adapters | [riot.py](bot_app/riot.py), [ddragon.py](bot_app/ddragon.py), [store.py](bot_app/store.py) | `RiotClient` is the single Riot egress point |
| Presentation | [emoji.py](bot_app/emoji.py), [render.py](bot_app/render.py), [charts.py](bot_app/charts.py) | embeds, team columns, matplotlib images |
| Features | [announce.py](bot_app/announce.py), [tracker.py](bot_app/tracker.py), [guest_tracker.py](bot_app/guest_tracker.py), [accounts.py](bot_app/accounts.py) | poll → format → publish |
| Interface | [commands/](bot_app/commands) | cogs, registered explicitly by `register_all` |

`ranks.py` sits in the "domain" row but imports `riot` for `fetch_ranks`, so the
pure/impure line actually runs through the middle of that module.

### 2.2 The shim tree is not architecture

`bot_app/domain/`, `bot_app/services/`, `bot_app/presentation/`, and
`bot_app/repositories/` are **eight files of pure `from ..x import y`
re-exports**. They enforce nothing: `commands/player/profile.py` imports
`...services.riot_api` on one line and `...ranks`, `...render`, `...history`
directly on the next. [test_architecture.py](tests/test_architecture.py) only
asserts three `__name__` strings, so a real boundary violation cannot fail it.

Same pattern in the command tree: `player/livegame.py`, `player/matchhistory.py`,
`player/matchlist.py`, `player/opgg.py`, `player/puuid.py`, `admin/data.py`,
`admin/reload.py`, and `info/serverstatus.py` are one-line re-exports of a class
defined elsewhere. Eight files, zero behaviour.

Decision to make before adding features: either delete the shims, or make them
load-bearing (§6.4). Leaving them adds a second import path for every symbol,
which is how the two `format_duration` implementations (`render.py:79`,
`history.py:10`) already diverged into a copy.

### 2.3 Control flow

**Startup** ([main.py](main.py)): `get_settings()` → `build_bot` → `configure_bot`
(stores the client for `emoji.py`) → `register_all` → three `tasks.loop`s created
at `poll_interval_seconds` (default 120). `on_ready` runs
`update_all_rank_snapshots()` once (establishing LP baselines) then starts all
three loops in the same tick.

**Match poll** ([tracker.py:150](bot_app/tracker.py#L150)): fan out `match_ids`
over 16 accounts (8 threads) → dedupe new ids → fetch each match once → sort by
`gameEndTimestamp` → per (player, queue) decide LP attributability → `format_match`
→ `publish` (embed + team columns + damage chart + gold button).

**Live poll** ([tracker.py:255](bot_app/tracker.py#L255)): `active_game` per
account → group by `server:gameId` → skip keys already in the previous state →
`format_live_game` → publish with `build_lobby_columns`.

**Command path**: `resolve_target(ctx, server, summoner, tag, user)` → optional
`ctx.defer()` → Riot calls in `asyncio.to_thread` → embed → `ctx.respond`.

### 2.4 On-disk state

| File | Shape | Writer |
| --- | --- | --- |
| `data.json` | `{discord_id: {puuid, server, riotId}}` | `save_accounts` — only ever called by `/updateriotids` |
| `match_tracker_state.json` | `{discord_id: {matches: [...], solo: {...}, flex: {...}}}` | match poller |
| `live_game_state.json` | `{discord_id: "SERVER:gameId"}` | live poller |
| `guest_tracker_state.json` | `{matches: [...]}` | guest poller |

`store.write_json` writes a `.tmp` sibling and `replace()`s it, so a crash
mid-write cannot truncate a file. `PlayerState.from_json` already tolerates a
legacy bare-list format — the precedent for schema evolution is established.

---

## 3. Constraints every new feature must respect

**3.1 The Riot rate budget is the binding constraint.** A dev key allows
20 req/s and **100 req / 2 min**, and `config.riot_rate_limits` encodes exactly
that. Steady-state consumption per 120 s cycle today:

| Source | Requests |
| --- | --- |
| match poller `match_ids` × 16 accounts | 16 |
| live poller `active_game` × 16 accounts | 16 |
| guest poller `match_ids` | 1 |
| new match detail fetches | 0–3 |
| **idle subtotal** | **~33 / 100** |
| one match announcement (`build_match_columns`: 10 × `league_entries`, 10 × `riot_id` cold) | +10 to +20 |
| one live-lobby announcement (`build_lobby_columns`, same fan-out) | +10 to +20 |
| one `/matchhistory` | +11 |

Two announcements in a cycle plus one `/matchhistory` overruns the window, and
`RateLimiter.acquire()` responds by **blocking the calling thread**, which
stretches poll cycles rather than failing. Any feature that fans out per-account
Riot calls on demand is a poll-latency regression.

**Design consequence:** new read features should be served from state the poller
has *already* paid for (`match_tracker_state.json`), not from fresh fan-out. This
single observation drives the ranking of features in §5.

**3.2 The command path currently blocks the event loop** (see D3). Any new
command must not repeat that; the fix in §6.1 is a prerequisite for the feature
work, not an optional cleanup.

**3.3 One announcement channel.** `settings.announcement_channel_id` is a single
global int. Multi-guild anything requires §5.4 first.

**3.4 No database, no new dependencies.** `requirements.txt` is three lines.
`sqlite3` is stdlib and is the only exception I would propose (§5.5).

**3.5 matplotlib is optional.** `charts.py` guards every use with
`MATPLOTLIB_AVAILABLE` and the announcement degrades to text. Any new chart must
follow that.

---

## 4. Defects found

Ordered by impact. Each is independently fixable and each should land with a
regression test.

### D1 — Back-to-back ranked games corrupt the LP baseline *(highest impact)*

[tracker.py:226-232](bot_app/tracker.py#L226)

```python
announcement = format_match(match, players)
if announcement is None:
    continue                      # <-- pending_ranks is discarded here
announcements.append(announcement)
for player_state, ranked_queue, snapshot in pending_ranks:
    player_state.ranks[ranked_queue] = snapshot
```

When a player finishes two ranked games in one poll window, the code at
[tracker.py:201-214](bot_app/tracker.py#L201) intentionally suppresses the LP
number (it can't be attributed) and `continue`s **before appending to `players`**.
`players` is therefore empty, `format_match` returns `None` at
[announce.py:137](bot_app/announce.py#L137), and the `continue` above drops
`pending_ranks` — so the baseline resync the comments promise never happens.

Consequences, both real:
- two back-to-back games are announced *not at all*, not even without an LP figure;
- the stored baseline stays N games stale, so the **next** single game announces a
  bogus LP swing (the sum of three games' movement).

Fix: commit `pending_ranks` unconditionally, before the `announcement is None`
check, and announce every ranked game — attaching `lp_change` only when
`is_only_game`. That turns "silently drop two games and lie about the third" into
"announce all three, with LP on the ones we can attribute".

### D2 — With no `GUILD_IDS`, no command registers at all

[commands/shared.py:19](bot_app/commands/shared.py#L19)

```python
GUILD_IDS = list(get_settings().guild_ids)   # () -> []
```

py-cord selects global commands with `guild_ids is None`
(`discord/bot.py:766`, verified in `.venv`). An empty **list** is not `None`, so
every command is treated as guild-scoped to zero guilds and is never registered
anywhere. The README documents `GUILD_IDS` as optional, so the documented
configuration is the broken one.

Fix: `GUILD_IDS = list(get_settings().guild_ids) or None`.

### D3 — Every slash command blocks the event loop before it defers

`resolve_target` ([shared.py:59](bot_app/commands/shared.py#L59)) is called
synchronously from all seven command handlers. It performs:

- up to **three sequential** `account-v1` lookups when no server is given
  ([shared.py:94](bot_app/commands/shared.py#L94) tries `NA1`, `EUW1`, `KR`,
  passing the platform code as the tagline);
- 2–3 `data.json` parses (`load_accounts` is called once per branch, uncached).

Then `Target.riot_id` is a **property that issues an HTTP call**
([shared.py:38](bot_app/commands/shared.py#L38)) and is read inside f-strings all
over the command modules, and `set_player_author` adds a `summoner-v4` call plus
a Data Dragon version fetch. None of it runs in a thread. During a cold lookup
the whole bot — including heartbeats — stalls for seconds.

Fix: §6.1.

### D4 — Smite detection is dead for finished matches

[positions.py:129](bot_app/positions.py#L129) reads `spell1Id`/`spell2Id`. Those
fields exist in **spectator-v5** only. match-v5 uses `summoner1Id`/`summoner2Id`
— confirmed against `json/samples/sample_match.json`:

```
{'individualPosition': 'TOP', 'lane': 'JUNGLE', 'summoner1Id': 4, 'summoner2Id': 14, ...}
```

So for `build_match_columns` the Smite signal never fires, and the module
docstring's claim that Smite overrides Riot's mislabelled `teamPosition` is false
on exactly the path where `teamPosition` exists. The same sample shows why it
matters: `lane` says JUNGLE while `teamPosition` says TOP.

Fix: read both key pairs in `assign_team_positions`.

### D5 — A match that can't be announced is still marked as seen

[tracker.py:238-242](bot_app/tracker.py#L238) marks every id whose *detail fetch*
succeeded. But `format_match` also returns `None` for a match with no
`gameEndTimestamp` ([announce.py:99](bot_app/announce.py#L99)). A match id that
appears before its payload is finalised is fetched, marked seen, and never
announced. Fix: mark seen only when announced, or when definitively
unannounceable (wrong queue), and let the 20-match lookback retry the rest.

### D6 — Failed lookups are cached as if they were answers

`TTLCache.get_or_set` stores whatever `produce()` returns, and `riot_id`'s
producer returns `None` on `RiotAPIError` ([riot.py:240-254](bot_app/riot.py#L240)).
With a 6-hour TTL, one transient failure pins a player to "Unknown player" /
`"-"` for six hours. Same shape for `summoner` (10 min) and `league_entries`
(60 s). Fix: don't cache negative results, or cache them with a short
independent TTL (~30 s).

### D7 — pyplot's global state is used from concurrent threads

`charts.py:104` and `charts.py:248` use the `plt` module-level API from
`asyncio.to_thread` workers. The match poller and the guest poller are separate
`tasks.loop`s and both reach `build_damage_chart` concurrently. pyplot's figure
manager is not thread-safe. Fix: use the object API
(`matplotlib.figure.Figure` + `FigureCanvasAgg`), which needs no global state,
or serialise renders behind a lock.

### D8 — `live_game_state.json` never shrinks

[tracker.py:262](bot_app/tracker.py#L262) seeds `current = dict(previous)` and
only ever adds keys. An account removed from `data.json` keeps its entry forever,
and that stale value keeps participating in the `key in previous.values()`
dedupe check. Fix: prune to the current roster on each write.

### D9 — Smaller items

| # | Where | Issue |
| --- | --- | --- |
| D9.1 | [recap.py:56](bot_app/commands/match/recap.py#L56) | `p.get("teamPosition", "").upper()` raises if the key exists with value `None` — `.get` default only covers a missing key |
| D9.2 | [emoji.py:31](bot_app/emoji.py#L31) | index invalidated on `len(emojis)` change only; a rename, or one added + one removed, leaves it stale for the process lifetime |
| D9.3 | [shared.py:19](bot_app/commands/shared.py#L19) | `get_settings()` at *import* time means no command module can be imported without full valid secrets — that is why there are zero command tests |
| D9.4 | [main.py:30](main.py#L30) | `intents.message_content = True` is a privileged intent the bot never uses (slash commands only); it fails startup unless enabled in the dev portal |
| D9.5 | [main.py:65-77](main.py#L65) | all three pollers share one interval and start in the same tick, so their bursts align against the 100/2 min window instead of interleaving |
| D9.6 | [guest_tracker.py:25-33](bot_app/guest_tracker.py#L25) | a real player's PUUID and in-game name are hardcoded in source; belongs in config |
| D9.7 | [store.py:47](bot_app/store.py#L47) | `write_json` doesn't `fsync` before `replace`, so the rename can be durable while the contents aren't |
| D9.8 | [config.py:38](bot_app/config.py#L38) | `tft_api_key` is parsed, documented in the README, and used nowhere |
| D9.9 | — | dead code: `ranks.fetch_rank`, `render.winrate_text`, `queues.is_ranked`, `ddragon.champion_name` / `champion_internal_id` / `champion_key_for_name`, `store.puuid_for_discord_id`, `store.server_for_puuid`, `services.players.target_from_options` |
| D9.10 | [store.py:87](bot_app/store.py#L87) | `load_accounts()` re-parses `data.json` on every call; the mtime cache next to it covers only the puuid set |

---

## 5. Proposed features

### F1 — `/track`, `/untrack`, `/accounts` *(foundation; do this first)*

**Why.** The bot is an account tracker whose roster can only be edited by hand.
`save_accounts` exists and is reachable from exactly one code path
(`/updateriotids`), which is the shape of a feature that was designed and never
finished. Every other feature below is more valuable with a self-service roster.

**Interface**

| Command | Access | Options |
| --- | --- | --- |
| `/track` | everyone (self); owner may pass `user` | `summoner`, `tag`, `server`, `user?` |
| `/untrack` | everyone (self); owner may pass `user` | `user?` |
| `/accounts` | everyone | — (public, read-only version of owner-only `/data`) |

**Store API change.** `load()` + mutate + `save()` is not atomic, and two
concurrent `/track` calls would lose one. Add to `store.py`:

```python
def update_accounts(mutate: Callable[[dict[str, Account]], None]) -> dict[str, Account]:
    """Read-modify-write data.json atomically, then invalidate the puuid cache."""
```

Implementation note: `write_json` already takes `_write_lock`, so `_write_lock`
must become an `RLock` (or `update_accounts` must call an unlocked
`_write_json_locked` helper). Also invalidate `_tracked_puuid_cache` explicitly
rather than relying on mtime — two writes inside one mtime tick would otherwise
serve a stale set.

**The important edge case: don't spam on link.** A freshly tracked account has no
`PlayerState`, so the next poll sees all 20 lookback matches as new and announces
every ranked one. `/track` must therefore, in the same operation:

1. `client.match_ids(puuid, server, count=MATCH_LOOKBACK)` and seed
   `PlayerState.matches` with all of them;
2. `fetch_ranks(puuid, server)` and seed `PlayerState.ranks`, so the first
   announced game carries a correct LP delta rather than none;
3. write `data.json` and `match_tracker_state.json` together, accepting that they
   are two atomic writes and ordering them state-first (a state file with no
   account is harmless; an account with no state spams).

**Other edge cases**

- Riot id not found on the given server → reuse `not_found_embed`.
- PUUID already tracked by a *different* Discord id → refuse with the existing
  owner's mention, unless the caller is the owner.
- Caller already tracked → replace (the file is keyed by `discord_id`, so one
  account per user; multi-account is a schema change, deferred).
- `/untrack` must also delete the tracker-state entry and the
  `live_game_state.json` entry, otherwise a re-link inherits stale baselines.
- Roster growth changes §3.1 arithmetic linearly: at 30 accounts idle polling is
  ~61/100 and a single announcement overruns. Add a configurable roster cap
  (`max_tracked_accounts`, default 25) and refuse past it with a clear message.
- `/accounts` with 16+ entries approaches the 1024-char field limit → paginate
  (§6.3).

**Tests** (`tests/test_account_registry.py`, all network-free, tmpdir-patched
paths, fake client): add/replace/remove round-trip; duplicate-PUUID rejection;
seeding writes exactly the lookback ids; untrack purges all three files;
concurrent `update_accounts` from two threads loses nothing; roster cap.

---

### F2 — LP history, `/today`, and `/lpgraph` *(highest value per Riot request)*

**Why.** The poller already computes "this player moved from X to Y LP in queue Q
after match M" and then throws the intermediate away, keeping only the newest
snapshot. Persisting it costs **zero additional Riot calls** and unlocks a whole
class of commands that are otherwise unaffordable under §3.1.

**Schema** — extend `PlayerState` (back-compatible, same pattern as the existing
legacy-list handling in `PlayerState.from_json`):

```json
"409951632491806721": {
  "matches": ["NA1_5619445010", "..."],
  "solo": {"tier": "EMERALD", "rank": "II", "lp": 47, "wins": 88, "losses": 81},
  "flex": null,
  "history": {
    "420": [
      {"t": 1755100000, "v": 2447, "d": 18,   "m": "NA1_5619445010", "w": true},
      {"t": 1755103600, "v": 2429, "d": -18,  "m": "NA1_5619445011", "w": false},
      {"t": 1755110000, "v": 2470, "d": null, "m": null,             "w": null}
    ]
  }
}
```

- `v` is `rank_value(tier, division, lp)` — the flattened scale that already
  exists in `ranks.py` and survives promotions.
- `d: null` marks a **resync point** (multi-game poll, D1): the value is known,
  the delta is not attributable. Charts draw those segments dashed; `/today` sums
  only non-null deltas and reports "+N LP over M games (2 unattributed)".
- Cap at 500 entries per queue via the existing `dedupe_tail` idiom.
- `from_json` treats a missing `history` key as `{}`; `to_json` always writes it.
  Old files load, new files are readable by old code (unknown key ignored).

**Write point.** Exactly where D1's fix commits the baseline — one function,
`PlayerState.record_rank(queue_id, snapshot, *, match_id, won)`, which computes
`d` from the previous entry's `v` and appends. Keeping the write in one place is
what makes it testable without touching the network.

**Commands**

- `/today [user|summoner] [queue]` — net LP, W–L, games played, biggest gain and
  loss, since local midnight. **Zero Riot calls.**
- `/lpgraph [user|summoner] [queue] [days=30]` — step plot of `v` over time with
  tier bands drawn from `SUB_MASTER_TIERS` and `value_to_rank`, styled with the
  existing `charts.py` Discord palette. **Zero Riot calls.**
- Optional follow-on: a scheduled daily digest posting every tracked player's net
  LP, reusing the same reader.

**Edge cases**

- **Timezone.** "Today" in UTC is wrong for a US group. Add a `timezone` setting
  (IANA string, default `"UTC"`), resolved with stdlib `zoneinfo`. Invalid value
  → `ConfigError` at startup, consistent with the rest of `config.py`.
- **Season reset** produces a huge negative `d`. Detect (drop below the tier floor
  combined with a wins/losses reset) and start a new series rather than plotting a
  cliff.
- **Apex demotion.** Falling out of Master crosses `APEX_THRESHOLD` in a
  continuous scale, so the arithmetic holds; the label does not. Render apex
  points with the Master+ label from `average_rank_text`.
- **Unranked → placement complete**: first entry has `d: null` by definition.
- **matplotlib absent** → `/lpgraph` responds with the text summary `/today`
  produces, plus a footer, matching the `/selftest` precedent.
- **Empty history** (new account, or all matches predate the feature) → "no LP
  history recorded yet; it starts filling from your next ranked game."

**Tests** (`tests/test_lp_history.py`): append + delta arithmetic across a
promotion and a demotion; `d: null` on resync; 500-entry cap keeps the newest;
legacy state without `history` loads and round-trips; season-reset detection;
`/today` window boundaries with a frozen clock and a non-UTC timezone.

---

### F3 — `/leaderboard`

**Why.** The obvious command for a group bot, and under §3.1 it is only
affordable if it reads stored state. It does: `data.json` + the `solo`/`flex`
snapshots in `match_tracker_state.json` give every tracked player's rank for
**zero Riot calls**.

**Design.** Join the two files, sort by `RankSnapshot.value` descending
(`None` last), render name / rank / W–L / win rate with the existing `rank_text`
and `average_rank_text`, and page 10 per embed (§6.3). Options: `queue`
(solo/flex, default solo) and `refresh` (owner-only; runs
`update_all_rank_snapshots` in a thread — that's 16 league calls, i.e. 16% of the
two-minute budget, hence the gate).

**Staleness is the design problem.** A snapshot is only as fresh as the player's
last announced game; someone who hasn't played since the last restart shows a
weeks-old rank with no indication. Fix by adding `updated_at` (epoch seconds) to
`RankSnapshot.to_state()` / `from_state()` — back-compatible, missing → `None` —
and footer the embed with the oldest snapshot's age.

**Edge cases:** empty roster; everyone unranked; ties (break by games played);
apex tiers sharing an LP pool (already handled by `rank_value`); riot ids stale
since the last `/updateriotids` (footer note); pagination when the roster is
under one page (hide the buttons).

**Tests** (`tests/test_leaderboard.py`): ordering across tiers/apex/unranked;
tie-break; page boundaries at exactly 10 and 11 entries; empty roster; staleness
label with a frozen clock.

---

### F4 — Per-guild announcement routing

**Why.** `announcement_channel_id` is one global int, so the bot can only ever
serve one server. This is the blocker for using it anywhere else, and it also
gates any "announce only to servers where this player is a member" behaviour.

**Design.** New `json/guilds.json`: `{guild_id: {"announcement_channel_id": int}}`,
read through the same `read_json`/`write_json` helpers. New `/setchannel`
(requires Manage Server), plus `/unsetchannel`. `announce.resolve_announcement_channel`
becomes `resolve_announcement_channels(bot, announcement) -> list[channel]`.

**The interesting part** is *which* guilds get a given announcement. Broadcasting
every tracked player to every guild is wrong. Route by membership: for each
configured guild, include it if any `highlight_puuids` maps back to a
`discord_id` that `guild.get_member()` resolves. Requires no new intent —
`get_member` uses the member cache, and falls back to `fetch_member` off the
event loop when cold.

**Edge cases:** guild configured then bot removed (drop the entry on
`on_guild_remove`); channel deleted or permissions revoked (log once, don't
retry every poll — track a failure count and disable after N); the legacy global
`announcement_channel_id` must stay as the fallback so the current deployment
keeps working with no config change; a `discord.File` **can only be sent once**,
so multi-channel publishing must rebuild the chart attachment per channel (or
buffer the PNG bytes and construct a fresh `discord.File` per send — cheaper, do
that).

**Tests** (`tests/test_guild_routing.py`): routing picks only guilds containing a
tracked member; falls back to the global channel when nothing is configured;
per-channel `discord.File` instances are distinct objects.

---

### F5 — Match cache and `/championstats` *(later; largest change)*

**Why.** Finished match payloads are immutable, and the bot re-fetches the same
ones constantly (`/matchhistory` = 11 requests every invocation; `/recap` and
`/timeline` refetch what the poller just had). A cache both removes that traffic
and makes aggregate features affordable for the first time.

**Design.** `json/matches.sqlite` via stdlib `sqlite3` — no new dependency, and it
gives indexed queries that a directory of JSON files does not:
`matches(match_id PK, platform, queue_id, game_end_ms, payload JSON)` plus
`participants(match_id, puuid, champion_id, win, kills, deaths, assists, ...)`
denormalised for queries. Written by the poller (which already has the payload in
hand) and by any command that fetches a match. All access off the event loop;
`check_same_thread=False` with one connection guarded by a lock, or a
connection-per-thread factory.

Unlocks `/championstats [player] [champion]` (games, W–L, average KDA/CS/damage
per champion), `/duo [a] [b]` (win rate when queued together), and a
`/matchhistory` that costs ~0 requests on repeat calls.

**Edge cases:** cache size growth (prune matches older than N days, and never
cache in-progress payloads — the D5 condition); schema versioning via
`PRAGMA user_version`; a corrupt DB must degrade to live fetches, not crash;
`json/*.sqlite` must be added to `.gitignore`.

**Tests** (`tests/test_match_cache.py`): insert/read round-trip on an in-memory
DB; unfinished matches rejected; prune keeps the newest; aggregate queries
against a small fixture set; corrupt-file fallback.

---

### Feature ranking

| | Feature | Value | Riot cost | Risk | Order |
| --- | --- | --- | --- | --- | --- |
| F1 | `/track` etc. | high (unblocks everything) | one-off per link | low | 1 |
| F2 | LP history + `/today` + `/lpgraph` | high | **zero** | low–medium (schema) | 2 |
| F3 | `/leaderboard` | high | **zero** | low | 3 |
| F4 | per-guild routing | medium (unblocks reuse) | zero | medium | 4 |
| F5 | match cache + stats | medium–high | negative (saves calls) | high | 5 |

---

## 6. Cross-cutting changes the features depend on

### 6.1 Get blocking I/O off the event loop (prerequisite for all of F1–F5)

Make `Target` carry pre-resolved data instead of hiding HTTP behind a property:

```python
@dataclass(frozen=True)
class Target:
    puuid: str
    server: str
    riot_id: str = "Unknown player"   # resolved once, in the worker thread
    icon_url: str | None = None       # ditto

async def target_for(ctx, server, summoner, tag, user) -> Target | None:
    return await asyncio.to_thread(resolve_target, ctx, server, summoner, tag, user)
```

`resolve_target` stays synchronous (the poller and `/selftest` use it off-thread
already) and gains the riot-id and icon resolution its callers were each doing
separately. `set_player_author` becomes pure. Seven call sites change; the
blocking property and the duplicate lookups both disappear.

While in there: fix the three-server guess at `shared.py:94` to run its
candidates concurrently, or drop it — passing a platform code as a tagline
succeeds only for players whose tag happens to be a platform code, and a name
collision silently returns the wrong region's account.

### 6.2 Make config injectable (prerequisite for command tests)

`GUILD_IDS = list(get_settings().guild_ids)` at import time (D9.3) is why zero
command modules are tested. Read guild ids lazily inside the decorator call, or
supply a test default when `ConfigError` is raised at import.

### 6.3 Extract the paginator

`MasteryPaginator` ([mastery.py:52](bot_app/commands/mastery.py#L52)) is a
complete, well-behaved paginator — author check, timeout disable, button
sync — welded to mastery entries. F1 (`/accounts`), F3 (`/leaderboard`), and F5
all need the same thing. Extract `render/paginator.py` with a
`Paginator(items, page_size, render_page: Callable[[list[T], int], Embed])`, and
reimplement `MasteryPaginator` on it (its tests should not change).

### 6.4 Resolve the shim question

Recommendation: **delete** `bot_app/domain/`, `bot_app/services/`,
`bot_app/presentation/`, `bot_app/repositories/`, and the eight one-line command
re-export modules; update the ~12 import sites. Replace
`tests/test_architecture.py` with an AST-based test that walks `bot_app/` and
asserts the real invariants:

- no module under `bot_app/` outside `commands/`, `render.py`, `charts.py`,
  `announce.py`, `emoji.py` imports `discord`;
- nothing outside `riot.py`, `ddragon.py`, and `commands/ai.py` imports `requests`;
- `routing`, `queues`, `positions`, `timeline`, `history` import nothing from
  `bot_app` except each other.

That test would actually fail on a violation, which the current one cannot.

### 6.5 Documentation debt (per CLAUDE.md)

Every public command added in F1–F5 must be added to the `_COMMANDS` table in
[info/commands.py](bot_app/commands/info/commands.py) with description and usage;
owner-only ones must stay out. `/track`, `/untrack`, `/accounts`, `/today`,
`/lpgraph`, `/leaderboard`, `/championstats`, `/duo` are public; `/setchannel` is
permission-gated but public-visible. README's layout table and settings list need
updating for `timezone`, `max_tracked_accounts`, and the removal of the shim
packages.

---

## 7. Implementation sequence

Each phase ends green on `python3 -m unittest discover -s tests -t .` and is
independently shippable.

**Phase 0 — correctness (no new features).**
D1 (baseline resync + announce back-to-back games), D2 (`GUILD_IDS or None`),
D4 (`summoner1Id`), D5 (mark-seen condition), D6 (negative caching), D8 (prune
live state), D9.1, D9.4. Regression test per fix.
*Acceptance:* new `tests/test_tracker.py` reproduces D1 (fails before, passes
after); the bot starts and registers commands with `guild_ids` unset.

**Phase 1 — plumbing.**
§6.1 async boundary, §6.2 injectable config, §6.3 paginator extraction,
§6.4 shim removal + real architecture test, D7 (figure API), D9.5 (stagger
poller start).
*Acceptance:* no `requests`/`get_client` call reachable from a coroutine without
`to_thread`, enforced by the new architecture test.

**Phase 2 — F1 `/track` / `/untrack` / `/accounts`.**
`store.update_accounts`, seeding on link, roster cap, `/commands` entry.
*Acceptance:* linking a fresh account produces zero backfill announcements on the
next poll; unlinking purges all three state files.

**Phase 3 — F2 LP history.**
Schema extension + `record_rank` + `/today` + `/lpgraph`, `timezone` setting.
*Acceptance:* an existing `match_tracker_state.json` loads unchanged and gains
history from the next announced game onward.

**Phase 4 — F3 `/leaderboard`.** Plus `updated_at` on `RankSnapshot`.

**Phase 5 — F4 per-guild routing.** Global channel remains the fallback.

**Phase 6 — F5 match cache.** Behind a `match_cache_enabled` setting so it can be
switched off if sqlite misbehaves in this deployment.

---

## 8. Test plan

Current coverage is 100 tests over the pure domain only: routing, ranks,
positions, timeline, ddragon, store, config, history, and one gold embed. Nothing
covers the poller, the Riot client, the command layer, or the charts — which is
where every defect in §4 lives.

New files, all network-free:

| File | Covers | Key cases |
| --- | --- | --- |
| `tests/test_tracker.py` | `collect_new_matches`, `collect_new_live_games` | **D1 regression**; D5 (unfinished match not marked seen); D8 (state pruned); one match shared by two tracked players announced once; chronological ordering; failed detail fetch retried next poll |
| `tests/test_riot_client.py` | `RateLimiter`, `TTLCache`, `_get` | sliding windows with a fake clock; retry budget exhaustion raises `RiotAPIError` rather than looping; malformed `Retry-After`; `none_on_404`; **D6** negative-result policy |
| `tests/test_commands_shared.py` | `resolve_target` | branch table (mention / caller / explicit riot id / combined `Name#Tag` / platform code in the tag field); stored server wins over the option; not-found path |
| `tests/test_account_registry.py` | F1 | see §5 F1 |
| `tests/test_lp_history.py` | F2 | see §5 F2 |
| `tests/test_leaderboard.py` | F3 | see §5 F3 |
| `tests/test_guild_routing.py` | F4 | see §5 F4 |
| `tests/test_match_cache.py` | F5 | see §5 F5 |
| `tests/test_charts.py` | `charts.py` | `skipUnless(MATPLOTLIB_AVAILABLE)`; damage chart returns a `discord.File`; kill map returns `None` for an unknown `mapId` and for a player with no kills or deaths; concurrent renders from two threads (D7) |
| `tests/test_architecture.py` | replaced | AST import-boundary rules from §6.4 |

Additions to existing files:

- `tests/test_positions.py`: a match-v5 style participant with `summoner1Id: 11`
  is assigned Jungle (**D4 regression**); the sample's `lane`/`teamPosition`
  disagreement resolves to one role per team.
- `tests/test_store.py`: `update_accounts` atomicity under two threads; puuid
  cache invalidated after a write within the same mtime tick.

Test infrastructure needed: a `FakeRiotClient` fixture (dict-driven responses,
call counting so rate-budget regressions are visible), a frozen-clock helper, and
a `tmp_state()` context manager patching the four `store` path constants — the
pattern `tests/test_store.py:62` already uses inline, hoisted into
`tests/support.py`.

---

## 9. Open questions

1. **One account per Discord user, or several?** `data.json` is keyed by
   `discord_id`, so multi-account (smurfs) needs a schema change. F1 assumes 1:1;
   say so now if that's wrong, because the migration is cheaper before F2 stores
   history keyed off the same id.
2. **Should back-to-back ranked games be announced without an LP figure** (my
   proposal in D1), or stay silent as the current code intends? The current code
   does neither correctly.
3. **Is the guest tracker still wanted?** It hardcodes a specific person's PUUID
   and in-game name and costs one poll slot per cycle. If yes, it should move to
   config (D9.6); if no, deleting it frees budget.
4. **Roster ceiling.** At 25–30 accounts the dev key's 100/2 min stops being
   sufficient for the current poll design. Is a production key available, or
   should the poller move to a staggered schedule (poll a third of the roster per
   cycle, keeping live-game detection at full rate)?
5. **`tft_api_key`** — is TFT support planned, or should the setting be removed?

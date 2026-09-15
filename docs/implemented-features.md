# Implemented feature reference

Current behavior supporting the summaries in [AGENTS.md](../AGENTS.md) and
[CLAUDE.md](../CLAUDE.md). Read the relevant section before changing a subsystem.
Maintain this reference with behavior changes; keep historical debugging accounts
in commits. The protected hard rules remain in the root instruction files.

## League announcements and persistent controls

### Completed matches

- `/match`, automatic announcements, and `/selftest` use the shared completed-match
  pipeline in `bot_app/announce.py`. It has no queue filter: all completed games
  are eligible, including non-ranked, rotating, custom, and unknown queue ids.
  Only `queues.RANKED_QUEUE_IDS` (Solo/Duo 420, Flex 440) trigger ranked LP lookups
  and rank-history writes. Processing is chronological with durable deduplication.
  Spectator lobby ids supplement the by-PUUID Match-V5 history for rotating modes
  such as ARAM: Mayhem, whose completed games can be omitted from that history.
- Embeds show team columns, tracked-player highlights, duration, and a relative
  end timestamp. Arena queues 1740 (Bravery 3v3) and 1750 (3v3) use team-aware
  layouts with concise `Team 1`, `Team 2`, etc. headings. Completed-match
  Players, Ratings, and Items views prefix each team with a distinct color marker;
  live-game announcements keep the headings uncolored.
  Rank displays include Solo/Duo win rates and replace player names, omitting the
  KDA suffix from centered-dot-separated labels while ranks occupy that column.
- Ranked teammate LP is snapshotted when a shared live lobby is first announced.
  When that lobby later surfaces through a tracked account's completed-match poll,
  the shared match announcement includes the teammate's current rank and LP change.
  Teammates still never initiate match polling or announcements themselves.
- Display choices are Players, Ranked Solo, Ratings, Items, and Mastery; Flex
  replaces Ranked Solo with Ranked Flex and adds Solo Rank. Mastery reuses the
  same player/champion data from an earlier live announcement when available,
  otherwise fetching on demand.
- Chart choices are Damage Done, Gold Graph, Gold Difference Graph, Jungle
  Proximity, Damage Taken, Healing and Shielding, and Vision Score. Display changes
  retain the selected chart, including Items and Ratings; each mode retains
  Display/Chart controls. Jungle Proximity changes only the chart, preserving the
  selected columns, and shares `jungle_proximity_render` with the standalone command.
- Items render blue/red name/items columns with six core icon slots (⬛ for empty
  slots) followed immediately by the trinket. They retain the base win/loss color.
  The final inventory is authoritative. If an ADC/BOTTOM boot is absent, timeline
  purchase history supplies the latest still-held boot: sales and undo events clear
  it, while a bot-lane role-quest `ITEM_DESTROYED` preserves it because the boot moves
  into a dedicated quest slot. Detection uses Data Dragon metadata plus conservative
  IDs, including boots `3008`/`3172`; Manamune `3004` is not a boot. Missing metadata
  or emotes still allow a name or 🥾 fallback.

### Live games and lifecycle

- `/livegame` and automatic lobby announcements share team columns, player/champion
  rows, inferred positions, ranks/win rates, duration, and a relative start timestamp.
  One automatic announcement is emitted per shared lobby. The persistent Display
  dropdown replaces names with ranks or champion mastery; Flex offers separate
  Flex Rank and Solo Rank choices. Ranks never appear in a separate bottom block.
- Automatic live posts are recorded by `platform:gameId` through
  `store.remember_live_game_message` in private runtime storage.
  `tracker.poll_and_announce` deletes the live post immediately before the completed
  announcement. `poll_live_games_and_announce` also sweeps ended or dodged lobbies
  through `announce.delete_live_game_messages`. `collect_new_live_games` retires
  stored keys when a successful `active_game` response confirms no game; a failed
  request is not evidence that a lobby ended. `/livegame` responses are never
  recorded for this cleanup or auto-deleted.
  Newly ended live posts remain recorded for one additional polling interval so
  the completed-match poller can retry their direct Match-V5 id before cleanup.
- Announcement channels are configured per guild with a mandatory global fallback,
  guild membership routing, cached batch lookups, and conservative treatment of
  transient Discord failures.

### Persistent interactions

- Match/live-game, `/champ`, `/coachless`, `/champstats`, and `/counterstats`
  controls use stable persistent views.
  Async startup restores them from incrementally updated SQLite records even when
  slash-command sync fails. Existing JSON view state is imported on first use.
  `/match` and `/livegame` persist through the same path as automatic announcements.
  Champion-history state stores its compact aggregate report; counter-history state
  stores only query inputs and reloads cached matches lazily after acknowledgement.
- Riot calls and chart rendering happen after acknowledgement. Immutable timelines
  and bounded chart bytes are cached, and display-only changes preserve unchanged
  chart attachments.

## Teamfight Tactics

### Registration and polling

- TFT credentials resolve distinct PUUIDs, so accounts live in private runtime storage
  with separate tracker and deduplication state. Owner-only `/add` resolves League
  and TFT identities with their respective keys and seeds both. A partial TFT
  failure preserves the League result and points to `/tftadd`, which can also link
  a different TFT Riot ID.
- Owner-only `/tftupdate` processes all tracked League accounts: resolve the current
  Riot ID from the League PUUID, then refresh the TFT PUUID with the TFT key. Failures
  are reported per account without stopping the sweep. Matching stored TFT PUUIDs
  reuse settled platforms and seeded history, preserving remembered match IDs.
  The deferred reply shows progress, and concurrent sweeps are refused.
- TFT Match-V1 polling seeds recent history on first use rather than replaying it.
  Every successfully fetched match is remembered even if it cannot be formatted;
  failed fetches remain pending for retry. Separate live deduplication state is
  retained for the disabled live collector.
- TFT live polling and `/tftlivegame` are disabled because Spectator-TFT-V5 returns
  403 for the configured key. `collect_new_tft_live_games` and shared live renderers
  remain available for re-enabling in `poll_live_games_and_announce` after access
  is authorized. The retained renderer supports up to eight players, highlights
  tracked participants, and shows ranks (`Unranked` for no rank, `—` on failure).

### Completed matches and commands

- `/tftmatch` uses the same completed TFT renderer and persistent controls as
  automatic announcements. Columns are Place, the selected Display column, and
  Level. Players is the default; Ranks fetches Ranked TFT standings on demand;
  Traits shows active synergies. Eliminations and damage are not embed columns.
  Tracked participants present in the game have bold Place/Player/Level cells;
  replacement rank/trait cells remain unbolded.
- Ranked TFT (1100) LP deltas appear only on announcement lines, not in a column,
  and are attributed only when an account has exactly one new ranked game in that
  poll. Last-seen ranks live in TFT tracker state. Startup calls
  `tracker.update_all_tft_rank_snapshots` after League rank initialization to seed
  missing baselines without overwriting ranks advanced by a real game.
- `game_datetime` is already the end timestamp; do not add game length. Renderers
  accept Riot's snake_case/camelCase forms for queue, game length, eliminations,
  and player damage, plus trait `style`/`tier_current`. Hosting platform comes from
  `PLATFORM_gameid`. Registered names come from the TFT registry; other names use
  spectator `riotId` then a regionally correct account-v1 lookup.
- `/tftmatchhistory` shows up to ten placements with level, eliminations, mode,
  optional queue filter, and a top-four/bottom-four record. `/match`, `/livegame`,
  and `/matchhistory` remain League-only. `/profile` appends a TFT standing only
  when a target is linked and `RiotClient.tft_league_entries` returns a rank;
  absent links/ranks or lookup failures omit the line.

## Accounts and player resolution

- Player-oriented commands use one optional `username` for a League name or
  Discord mention and optional `server`, resolved by `shared.resolve_username` /
  `target_for`. Omitted players default to the caller's linked account. Commands
  requiring a match default to that account's latest match.
- Riot-ID resolution sweeps NA1/EUW1/KR via `routing.lookup_platforms`; `server`
  selects the first candidate. Account-v1 is regionally routed but cannot establish
  the hosting platform, so `RiotClient.home_platform` probes summoner-v4 across
  candidates. Stored tracked-account servers take precedence. `/add` uses the same
  resolution. Region-only selectors such as `/champ` and `/rotation` are separate.
- Lookup failures name searched platforms through `shared.searched_servers` and
  echo supplied options through `shared.supplied_options_text`, with non-pinging
  mentions. If nothing was supplied, the message identifies the default-account
  lookup. Successful player views omit a server footnote; server-scoped champion
  statistics put the resolved platform in the title.
- Account tools support tracking, refresh, reassignment, duplicate prevention,
  roster limits, and removal cleanup. Owner-only `/accounts` paginates all tracked
  accounts (linked mentions or `*(unlinked)*`) followed by Teammates, ephemerally.
- Owner-only `/add` accepts optional `user` and `teammate` (false by default).
  A summoner without a tag defaults to `NA1`. Normal tracking without a Discord
  user uses a synthetic 18-digit sentinel key shared by League/TFT, reusing it
  when re-adding an existing PUUID. Unlinked accounts poll normally.
- Teammates live in private runtime storage, keyed by PUUID with optional `discordId`,
  and are cached through `teammate_puuids()`. They resolve through player commands,
  including caller defaults and mentions via `shared._linked_target`; Riot-ID
  lookups use their stored server. They are never polled for games and cannot
  initiate announcements. In a ranked live lobby shared with a tracked account,
  their current queue rank is saved as a pre-game baseline; the completed-match
  announcement then shows their rank and attributable LP change.
  `render.build_match_columns` highlights them only when a real tracked player is
  in the same lobby. TFT highlighting is unaffected.
- `/add` with a `user` but no `summoner` switches an existing registration:
  `teammate:true` demotes League/TFT tracking via `_demote_to_teammate`,
  `untrack_account`, and `untrack_tft_account`; false promotes via
  `_promote_from_teammate` / `_track_both_accounts` and removes the teammate entry.
  A PUUID belongs to one registry. Teammate removal is a direct registry-file edit.
- Match `position` slots 1–5 are blue Top/Jungle/Mid/ADC/Support; 6–10 are red.
  `/match`, `/timeline`, `/laning` select from the requested match. `/mastery`,
  `/matchhistory`, `/matchlist`, `/champstats`, `/counterstats`, and the primary
  `/duo` side select from the lookup account's latest match. Match/timeline/self-test
  accept ordinal references 1–20.

## Commands and champion statistics

### Lookups, builds, and discovery

- `/profile` shows level, Solo/Duo and Flex ranks/records, and top masteries.
  Commands also cover PUUID, mastery, timelines, rotation, status, match lists,
  and OP.GG links. Profile/build URLs use `routing.opgg_url` /
  `opgg_champion_url` with `hl=en_US` to stay in English.
- `/matchhistory` filters by champion and mode (Normal Draft, Ranked Flex,
  Ranked Solo/Duo, Arena, ARAM), shows up to ten matching results, and totals W/L.
  `/duo` uses the same mode autocomplete and team-aware cached results.
- `/champ` uses live OP.GG tier, win/pick/ban rates, patch, skill order, grouped
  primary/secondary/shard runes, and item builds. It defaults to global and the
  most-picked available role, with other-role buttons. Icon-only rune/item rows
  retain configured shards, duplicate item quantities, and quantity badges.
  Three to five situational suggestions come from fourth–sixth item choices,
  exclude core items, and exclude Mejai's.
- `/trends role:<role>` scans every champion on OP.GG's selected global Ranked
  Solo/Duo role page and reads each individual Trends tab. It ranks the ten
  highest win rates at every game-length timestamp and exposes all returned
  ranges through persistent buttons (currently under 25, 25–30, 30–35, 35–40,
  and 40+ minutes). Champion rows link back to their OP.GG Trends source.
- `/coachless` fetches the Coachless JSON API directly, caches by champion/role,
  and resolves IDs through Data Dragon. It shows rune, spell, starter,
  first/second/third/fourth+ item, and boots WPA/purchase tables. Bravery selects
  the highest-WPA option in each category among picks below 1% pick rate.
- `/help` provides categorized purpose-only lines and detailed `command:<name>`
  Usage/Options, combining `_COMMANDS` descriptions with registered option metadata.
  `/commands` has public usage documentation. `/leaguecommands` and `/tftcommands`
  derive directories from the registered tree, partitioning non-owner commands by
  TFT prefix; entries split at command boundaries before embed field limits.
- Owner-only `/ownercommands` introspects `@commands.is_owner()` checks. Owner
  self-tests and `/msg` (send as the bot to a chosen channel, defaulting to the
  invoking channel) are excluded from public directories. Shared behavior includes
  pagination, mention-safe responses, deferred slow work, ephemeral permission
  errors, and bot-mention latency replies. `/mastery` pages are clickable by anyone;
  other author-scoped paginators retain their restriction.
- Owner-only `/flakerank user:<member> category:<tier>` privately assigns one
  member selected with Discord's native user picker to a separate tier list for
  each Discord server.
  Re-ranking moves a member to the new category, and its ephemeral response shows every tier from S (most flaky)
  through F (least flaky), followed by Unknown for unranked members. It is
  excluded from public command directories; public `/flake` displays the saved
  server list without allowing edits. These tiny atomic JSON reads/writes run
  directly under a file-specific lock, so Riot polling and unrelated store writes
  cannot starve them through shared executors or locks.

### Champion and counter reports

- `/champstats` waits for all available Riot history and required timeline-backed
  data, merges the local 180-day cache, and falls back to cached results on scan
  failure. Timelines are fetched only for deduplicated eligible champion games
  after queue/role/patch/duration/remake filtering.
- Filters include All Games/Ranked/Solo-Duo/Flex, Top/Jungle/Mid/ADC/Support,
  full or major/minor patch, and `since_patch` through newer patches. No patch
  means all available history. The report includes record and oldest-match time,
  selected keystone/other rune win rates, legendary items, and completed boots.
- Default `filter:true` hides choices below 1% usage or with at most two games;
  exactly 1% remains visible with at least three picks. False shows all choices.
  Games of 15 minutes or less are excluded. Breakdown state stays synchronized
  when returning to Runes, pages clamp on report changes, and controls never expire.
- Every role has a `No Boots` W-L/win-rate row. ADC/BOTTOM no-boot classification
  requires timeline confirmation using the shared boot classifier and quest-slot
  handling described above. Metadata tags are case-insensitive, boot names provide
  fallback identification, and final items tolerate historical depth differences
  while excluding items with an upgrade path. Confirmed no-boot games append once
  per match to private `data/boots.log`: age, end timestamp, champion/role, K-D-A, result,
  and duration.
- `/counterstats` combines cached and paginated Riot history for a required
  champion and player role. It initially compares all enemy roles, uses champion
  emotes, and sorts by win rate. `filter:true` retains usage above 1%; `laning:true`
  instead measures gold leads over the direct role opponent at 15:00, excluding
  ties/unavailable checkpoints. Five role buttons and persistent paginated rows
  retain the selected player and queue. Both statistics commands persist their
  author, selection, and page so controls survive process restarts.
- Rank history persists snapshots with LP attribution, promotion and season-reset
  handling. `/today` uses configurable recent local-calendar days (one by default),
  `/lpgraph` annotates rank/LP points, and the leaderboard paginates. The versioned
  match cache excludes remakes, respects team membership, migrates older schemas,
  prunes expired data, and degrades safely when unavailable.

## Timeline analysis and ratings

### Timeline and lane comparisons

- Timeline utilities support kill/death positions, solo kills, lane opponents,
  gold/XP differences, role-aware level caps, five-minute series, time-weighted
  kill/death bounty ledgers, objective proximity, and minimap/chart rendering.
- `/jungleproximity` blends dwell time and kill involvement at 5/10/15 minutes,
  displaying junglers side by side with the shared grouped bar chart. Region
  boundaries follow blue nexus → blue red buff → bot Scuttle → red blue buff →
  red nexus, and blue nexus → blue blue buff → top Scuttle → red red buff → red
  nexus. Mid lies between routes; Top/Bottom lie outside. Within 1,500 map units,
  proximity is split between the adjacent outer lane and Mid.
- `/laning [server] [username] [match_id] [position]` uses
  `bot_app/laning_render.py` and `MatchTimeline.stats_at` for Gold/XP/CS comparisons
  at 5/10/15 minutes in separate inline columns. `build_laning_comparison_chart`
  plots grouped Gold/XP bars colored by side. Jungle has no supported lane opponent.

### Lane corpus and rating baselines

- `bot_app/lane_matchups/extract.py` extracts TOP/MIDDLE/BOTTOM/UTILITY outcomes at
  14:00 from gold/XP/CS/solo-kill differences. Jungle is excluded; support uses the
  bot pair's combined gold/XP lead plus bottom-half kill participation.
- `match_cache.py` schema version 5 stores sufficient statistics per
  `(patch, position, champion, opponent)` in `lane_matchups`/`lane_ratings`, with
  both matchup directions and no per-game rows. `model.py` fits additive champion
  ratings and a shared blue/red coefficient by alternating projection, forces
  antisymmetric pair effects, and shrinks them toward rating differences using an
  empirical-Bayes constant from the corpus. Fits are reused until a manual write
  advances the in-process statistics revision. No scipy is required.
- `collect.py` is a manual snowball harvester: apex League-V4 seeds → PUUIDs →
  matches → participants, deduplicated against `matches.sqlite` and using the
  tracker's rate-limited `RiotClient`. It is not wired into startup. `/counters`
  is removed; stored statistics support rating-baseline collection.
- `rating_baselines.py` stores count/running sums per `(patch, position, metric)`
  in schema-v5 `rating_baselines`, without player identity or per-game rows. It
  pools the retained patch window and withholds baselines with too few samples
  or no spread. `lane_matchups.collect.harvest` uses already-fetched matches and
  timelines; `backfill_rating_baselines` replaces box-score totals from cached
  matches, is safe to rerun, and leaves timeline baselines untouched. Announcements
  read baselines without writing them. Baselines age out with lane statistics.

### Match ratings

- `bot_app/rating.py` version 1.1 rates standard five-player Summoner's Rift only;
  ARAM, Arena, remakes, and malformed role layouts remain unrated. Signals come
  only from actual Match-V5/timeline fields, with no invented ward quality,
  support quest timing, camp counts, isolated deaths, or teamfight presence.
- Gold/XP/CS use signed role-opponent differences at 10:00 and the 10:00→15:00
  swing. Metrics normalize against role-specific population mean/spread; fewer
  than 200 samples for a role/metric falls back to lobby normalization for that
  metric. This keeps mature scores comparable across games. Accumulating totals
  (healing/shielding, crowd control, bounties, steals, enemy jungle monsters) use
  per-minute rates to account for different match lengths.
- Economy is role-specific: lanes use gold/XP/CS differences and swings, GPM and
  CS/min; jungle uses jungle-CS differences/swings and enemy monsters per minute;
  support uses the bot pair's gold/XP differences/swings and support GPM.
  Solo/outnumbered kills and multikills are separate weighted combat metrics.
  Objectives require explicit killer/assist credit, not frame proximity.
  Discipline uses time-weighted bounty surrendered, fraction of time dead, and
  team death share. Win/loss is excluded from individual scores.
- Grades S+ through F use `SCORE_SPREAD` / `GRADE_THRESHOLDS` calibrated from the
  composite distribution. Missing timelines produce a renormalized box-score-only
  score marked Limited; full data is marked Full. The match Ratings display
  fetches timelines on demand, shows name/score/K-D-A columns, and retains the
  selected chart and both dropdowns even when the timeline fetch fails.

## Meetup planning

- `/meetup propose|confirm|list|cancel` and explicit IDs are scoped to the invoking
  server. `/meetup list` paginates open events in five-row aligned columns. Storage
  is separate `data/meetups.sqlite` (schema 1: `meetups`, `meetup_options`,
  `meetup_votes`, `meetup_answers`) so pruning the match cache cannot delete plans.
- `timeparse.py` converts comma-separated natural-language times to UTC epochs
  using an IANA zone, rejects explicit past times, and rolls bare times forward.
  Accepted forms include `fri 7pm`, `tomorrow 18:30`, `9/5 7:30pm`, `sept 6 8pm`.
- Activities and times are independent multi-select polls, avoiding a cross-product
  that could exceed Discord's 25-option limit. Each user/axis/option vote is stored;
  a new answer replaces that axis, and empty selection counts as an answer.
  Organizer lock-in previews axis leaders in an ephemeral picker, acknowledges
  before storage, and atomically locks only a still-polling meetup. Stale pickers
  cannot reopen closed plans or overwrite a lock. Headcount is the overlap of
  members who selected both winning options.
- `render.py` builds every state from stored meetup state, uses inline label/vote/
  voter columns with field limits, and displays `<t:epoch:F>` / `<t:epoch:R>` for
  viewer-local time. Persistent views store only `{"meetup_id": …}` and reload
  current votes. `command_views.py` restores `meetup` and `meetup_confirm` controls.
- `/meetup confirm` previews exactly who will be pinged, requiring a second click
  to send. Default recipients are expected but unanswered members; other scopes
  include everyone expected or everyone who voted. A new thread message explicitly
  enables `AllowedMentions(users=True)` because edits do not notify members.
  Replies update the same attendance store as the original poll, and repeated
  default confirmations target only members still unanswered.
- Proposals attach and pin an optional discussion thread while keeping the poll
  embed in the parent channel so thread archival cannot prevent embed edits.
  Threads use seven-day auto-archive; commands inside threads skip nested thread
  creation. Missing Create Public Threads / Manage Messages permissions degrade
  to a threadless/unpinned plan. The ten-minute `poll_and_close_meetups` loop closes
  plans six hours after their start or unlocked polls after 30 days, replaces the
  embed with a static summary, removes controls, unpins, and archives/locks the
  discussion thread without deleting it.

## Runtime and storage

- `RiotClient` centralizes platform/regional routing, bounded retries, per-key
  sliding-window rate limits, bounded request fan-out, connection pooling,
  coalesced concurrent misses, and optional SQLite match/timeline caching.
  League/TFT keys have independent budgets; identical key values share one budget.
- Data Dragon provides champion metadata, aliases, emoji rendering, profile icons,
  map images, patch-aware caches, and internal-ID-to-display-name resolution.
- Environment-first configuration falls back to JSON, validates numeric/boolean
  settings, and supports IANA timezones, polling intervals, cache controls, and
  clean startup/shutdown. `LOG_LEVEL` / `log_level` defaults to INFO for routine
  operations; DEBUG adds item work, cache outcomes, request/response details, and
  branch diagnostics. The terminal uses compact rows with individually color-coded
  category labels, short project logger names, grouped multiline tracebacks, and explicit
  startup/shutdown lifecycle events; `bot.log` retains those categories plus full
  dates, millisecond timestamps, full logger names, and no terminal color codes.
- Central command diagnostics record invocation, duration, and exceptions.
  Command result logs identify the originating slash command, including
  `/matchlist` responses.
  Coachless diagnostics include endpoint, status, stage, cache, and traceback.
  Completed-match request failures name every tracked account that surfaced the
  match and include the full resolved host and endpoint URL. Active-lobby fallback
  ids are attributed only to accounts mapped to that lobby; message-only recovery
  is labeled as saved live-announcement state rather than inventing player sources.
  Their expected pre-publication Match-V5 404s are DEBUG events; normal-history
  404s and other request failures remain warnings.
  Discord 429 debug logging includes `X-RateLimit-Limit`, `X-RateLimit-Remaining`,
  `X-RateLimit-Reset-After`, `X-RateLimit-Bucket`, and `Retry-After`.
- Exclusive `bot.lock` rejects duplicate processes. `run_bot.command` stops prior
  instances and starts system Python. `sync_commands_enabled` defaults true;
  false skips startup command sync while tracking and announcements continue.
  A failed enabled sync leaves the success flag unset so the next reconnect retries.
- Mutable state lives under gitignored `data/` (or `VIBECODE_DATA_DIR`). Existing
  `json/` files are copied there when newer and retained as backups, allowing an
  already-running old process to write safely until cutover. SQLite migration uses
  the backup API for a transactionally consistent snapshot.
  Atomic JSON replacement prevents partial writes; independent League and TFT
  transaction locks serialize full poller read-modify-write cycles, including
  registry changes, so overlapping workers cannot commit stale snapshots. Match,
  meetup, and persistent-view records use versioned SQLite storage.
- Offline-capable `unittest` coverage includes domain logic, registration and option
  types/order, pollers, routing, publishing, commands, persistence, migrations,
  rate limits, caches, chart fallbacks, and startup errors. A suite-wide socket
  guard blocks accidental requests unless an integration run explicitly opts in;
  Matplotlib uses a temporary cache and test-created event loops are closed.
  GitHub CI installs the Python 3.9 lock and runs full discovery. Documentation checks
  also enforce synchronized editable root sections, a 12 KiB budget per root
  file, and existing local link targets/Markdown anchors. Exact commands are in
  [AGENTS.md](../AGENTS.md#verification).

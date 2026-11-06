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
  and rank-history writes. Processing is chronological against one permanent
  global ledger of completed match ids, so shared games are stored once and an
  account change cannot make an old game new again. Registration seeds recent
  ids into the same ledger without backfilling announcements. Legacy per-player
  histories migrate into it before their duplicate lists are cleared.
  Spectator lobby ids supplement the by-PUUID Match-V5 history for modes with
  limited completed-match coverage, including ARAM: Mayhem (2400), Clash (700),
  and ARAM Clash (720).
  After Spectator confirms one of these lobbies ended, a Match-V5 `403 Forbidden`
  produces a one-time shared completed-game notice stating that the game finished
  and detailed results are unavailable. Finished Mayhem and Clash embeds use purple,
  including unavailable-result notices and all completed display modes. The notice reuses the stored live-game
  player/champion summary and team columns. It shows the elapsed time from the
  stored game start to the 403, minus two minutes and floored at zero, as
  `(~Nmin)` to compensate for polling delay, while omitting seconds, the stale
  relative start time, and invented match statistics. Active lobbies never
  produce a finished notice; `404` responses remain retryable. Successful
  Match-V5 payloads retain their real results, statistics, and controls.
  Explicit `/match` and `/livegame` lookups include every registered tracked
  participant present, so summaries, bold rows, and gold chart highlights stay
  consistent with automatic announcements; an explicitly selected unregistered
  player is included too.
- Embeds show team columns, tracked-player highlights, duration, and a relative
  end timestamp. The tracked-player summary shows parenthesized K/D/A directly
  after the player name. Team rows use the same `name (K/D/A)` format, omitting
  the former centered-dot separator to reduce wrapping in the two-column layout.
  Right-to-left names are isolated from both the champion icon and Latin stats,
  keeping all three in their intended order. Arena queues 1740 (Bravery 3v3)
  and 1750 (3v3) use team-aware layouts with concise `Team 1`, `Team 2`, etc.
  headings. Completed-match
  Players, Ratings, and Items views prefix each team with a distinct color marker
  and list teams by ordinal placement (`1st`, `2nd`, etc.), best finish first;
  live-game announcements keep the headings uncolored.
  Rank displays include Solo/Duo win rates and replace player names, omitting the
  K/D/A suffix while ranks occupy that column.
  Displayed Emerald standings use `Em` to keep rank labels compact.
- Top featured-player summary lines append 🔥 (`:fire:`) for three or more
  consecutive wins or 🥶 (`:cold_face:`) for three or more consecutive losses
  across League queues. Completed views include the selected match; live views
  check three completed results before the lobby started. History comes from
  Riot's consecutive match IDs, with cached match payloads and a five-minute
  marker cache. Later games never affect historical views. Remakes, missing
  results, insufficient history, and API failures omit the marker. ARAM: Mayhem,
  Clash, and ARAM Clash never show streak markers in live or completed summaries; finished notices
  also remove markers from older stored live summaries. Display/chart controls
  preserve this text.
- Ranked promotions append 🎉 and demotions 📉 to the top completed-player
  summary in place of 🔥/🥶. Moving up/down a division or tier qualifies,
  including apex tiers; LP-only
  gains/losses, placements, resets, remakes, and ambiguous multi-game rank changes do
  not. Rank snapshots must differ by exactly one completed ranked game.
  The poller supplies attributable before/after snapshots and stores a
  promotion/demotion flags on the match's rank-history point, so later `/match`
  views and persistent controls retain the cue. Missing historical evidence
  omits the rank-change emoji.
- Top featured-player summaries append 🖐️ beside K/D/A when that participant's
  Match-V5 `pentaKills` is positive. One marker covers any number of pentakills
  in the selected match and coexists with streak/rank-change icons. It applies
  to all League queues with available results, including Mayhem and Clash;
  missing statistics and unavailable-result notices never invent a pentakill.
  Shared `/match`, automatic announcements, display/chart controls, and saved
  views retain the same summary.
- Linked teammates present in a lobby appear in shared live and completed summaries
  in every queue, including Mayhem, and in `/livegame` and `/match` selections.
  Live team rows highlight them too. They never initiate polling; missing ranked
  data does not exclude them from summaries.
- Ranked teammate LP is snapshotted when a shared live lobby is first announced.
  When that lobby later surfaces through a tracked account's completed-match poll,
  the shared match announcement includes the teammate's current rank and LP change.
  Teammates still never initiate match polling or announcements themselves.
- Display choices are Players, Ranked Solo, Ratings, Items, and Mastery; Flex
  replaces Ranked Solo with Ranked Flex and adds Solo Rank. Mastery reuses the
  same player/champion data from an earlier live announcement when available,
  otherwise fetching on demand. Ratings and Items retain the completed-match
  header and tracked-player summary above their columns, including after chart
  changes; Ratings retains the outcome color (purple for finished Mayhem/Clash) as well.
- Chart choices are Damage Done, Gold Graph, Gold Difference Graph, Jungle
  Proximity, Damage Taken, Healing and Shielding, and Vision Score. Display changes
  retain the selected chart, including Items and Ratings; each mode retains
  Display/Chart controls. Jungle Proximity changes only the chart, preserving the
  selected columns, and shares `jungle_proximity_render` with the standalone command.
  Arena metric charts use borderless solid colors matching the team heading
  markers, center each ordinal inside its bar with contrasting text, and retain
  the tracked player's gold outline and ordinal legend labels.
- Items render blue/red name/items columns with seven core icon slots for ADCs
  and six for other roles (⬛ for empty slots), followed by the trinket. They
  retain the base win/loss color (purple for finished Mayhem/Clash).
  The final inventory is authoritative. If an ADC/BOTTOM boot is absent, timeline
  purchase history supplies the latest still-held boot: sales and undo events clear
  it, while a bot-lane role-quest `ITEM_DESTROYED` preserves it because the boot moves
  into a dedicated quest slot. Detection uses Data Dragon metadata plus conservative
  IDs, including boots `3008`/`3172`; Manamune `3004` is not a boot. Missing metadata
  or emotes still allow a name or 🥾 fallback. Stormrazor (`3097`, and older
  `3095`) keeps its name when absent from Data Dragon. Older `3095` match slots
  use the configured `3097` emoji when available; the legacy `Stormrazer`
  spelling also resolves. Text is the fallback when neither emoji exists.

### Live games and lifecycle

- `/livegame` and automatic lobby announcements share team columns, player/champion
  rows, inferred positions, ranks/win rates, duration, and a relative start timestamp.
  Mayhem, Clash, and ARAM Clash omit elapsed timers and streak icons, showing
  `<queue> — Live Game` without a parenthesized duration; their relative start
  timestamps remain available.
  Right-to-left player names are isolated from their champion icons so Discord's
  bidirectional layout keeps the icon at the start of each row.
  One automatic announcement is emitted per shared lobby. The persistent Display
  dropdown replaces names with ranks or champion mastery; Flex offers separate
  Flex Rank and Solo Rank choices. Ranks never appear in a separate bottom block.
- Automatic live posts are recorded by `platform:gameId` through
  `store.remember_live_game_message` in private runtime storage. Their shared
  announcement payloads are stored under the same key until cleanup, preserving
  the Spectator participant, champion, queue, and tracked-player data needed for
  a Mayhem/Clash completion notice when Match-V5 denies the finished result.
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

### Inferred partymates

- Shared `format_live_game` admits inferred partymates on their third same-team
  game: two prior completed games in the same queue within seven days plus the
  current live lobby. The prior games must be at least half of each player's
  last ten cached non-remake games before the lobby began. Completed-match
  views retain saved markers but never admit new identities.
  Mayhem (queue 2400) requires three distinct observed same-team live lobbies,
  including the current one, within seven days and at least half of each
  player's last ten observed lobbies. Finished results/notices never count.
  Platform/game IDs deduplicate repeated polls and `/livegame` calls; the
  separate SQLite history survives restarts and does not create match results
  or statistics. Missing platform, game ID, or timing skips observation.
- Detection uses the existing SQLite match cache without extra Riot requests.
  Bots, custom games, unknown teams/timing, and Arena are excluded. Missing
  cached evidence does not establish solo play. There is no separate
  **Possible premades** section or confirmed party-size claim.

- Qualifying pair members outside the tracked/teammate registries are merged into private
  `data/partymates.json`, keyed by PUUID with `server` and full `riotId`, using
  the same identity layout as `teammates.json`. Optional existing Discord IDs
  and known Riot IDs survive incomplete later payloads. Entries accumulate as
  historical inferred identities, not confirmed current party membership.
  Unknown platform skips saving rather than guessing a server. Concurrent
  writers merge atomically and unchanged lookups do not rewrite the file;
  storage failures leave the announcement usable.
- Partymates exclude tracked and teammate PUUIDs on reads, saves, and detection
  merges. Saving a League tracked account or teammate also prunes matching stored
  partymates; legacy overlaps are hidden immediately and removed on the next merge.
  `/accounts` additionally excludes identities shown in either earlier section.
  Tracked accounts use ⭐, linked teammates ⭐, and saved partymates 👥
  beside their names in shared match/live rows and `/accounts`. If an identity
  overlaps, tracked status takes precedence. A tracked player with no other
  tracked, teammate, or saved partymate on their team uses 😢 instead of ⭐.
  This means no known queue companions, not confirmed solo queue; opponents
  do not count. Bots, unknown queues/teams, and Arena skip the lonely marker.
  Name, rank, and mastery modes share it across commands and announcements.
  Saved partymates are bold, including
  rank and mastery modes. They retain this marker
  after detection evidence ages out. Partymates do not initiate polls or change
  player lookup; tracked and teammate identities remain excluded.

- When exactly two participating players would show ⭐ after solo/five-stack
  overrides, both use 🫂 (`:people_hugging:`) instead. Count tracked and linked
  teammate identities once per game; saved partymates retain 👥. This applies
  to shared live/completed rows in name, rank, and mastery modes.

- A full five-player team uses 🤡 instead of its registry, lonely, or repeat
  markers immediately when all five are tracked, linked teammates, or saved
  partymates on the same team. This overrides ⭐ even without cached history.
  Ranked Flex (440) also marks all five when exactly four are known and one is
  unknown, without history or adding the fifth to partymates. Other queues retain
  the five-known-player rule. Otherwise all five must share the same two prior completed games in that queue
  within seven days and those games comprise at least half of each member's last
  ten cached games. Mayhem requires three shared observed live lobbies through
  the selected game's start. Overlapping qualifying pairs alone do not suffice
  for unknown identities. Missing evidence for unknown players, bots, unknown teams,
  and Arena skip detection. This is inferred, not a confirmed Riot party roster.
  Both shared surfaces and name/rank/mastery modes use the same read-only check;
  existing bolding remains unchanged.

- Shared live/completed player rows append 👤 for a prior ally currently on the
  same team again and 😈 for a prior enemy currently opposing the selected or
  tracked player again. The marker uses only each selected/tracked player's
  immediately previous cached match or observed live lobby before this game's start. Older encounters
  never contribute; cached history may be incomplete.
  Blue/red side changes do not change the relationship. 🔄 marks a previous
  ally now opposing you or a previous enemy now allied with you. Multiple
  selected/tracked players can contribute different cues from their respective
  previous games.
  ⭐/👥 registry markers override all three encounter cues in name,
  rank, and mastery modes and do not add players to any registry. Arena, bots,
  unknown teams/queues/timing, the selected game, and future games are excluded.
  Normal live lobbies now persist distinct encounter observations too; repeated
  polls/views count once. Missing evidence shows no encounter marker.

- Owner-only `/updatepartymate` (no options) fetches every tracked League
  account's complete paginated Match-V5 history for a fixed past-seven-day window.
  It reuses cached matches and deduplicates shared IDs. The shared pair rule is
  applied only to a current Spectator same-team lobby, with two completed games
  before its start and pairs anchored to a completely scanned tracked account. Untracked histories remain partial:
  only games observed in the roster scan contribute to their ten-game window.
  Mayhem uses the shared three-live-lobby observation gate. Completed Mayhem games, Arena, custom games, bots, remakes, out-of-window
  games, and unavailable results are excluded. Existing partymates remain; storage excludes tracked League/TFT
  and teammate identities. The reply defers privately, shows progress, prevents
  concurrent sweeps, and reports additions, saved total, and incomplete coverage.
  Riot failures do not prevent other accounts from being scanned. No live lobby
  or third match is needed for this historical scan. `/ownercommands` discovers
  it automatically; public directories exclude it.

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

### Worlds esports announcements

- Worlds schedules/results, player statistics, live records and damage
  charts display directly without spoiler formatting. `/bracket` also shows its
  image directly. Current automatic schedules and recent result posts migrate
  in place when the rendering version changes.
- `/stats` (no options) opens directly to Game 1 of the latest played Worlds
  series from the current year. Its dropdown lists matchups and Pacific dates, newest first, with
  menu pages beyond 23 series (Newer series at the top, Older series at the bottom).
  Selecting a series resets to Game 1; buttons
  include only played games. No date tabs, schedule rows, or back button appear.
  `WorldsStatsView.render_game` reuses `game_stats_embed` and the announcement
  chart renderer for initial sends and subsequent edits. Saved stats are reused;
  missing stats are requested after acknowledgement; winners use saved data. Series/game
  changes replace attachments, removing charts when unavailable. Controls expire
  after 30 minutes; no played series shows a concise empty status.
  `/worlds` keeps date schedules/results without the stats picker.
  `worlds_history` saves all played current-year Worlds series to private
  `data/worlds_history.json`. The full-year official history query refreshes at
  most hourly, splitting saturated date ranges to avoid truncated pages; fresh
  30-second schedule data merges in without regressing saved scores. Archived
  series survive restarts and refresh failures. A failed first complete history
  fetch returns unavailable rather than silently presenting a partial list.
  Final player stats (`data/worlds_stats/`) and winners
  (`data/worlds_game_details/`) persist independently and are reused on selection;
  missing stats are fetched on demand, rather than prefetching every game's stats.

- Automatic Worlds results post the cumulative score after each completed game
  and the final series score, without player stats, charts, or game tabs. Series-start
  alerts and intermediate score posts are deleted after the final result is delivered. `/stats` retains manual
  player K/D/A tables, damage-share charts, and game selection; automatic results
  are never enriched or upgraded into stats messages.
- Completed esports game IDs/numbers are retained from the schedule/live feeds.
  Timestamped `window` and `details` requests must supply a `finished` window and
  a detail frame at exactly the same timestamp, matching game/match/team IDs,
  ten unique players, synchronized K/D/A, and complete finite team damage shares.
  Default feed requests return opening frames and are never used as final stats.
  Only validated final snapshots are cached under private `data/worlds_stats/`.
- Final stats are fetched and cached on demand through `/stats`; the automatic
  announcement poller does not fetch or publish player statistics. Delivery
  receipts persist in private `data/worlds.json`; historical series stay silent.
- `/setchannel [channel] [type]` and `/unsetchannel [type]` (Manage Server)
  offer Tracked Players, Worlds, and Both. Set defaults to Tracked Players and
  the invoking channel; unset defaults to Both. Tracked Players includes automatic
  player matches/live games and recaps; Worlds includes schedules/start/final
  alerts. Both sets or disables both families. Single-family changes preserve the
  other family; an inherited Worlds route is pinned before changing/removing a
  configured player route. Worlds/Both setting checks numbered-post permissions.
  `/setworldschannel` remains a Worlds-only shortcut. Selected unsets clear their
  routes and persist independent player/Worlds opt-outs in private
  `data/announcement_preferences.json`, preventing fallback destinations from
  restoring unwanted posts. Shared publishing filters enabled server routes
  while collection remains active for other servers. The recap poller and both
  Worlds pollers honor the same opt-outs. Other servers and manual replies are
  unaffected. Set commands re-enable only their selected families. Guild removal clears the
  saved opt-outs. Public channel commands live in their own named modules;
  shared route updates live outside command handlers.
- Public `/setworldschannel [channel]` (Manage Server) saves a dedicated Worlds
  destination in private `data/worlds_channels.json`, defaulting to the invoking
  channel. Permission checks require View Channel, Send Messages, Embed Links,
  Add Reactions, and Read Message History. Both Worlds pollers share the same
  resolver: each server's override replaces its regular and fallback Worlds
  destinations while player match/recap routing stays unchanged. Unconfigured
  servers retain their existing routes. Route writes are serialized, and guild
  removal clears both general and Worlds settings.
- Changing the route after 04:00 posts today's numbered schedule in the new
  channel on its next poll; users subscribe on that new post. Old-channel
  reactions are not transferred. Existing posts remain; an ongoing series may
  get a new-channel live notice, but completed history is seeded without spam.
  Manual `/worlds` and `/bracket` replies still use the invoking channel.
- Schedule rows and series announcement footers show the entering Swiss record
  bracket (for example `Swiss · 2-2 Bracket`) or `Quarterfinal`, `Semifinal`, or `Final`.
  Series-start descriptions show the same label. Swiss records count completed
  series, never individual games, and remain the pre-series record after results.
  Recent feeds are enriched with saved current-year history or an hourly cached
  official full-year lookup; incomplete history omits the record rather than guessing.
- Worlds start and final announcements use Riot's full official
  team names with the configured custom team logo before each name. Schedules
  and Past Results keep compact team codes with the same logos. Named emoji lookup
  maps TLAW to TL, TES to TOP, and GEN to GenG; other codes use their matching
  emoji names. Missing/unavailable icons fall back to plain team text. Bracket
  images continue using the source's official names and downloaded team logos.
  `/emoji` and its docs list these team-logo meanings and aliases.
- At 04:00 America/Los_Angeles, a separate minute poller posts the shared
  `/worlds` schedule in every resolved Worlds channel, once per local
  day. After downtime or initial deployment it catches up today's post after
  04:00; it never backfills previous days. Automatic posts show only today's
  series; off days show a brief no-games-today status. Manual `/worlds` still
  includes the next game day. Existing daily posts are edited in place when
  migrating to the today-only layout, preserving their reactions/subscriptions.
  Today's series are marked 1️⃣–9️⃣/🔟 in chronological order and matching reactions
  are added to the message; next-day series have no subscription numbers.
- Reacting subscribes to that specific series' live-start alert in the same
  channel. Removing the reaction opts out. Subscribers are fetched from Discord
  when the series goes live; bots are ignored and only those user mentions are
  allowed. No roles, reaction event handlers, or privileged intents are required.
  The bot needs Add Reactions, Read Message History, View Channel, Send Messages,
  and Embed Links. Start pings follow confirmed live status, never the schedule
  time alone. Score/final announcements do not ping subscribers.
- Private `data/worlds_schedule.json` stores per-day message IDs, stable
  number-to-series IDs, and reaction setup progress for fourteen days. Daily
  delivery and partial reaction setup resume after restart without reposting;
  deleted schedule posts are not recreated and cannot supply subscriptions.
  Large subscriber groups split into mention batches with persisted progress;
  failures retry unsent batches. As with other Discord sends, a crash after
  sending but before saving its receipt can repeat a delivery. Overflow schedule
  pages use acknowledged page controls in the same message (30-minute timeout).
- Public `/bracket` (no options) renders the current Worlds stage as a PNG. Swiss:
  five rounds grouped by entering series record, team logos/names, game scores,
  winner highlights, and advancing/eliminated teams with series W/L. Undrawn
  matchups remain TBD. It uses the latest tournament with Swiss events and
  requires all eight opening matches and consistent series records; partial
  history fails with a concise status. Optional logos use cached Riot-hosted
  images with neutral-circle fallbacks. It defers before data, logo, and rendering
  work; the command refreshes on each invocation and has no watch or title links.
  After Swiss finishes or a knockout series starts (including its scheduled
  start), it switches to four quarterfinals, two semifinals, and one final in a
  connected tree. Future knockout fixtures alone do not hide active Swiss.
  Published participants/scores/logos populate cards; unassigned slots stay TBD.
  Connections between known matches require shared published participants;
  no downstream pairing or advancement is invented from chronological order.
  Both stage selection and rendering use the latest tournament in the feed.
- Public `/worlds` (no options) shows the full Pacific-date schedule for today
  and the next date with games, including completed results, live scores,
  scheduled/awaiting-start statuses, best-of formats, stages, local Discord
  start times, and future countdowns. Off days show an empty today plus the full
  next game day; earlier-day series still live get an additional schedule.
  Undrawn matchups remain TBD in schedules and stay excluded from announcements
  and bracket reconstruction. Rows split into aligned inline columns/pages before
  embed limits. Manual `/worlds` initially shows Today, with buttons ordered Past Results, Today, then the next
  game date as buttons switching the same message. Adjacent dates use Tomorrow/Yesterday; other dates retain weekday labels. Only
  the selected day is visible. Past Results shows every series from the latest
  Pacific match date strictly before today, including unfinished series and
  skipping off days; no past date shows a brief empty status. Date changes reset overflow pages; buttons defer
  before editing. Manual schedules refresh scores/status on the adaptive cadence below using direct
  channel edits, preserve the selected date/page, and retain persistent date controls
  across restarts. Message IDs and selections are retained privately for 14 days;
  deleted messages are forgotten and failed edits retry without reposting.
  Other date headings and dated buttons include abbreviated weekdays. Compact headings
  and aligned columns have no spacer fields or footer.
  Automatic daily posts stay today-only without date buttons. The schedule title links to the
  [LoL Esports league schedule](https://lolesports.com/en-US/lolesports/leagues/first_stand,lck,lpl,msi,worlds);
  Watch links include [Caedrel](https://www.twitch.tv/caedrel) and official
  [Twitch](https://www.twitch.tv/riotgames),
  [YouTube](https://www.youtube.com/@lolesports/live), and
  [Kick](https://kick.com/riotgames). Links display on two lines: Caedrel: Twitch, then Official: Twitch, YouTube,
  Kick, appearing in
  series start/score/final announcements and bracket replies. Manual schedule/
  Past Results tabs and daily posts have no broadcast-link block. Existing
  daily posts refresh scores/status on the same adaptive cadence in place while preserving reactions. No later games is noted in the message.
  The command defers before fetching. Commands and the poller share a 30-second,
  coalesced feed cache; source failures have a concise retry status.
- `bot_app/worlds.py` combines scheduled/completed Worlds page data with the
  website's persisted `homeEvents` query for live and recent completed series,
  scoped to Worlds with a two-day lookback and a next-day end bound. Both UTC
  date bounds are supplied so finished series remain visible while the page cache
  catches up. The
  server-rendered schedule omits in-progress series. Live-query failures stop
  that poll with a visible error rather than silently dropping active series;
  the public persisted operation ID may require maintenance when the site changes.
  Live requests use a wall-clock minute refresh key to avoid the CDN serving
  multi-minute stale game scores while retaining one cache key per minute;
  command/poller consumers still share the in-process 30-second cache.
  Duplicate records prefer the most advanced status at the same score. Team
  arrays are aligned to saved team IDs before comparing scores or sending alerts,
  since the live query can reverse scheduled team order. All three Worlds background jobs (announcements, daily schedules and retained
  manual schedules) run once per minute from thirty minutes before a scheduled
  start through six hours after it while awaiting a delayed start, and whenever
  a series is live. They run every fifteen minutes otherwise to discover schedule
  changes. A local minute timer wakes them as soon as the lead-in begins without
  making idle network requests. Minute polling continues for fifteen minutes
  after observed activity for final delivery and cleanup retries. Startup fetches
  immediately, daily schedules still run at 04:00 Pacific, and manual commands
  retain on-demand fetching with the shared 30-second cache. Background jobs run
  independently of tracked-player polling. Alerts follow published site status, which can lag the
  broadcast; scheduled times alone never trigger a start alert.
- Each existing announcement channel receives one series-live alert, a cumulative
  series score after each newly observed game result, and a final series result
  replacing the last game's ordinary score update. Later game starts never send
  separate live notices. The green `Worlds · Series started` alert leads with
  the bold matchup, followed by the stage and best-of format. It shows verified entering
  Swiss record when available, official team names/logos, and watch links, without
  a game number. If first observed mid-series, it still sends only one series alert.
  Series alert IDs, intermediate score IDs, legacy game notice IDs, and split subscriber-ping batches
  are saved per channel and deleted after the final series result is delivered.
  Failed deletions retry without reposting the final; missing messages count as
  cleaned up. A one-time bounded history scan recovers legacy bot-authored live
  and intermediate score posts for the same matchup within recent series, excluding schedules,
  final results, and other authors. Restart receipts preserve cleanup progress.
  Finished games never receive retroactive live notices. Numbered reactions ping
  only for the initial series alert; result posts have no pings.
- Private `data/worlds.json` stores per-channel start/score/final receipts, retaining
  them across restarts. Already completed series are seeded silently when a
  channel first joins; an ongoing series receives a live notice and its current
  score. Failed Discord sends retry without repeating previously saved deliveries.
  As with ordinary sends, a crash between Discord accepting a message and saving
  its receipt can repeat that message.
- The parser reads embedded `EventMatch` JSON without executing JavaScript and
  filters to Worlds. Two known teams, best-of format, completed-game counts, and
  published cumulative scores must agree; malformed or incomplete events are
  skipped. Regressing scores do not announce. If several games finish during an
  outage, only the latest confirmed score is announced, without inventing past
  game winners. Website schema changes fail without modifying delivery receipts
  and are reported through the existing background-error handler. This website
  feed is separate from Riot's player API and may need maintenance when the site
  changes; it does not require an esports API subscription.

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
  accounts (linked mentions or `*(unlinked)*`), then Teammates and inferred
  Partymates, ephemerally. Each section is sorted by Riot ID; partymate rows show
  Riot ID, server, and an optional linked Discord user. Section headings include
  counts, and pagination includes all three registries.
- Owner-only `/add` accepts optional `user` and an `account_type` dropdown:
  `tracked` (default), `teammate`, or `partymate`. The dropdown replaces the
  former teammate checkbox. Manual partymates skip the third-game inference gate
  and never start polling. Re-adding a companion preserves a known Discord tie
  when `user` is omitted. Tracked PUUIDs must be switched using the linked user.
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
- `/add` with a `user` but no `summoner` switches the saved League identity
  to the selected account type. Teammate/partymate selection stops League/TFT
  polling through their normal unregistration paths; either companion can be
  promoted back to tracked through `_track_both_accounts`. Moves between
  teammate and partymate preserve the Riot ID, platform, and Discord tie.
  Companion mutations share the League state lock and prevent tracked overlap;
  tracking or selecting teammate retires a matching partymate.
- Match `position` slots 1–5 are blue Top/Jungle/Mid/ADC/Support; 6–10 are red.
  `/match`, `/timeline`, `/laning` select from the requested match. `/mastery`,
  `/matchhistory`, `/matchlist`, `/champstats`, `/counterstats`, and the primary
  `/duo` side select from the lookup account's latest match. Match/timeline/self-test
  accept ordinal references 1–20.

## Commands and champion statistics

- All command responses and announcement embeds show concise results, filters, dates,
  and page numbers. Methodology, retention, and usage explanations live in
  `/commands` help and command docs, rather than result descriptions or footers.
  Error, empty, scan-progress, and partial-history statuses stay brief. Command
  directories omit introductory prose; explicit per-command help retains usage
  details. Source links use embed titles. Ratings omit methodology notes and show
  a brief timeline-unavailable footer when confidence is Limited.

### Lookups, builds, and discovery

- `/emoji` (no options) explains player markers, previous-game cues, streaks, pentakills,
  promotions/demotions, display and chart icons, Arena colors, champion icons,
  and numbered Worlds series subscriptions. It distinguishes inferred groups from confirmed parties and resolves
  custom icons from the bot cache with readable aliases as fallbacks. Available
  in the public help and League command directories; no Riot requests or controls.

- `/bestplayer` ranks every tracked League account–champion pair over the last 180 days by win rate, then games
  played, with optional `min_games` (positive integer, default 10). The threshold
  is inclusive: `min_games:20` includes champions with at least 20 valid games. Optional `queue` defaults to `All Games`; `Ranked` combines Solo/Duo and
  Flex, and named queue choices select exact queue IDs (including Arena variants
  under their queue label). Minimum games and win rates use only the selected
  queue scope, combining roles. Each invocation paginates every tracked account's
  Riot Match-V5 history in 100-ID pages within a fixed rolling 180-day window,
  without a game-count cap,
  merging cached payloads and fetching missing details through RiotClient.
  Duplicate identities and shared matches are scanned once; a fixed end time
  keeps pagination stable. Both cached and fetched payloads are filtered by match
  start time (gameStartTimestamp, falling back to gameCreation); the boundaries
  are inclusive, and undated matches are excluded. Cached results survive API failures, with incomplete
  accounts and unavailable matches disclosed on every page. Riot-available history
  is not guaranteed lifetime history; the shared cache still prunes at 180 days,
  and older matches do not contribute. One roster scan runs at
  a time. Acknowledged scan progress and final results edit a normal channel
  message so long scans survive interaction webhook expiry. Duplicate matches, remakes, missing outcomes, and
  unavailable results are excluded; MonkeyKing is grouped as Wukong. Tracked
  accounts are deduplicated by PUUID; linked teammates and inferred partymates
  are not included unless tracked. Ten rows per page show player, champion,
  win rate, W/L, and games in bounded inline columns. Plain `#1` rank labels
  avoid Discord Markdown numbered-list spacing. The command acknowledges
  before cache work, and anyone can navigate the shared paginator.

- `/profile` shows level, Solo/Duo and Flex ranks/records, and top masteries.
  Commands also cover PUUID, mastery, timelines, rotation, status, match lists,
  and OP.GG links. Profile/build URLs use `routing.opgg_url` /
  `opgg_champion_url` with `hl=en_US` to stay in English.
- `/patchnotes` reads the five newest dated entries in `PATCH_NOTES.md` on each
  invocation. The maintained file records user-facing bot changes as they are
  made, including before commit. Owner-only command additions and updates are
  excluded from patch notes. The command defers before reading and reports
  missing or empty notes gracefully. Full entries are shown without a character
  cutoff, separated by blank lines. Long output splits into numbered embeds
  at entry/word boundaries, each with at most 4,000 description characters.
- `/matchhistory` filters by champion and mode (Normal Draft, Ranked Flex,
  Ranked Solo/Duo, Arena, ARAM), shows up to ten matching results, and totals W/L.
  `/duo` uses the same mode autocomplete and team-aware cached results. Its
  record and empty-result embeds label the selected game mode, or `All Games`
  when no filter is supplied.
- `/championpool` scans the latest 100 Match-V5 IDs, reading cached payloads
  first, then fetching missing matches. It groups non-remake standard
  Summoner's Rift games longer than 15 minutes by recorded role and champion,
  with optional All SR, Ranked Solo/Duo, or Ranked Flex scope. Each role shows
  up to five champions ordered by sample size, with emoji-backed champion names,
  record, and win rate in aligned role rows without a footer. Missing emoji
  leave the champion name visible. Riot’s MonkeyKing is displayed and grouped as
  Wukong, with icons accepted under either name. The player title links to the resolved
  account's OP.GG profile. Unavailable
  match details and the eligible sample size are disclosed in the embed.
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
  TFT prefix. They group known commands by purpose, place newly registered names
  under Other, and split entries at command boundaries before embed field limits.
  `/leaguecommands` omits `/flake` from display while leaving the
  public command registered and available directly.
- Owner-only `/ownercommands` introspects `@commands.is_owner()` checks and
  lists each command once across guild-specific registrations. Owner self-tests,
  `/msg` (send as the bot), and `/purge number:<1-1000>` are excluded from public
  directories. `/purge` immediately defers ephemerally, scans newest-first, and
  deletes only messages authored by the bot in the invoking channel. Shared behavior includes
  pagination, mention-safe responses, deferred slow work, ephemeral permission
  errors, and bot-mention latency replies. Anyone who can see a command result
  can use its display and page controls, including the shared paginator.
- Owner-only `/flakerank user:<member> category:<tier>` privately assigns one
  member selected with Discord's native user picker to a separate tier list for
  each Discord server.
  Re-ranking moves a member to the new category, and its ephemeral response shows every tier from S (most flaky)
  through F (least flaky), followed by Unknown for unranked members. It is
  excluded from public command directories; public `/flake` displays the saved
  server list without allowing edits. These tiny atomic JSON reads/writes run
  directly under a file-specific lock, so Riot polling and unrelated store writes
  cannot starve them through shared executors or locks.

### Party records and daily recaps

- Public `/party [server] [username] [game_mode] [size]` defaults through the
  shared linked-account resolver. It groups completed cached games by exact
  three-, four-, or five-player known roster and queue; known players are tracked
  accounts, linked teammates, saved partymates, and the selected player. Larger
  groups never also count as smaller subsets. Each paginated report shows W/L,
  win rate, games, each player's most-played role, and the most-used champion
  lineup. Current registries define membership; shared teams do not prove a
  premade party. Cache-only coverage (normally 180 days) is documented in command help. Remakes,
  Arena, unknown queues, incomplete teams, and unavailable results are excluded.
  The command acknowledges before lookup and database work. Existing shared
  pagination permits any reader to navigate; field values remain below 1,024.
- Automatic completed-match publishing journals successful posts by channel and
  League/TFT match ID in private `data/daily_summary.json`. Recap identities resolve
  from registered accounts and linked League teammates, including ARAM: Mayhem
  payloads that omit names; display only summoner names without Riot tags, retaining
  the TFT suffix. Accounts still aggregate by PUUID, including namesakes.
  Older nameless journal events recover names by PUUID
  when rendered. Manual `/match` and
  `/livegame` invocations do not enter recap totals. A background poll every
  minute posts a daily recap at the first check after **4 a.m. America/Los_Angeles**,
  covering the preceding **4 a.m.–4 a.m. gaming day** using posting timestamps.
  DST boundaries preserve local 4 a.m. times (23/25-hour days where applicable).
  The fallback announcement channel and current `/setchannel` routes receive
  separate channel-specific recaps, including an empty-day notice. League W/L
  and TFT top-four/bottom-four rows are separate; unavailable results never count
  as wins/losses or LP. LP totals include only recorded attributable changes,
  combined across ranked queues. Player tables use three inline columns, Player (Games), W/L, and LP, on
  eight-row pages so LP stays beside each record. Games includes unavailable results; W/L
  reports known outcomes (top-four/bottom-four for TFT) without displaying
  unavailable-game counts; missing W/L and LP values are blank, including saved
  recap replay and restored page buttons. Invisible placeholders preserve empty
  LP rows in Discord. Game counts sit beside player names, such as `BlyTea (8)`. W/L and LP
  use their own native inline columns without separator dots, so values stay
  under the correct headers. LP uses signed totals. Recaps omit the collection explanation, date-range and
  completed-announcement count header; the title still identifies the gaming day.
- Collection starts when this feature or a destination is enabled, without
  importing old announcements. Each automatic recap uses one message; extra
  pages use First/Prev/Next/Last buttons that anyone can use. Buttons acknowledge
  before editing or saving state, and pages and selection restore after restart.
  Single-page recaps omit controls. Per-channel/day delivery receipts survive
  restarts, and failed sends retry on later polls. Receipt pruning uses the same
  closed gaming-day boundary as catch-up, including before 4 a.m., so eligible
  days keep their receipts and cannot repeat after calendar midnight.
  Up to 14 closed days catch up after downtime. Events and receipt history are
  retained for 14 days. A process crash between Discord accepting a page and
  saving its receipt can cause that message to repeat; normal restart/retry paths
  skip saved deliveries. Removed routes receive no further recaps.

- Public `/dailyrecap` replays the latest fully published automatic recap from
  the current channel, falling back to this server's announcement route. The
  legacy fallback channel is used only if it belongs to the invoking server;
  other servers' recaps are never used. The command defers before state reads,
  has no options, and explains when no recap has posted yet. Embed pages
  are saved when all automatic recap pages succeed, independently of the
  14-day event/receipt retention, and survive restarts and pruning. Partial newer
  recaps leave the last complete snapshot available. Existing shared pagination
  lets any reader navigate the replay without recomputing the statistics. Legacy
  saved date-range/count headers and collection explanations are removed on replay;
  legacy three- and four-column records are upgraded to the compact layout with
  games beside names, separate W/L and LP columns, and no unavailable-game counts.

### Synergy records

- Public `/synergy [server] [username] [teammate] [game_mode] [breakdown] [min_games] [size]`
  defaults to the invoking user's linked account via the shared resolver. Known
  group members come from current tracked, linked, and partymate registries plus
  the selected player. An explicit League/Discord teammate may be outside those
  registries and filters whole groups containing that identity.
- Reports exact known same-team groups of two through five players. `size:2-5`
  selects an exact total including the selected player. Larger groups never count
  as smaller subsets. Champions (default) groups complete champion lineups; Roles
  groups complete recognized role combinations. Records remain separate by exact
  roster, lineup, and queue; each distinct game counts once. Selected player is
  first, followed by companions in stable identity order. Wukong is normalized
  from MonkeyKing. Missing picks exclude the whole lineup for that breakdown;
  roles are never guessed for ARAM.
- Defaults to at least three games per lineup, configurable from 1 to 1,000;
  sorts by observed win rate, then games. One lineup per page shows group size,
  queue, W-L, sample count, and win rate above separate aligned Player and
  Champion/Role columns. Values remain below 1,024 characters even for five players.
  Any reader can navigate using the shared paginator. Empty and single-page
  follow-up responses omit the view keyword: Pycord rejects an explicit `view=None`.
- Uses retained player-scoped completed-match cache (normally 180 days), without
  a history scan. Excludes remakes, unavailable results, bots, co-op AI, Arena,
  unknown/custom queues, incomplete or duplicate team rosters, and inconsistent
  outcomes. Observed records do not establish causal synergy or premade membership.
  Acknowledges before player resolution and database work.

### Champion and counter reports

- `/championpool` and `/champstats` share one scan start per 60 seconds per
  invoking Discord user, across guilds, accounts, and options. A running scan
  blocks another call to either command even after the cooldown elapses.
  Admission is reserved before lookup or other slow work; rejections respond
  ephemerally without scanning. Completion or failure releases the active slot
  while retaining the start cooldown. This process-local guard prevents users
  from stacking expensive history scans; a bot restart resets it.
- `/champstats` posts a scan status message before loading all available Riot
  history and required timeline-backed data. It edits that channel message
  with the complete report after the scan, even if the interaction token has
  expired. A removed message ends the refresh quietly. The scan merges the
  local 180-day cache and falls back to cached results on Riot failure.
  Timelines are fetched only for deduplicated eligible champion games after
  queue/role/patch/duration/remake filtering.
- Filters include All Games/Ranked/Solo-Duo/Flex, Top/Jungle/Mid/ADC/Support,
  full or major/minor patch, and `since_patch` through newer patches. No patch
  means all available history. The report includes record and oldest-match time,
  selected keystone/other rune win rates, legendary items, and completed boots.
- Default `filter:true` hides choices below 1% usage or with at most two games;
  exactly 1% remains visible with at least three picks. False shows all choices.
  Games of 15 minutes or less are excluded. Breakdown state stays synchronized
  when returning to Runes, pages clamp on report changes, and anyone can use
  the controls. They never expire.
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
  author, selection, and page so controls survive process restarts. The background
  history scan edits the channel message directly, so interaction-token expiry
  cannot prevent the refresh; a deleted message ends the refresh quietly.
  Anyone viewing the result can use its role and page controls.
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
  at 5/10/15 minutes in separate inline columns. Previous/Next buttons advance
  through later five-minute marks in groups of up to three, including End using
  the last timeline frame; buttons acknowledge before chart rendering. `build_laning_comparison_chart`
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

## Runtime and storage

- Development, tests, debugging, commits, and GitHub pushes run on the Oracle VM
  in `/opt/discord-bot`, reached with `ssh backseater-prod`. No laptop checkout
  or project backup is retained. Migration backups stay private on the VM and
  outside Git; production runtime data and secrets remain excluded.

- `RiotClient` centralizes platform/regional routing, bounded retries, per-key
  sliding-window rate limits, bounded request fan-out, connection pooling,
  coalesced concurrent misses, and optional SQLite match/timeline caching.
  League/TFT keys have independent budgets; identical key values share one budget.
- Data Dragon provides champion metadata, aliases, emoji rendering, profile icons,
  map images, patch-aware caches, and internal-ID-to-display-name resolution.
  Shared champion-emoji lookup uses original named champion icons across commands
  and announcements, including Ryze, Yone, Aatrox, Viego, and Kayn; unavailable
  emoji fall back to plain text. Name-only `Renata Glasc`/`RenataGlasc` stats
  payloads resolve the `Renata` icon through the shared helper. `/emoji` documents this behavior.
- Environment-first configuration falls back to JSON, validates numeric/boolean
  settings, and supports IANA timezones, polling intervals, cache controls, and
  clean startup/shutdown. `LOG_LEVEL` / `log_level` defaults to INFO for routine
  operations; DEBUG adds item work, cache outcomes, request/response details, and
  branch diagnostics. The terminal uses compact rows with individually color-coded
  category labels, short project logger names, grouped multiline tracebacks, and explicit
  startup/shutdown lifecycle events; `bot.log` retains those categories plus full
  dates, millisecond timestamps, full logger names, and no terminal color codes.
- Central command diagnostics record invocation, every caller-supplied slash
  argument (including nested subcommand payloads), duration, and exceptions.
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
- Unexpected command, poller, synchronization, persistent-view, and Discord-event
  failures send sanitized DMs to the configured owner. Identical source/error pairs
  are suppressed for 15 minutes, known credentials are redacted, and DM failures
  are swallowed so notification problems cannot recurse into bot failures.
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
  registry changes, so overlapping workers cannot commit stale snapshots. Match
  and persistent-view records use versioned SQLite storage.
- Offline-capable `unittest` coverage includes domain logic, registration and option
  types/order, pollers, routing, publishing, commands, persistence, migrations,
  rate limits, caches, chart fallbacks, and startup errors. A suite-wide socket
  guard blocks accidental requests unless an integration run explicitly opts in;
  Matplotlib uses a temporary cache and test-created event loops are closed.
  Pull-request CI installs the Python 3.9 lock and runs full discovery; ordinary
  pushes do not trigger the workflow. Documentation checks also enforce
  synchronized editable root sections, a 12 KiB budget per root
  file, and existing local link targets/Markdown anchors. Exact commands are in
  [AGENTS.md](../AGENTS.md#verification).

## Hard rules

This section is owned by the user. Never add, edit, reword, reorder, or remove
anything in it — not to reflect a code change, not to keep `AGENTS.md` and
`CLAUDE.md` in sync, not as cleanup — unless the user explicitly asks for that
change in this section. Every other instruction in this file, including the
"update the implemented-features sections" rule below, stops at this heading.
If a hard rule conflicts with anything else in this file or with a request,
say so and follow the hard rule.

<!-- Add hard rules below this line. -->

### Embed interactions must never time out

Every embed button, dropdown, and other component interaction must be
acknowledged within Discord's interaction deadline, including before any slow
Riot API, chart-rendering, or database work.

### `/match` and `/livegame` are the announcements, not copies of them

Completed matches are one code path and live games are another, and the slash
command and the automatic announcement must each go through the same functions
in the same order.

Completed match — `announce.format_match` → `announce.build_announcement_embed`
→ `announce.MatchAnnouncementView` → `announce.remember_match_view_state`.
Callers: `/match` (`commands/match/match.py`), the match poller
(`tracker.poll_and_announce` → `announce.publish`), and `/selftest`.

Live game — `announce.format_live_game` → `announce.build_live_game_embed` →
`announce.LiveGameAnnouncementView` → `announce.remember_live_game_view_state`.
Callers: `/livegame` (`commands/player/profile.py`) and the live-game poller
(`tracker.poll_live_games_and_announce` → `announce.publish_live_games`).

Never write a second embed builder, view, dropdown, chart, or button set for
either surface. Never branch inside those functions on whether the caller is a
command or a poller. Never inline "just this one field" into a command handler.
Any change to layout, fields, dropdown options, charts, or buttons goes into the
shared function, so both surfaces change together in the same edit.

Where the two genuinely must differ, the difference is a field on the
announcement dataclass that the shared code reads, never a forked call path.
If a change cannot be expressed that way, say so and stop rather than forking.

Deploy and restart production after code or runtime changes. Documentation-only edits (such as Markdown files) require no deployment or restart unless explicitly requested. For restarts, first stop every production bot process, then use system Python 3.9.25: `/usr/bin/python3 main.py`, never `.venv`.

Push only when requested, always from the Oracle VM; never from the laptop.

When you add or change a public slash command, update the `/commands` command directory with its current description and usage instructions. Keep owner-only commands out of that directory.
Keep each public slash command in its own command module named after the command (for example, `/champ` belongs in `commands/champ.py`). Move reusable logic shared by multiple commands into a separate shared module.

For any new command that requires a player, default to the account linked to the invoking Discord user when no player is supplied. For commands that require a match, default to that account's most recent match. Player-oriented commands should provide an optional `server` option plus a single combined `username` option (League or Discord username) so callers can select another account when needed; resolve it through `shared.resolve_username`/`target_for`, never separate `summoner`/`user` options.

Default embed layout for tabular data should follow `/jungleproximity`'s pattern: separate inline `embed.add_field` columns (name/label, then each stat) so Discord aligns the values automatically, rather than single-line text with inline markdown or manual padding.

Every Discord embed field value must be 1,024 characters or fewer. When a
tabular result can exceed that limit, split it at row boundaries into multiple
fields or pages before sending or editing the embed.

Automatic match announcements and `/match` must always render the same embed layout, fields, toggles, and buttons; keep them using the same shared rendering code rather than diverging implementations. The same requirement applies to automatic live-game announcements and `/livegame`.

Whenever functionality is added, changed, or removed, update the implemented-
features sections in both `AGENTS.md` and `CLAUDE.md`, keep their development
instructions synchronized, and update all affected docstrings before finishing
the task.

## Development instructions

Read the [feature reference](docs/implemented-features.md) first; update it
and root summaries for behavior changes. Keep debugging in Git on the VM.

Keep all command responses concise: results, filters, dates, and page numbers.
Put command descriptions, methodology, and retention notes in `/commands` help
and docs. Keep error, empty, progress, and partial-result statuses brief.

Always update `/emoji` and its help/docs when adding, changing, or removing
emojis, markers, aliases, or their meanings.

Keep [Bot Patch Notes](PATCH_NOTES.md) for impactful public-facing changes only;
ask before adding entries. Never include owner-only command additions or updates.
Use the current date, newest first, with an America/Los_Angeles timestamp per
entry. `/patchnotes` reads this file directly; describe behavior, not commits.

Keep everything from this heading onward identical in `AGENTS.md` and `CLAUDE.md`.
Each root file has a 12 KiB budget; move editable detail to the reference.
`tests/test_agent_docs.py` checks synchronization, size, and local Markdown links.
It never rewrites either hard-rule section.

Declare slash-command options with `@discord.option(...)` decorators. Postponed
`discord.Option(...)` annotations can register as strings and fail at invocation.
Register commands explicitly through `bot_app.commands.register_all`; keep Riot
transport, retries, and rate limits in `RiotClient`.

## Project map

| Work area | Start here |
| --- | --- |
| Startup and settings | [main.py](main.py), [config.py](bot_app/config.py), [runtime.py](bot_app/runtime.py) |
| Commands and registration | [commands/](bot_app/commands/), [register_all](bot_app/commands/__init__.py); public usage in [commands/](commands/) |
| Player lookup and defaults | [shared.py](bot_app/commands/shared.py), [routing.py](bot_app/routing.py), [account_registry.py](bot_app/account_registry.py) |
| Announcements and polling | [announce.py](bot_app/announce.py), [announcement_models.py](bot_app/announcement_models.py), [tracker.py](bot_app/tracker.py) |
| Shared embeds, charts, controls | [render.py](bot_app/render.py), [charts.py](bot_app/charts.py), [command_views.py](bot_app/command_views.py) |
| Riot and metadata access | [riot.py](bot_app/riot.py), [ddragon.py](bot_app/ddragon.py); service boundary in [services/](bot_app/services/) |
| Persistence | [store.py](bot_app/store.py), [match_cache.py](bot_app/match_cache.py), [repositories/](bot_app/repositories/) |
| Analysis | [timeline.py](bot_app/timeline.py), [rating.py](bot_app/rating.py), [rating_baselines.py](bot_app/rating_baselines.py), [lane_matchups/](bot_app/lane_matchups/) |
| Regression tests | [tests/](tests/); named by subsystem |

## Verification

Run checks on the VM from `/opt/discord-bot` with system Python.
Registration/docs checks are network-free; rendering uses offline fallbacks.

```sh
# Documentation checks
/usr/bin/python3 -m unittest tests.test_agent_docs

# Registration and architecture
/usr/bin/python3 -m unittest tests.test_architecture

# Shared announcements, publishing, and rendering
/usr/bin/python3 -m unittest tests.test_announce tests.test_publish tests.test_render

# Full offline suite
/usr/bin/python3 -m unittest discover -s tests -t .
```

For public command changes, verify fresh startup logs contain
`Synchronized application commands` after the required production restart.

## Deployment

Work only through `ssh backseater-prod` in `/opt/discord-bot`: edit, test,
debug, commit, and push to GitHub on the VM. Keep no laptop checkout or backups.
Never sync laptop files onto production. Before code/runtime restarts, stop
`discord-bot.service` and every bot process; use `/usr/bin/python3 main.py`
(Python 3.9.25). Verify active service and fresh logs containing
`Synchronized application commands`. Docs need no restart. Keep secrets,
runtime data, caches, and migration backups out of Git.

## Implemented features

- [League announcements and controls](docs/implemented-features.md#league-announcements-and-persistent-controls):
  shared `/match` and `/livegame` pipelines, all completed queues, rank LP tracking,
  RTL, 🔥/🥶 streaks except Mayhem/Clash, 🎉/📉 rank changes, 🖐️ pentakills, ⭐/👥 over prior-game 👤/😈/🔄 cues,
  Em ranks,
  permanent global match deduplication, purple Mayhem/Clash embeds and ~Nmin 403 notices minus two minutes, timer-free live views, all-queue teammate summaries,
  third-live-game partymate detection, Mayhem three-lobby evidence, bold 👥 names, 😢 solo, 🫂 for two stars, 🤡 for five-stacks/Flex four-known teams, Arena team-colored charts, persistent
  Display/Chart controls retaining summaries, seven-slot ADC inventories, boot and Stormrazor item recovery,
  and delayed live-post cleanup.
- `/worlds` live dates, `/stats` newest-first archive, `/bracket` link-free Swiss/KO; feeds, team names/emojis, manual stats tabs, game/final scores, official feeds, no result links, live/score cleanup, /setchannel and /unsetchannel routing, 4 a.m. pings, Swiss W-L Bracket/KO; 1min near/live, 15min idle polls; no game-start alerts.
- [Teamfight Tactics](docs/implemented-features.md#teamfight-tactics): separate
  identities/state, completed-match announcements, rank/trait displays, filtered
  history, and startup rank baselines. TFT live polling remains disabled.
- [Accounts and player resolution](docs/implemented-features.md#accounts-and-player-resolution):
  username lookup, NA/EUW/KR resolution, linked-account defaults,
  match positions, /add types, /accounts partymates, owner /updatepartymate scans, and teammate LP changes.
- [Commands and statistics](docs/implemented-features.md#commands-and-champion-statistics):
  profiles, mastery, histories, OP.GG/Coachless builds, linked pool records naming Wukong and champion/counter/duo statistics with /duo game-mode labels,
  role-based OP.GG trends, paged patch notes, rank graphs, /bestplayer 180-day champion rankings with queue/min-games filters,
  /emoji legend, concise command responses, public command directories, deduplicated owner directory,
  tracked highlights, owner purging, a curated League directory,
  history refreshes, shared champion-report cooldowns/scan guards,
  server flake tier lists, exact cached /party group records, /synergy 2–5-player lineups with safe follow-ups, and 4 a.m. Pacific recaps with aligned Player (Games)/W/L/LP, tag-free names, persistent pages, catch-up receipts, and /dailyrecap replay.
- [Timeline analysis and ratings](docs/implemented-features.md#timeline-analysis-and-ratings):
  jungle proximity, three-at-a-time lane comparisons through game end, role-aware match ratings, and manually
  collected aggregate lane statistics and population baselines.
- [Runtime and storage](docs/implemented-features.md#runtime-and-storage): per-key Riot
  limits, caching, private migrated runtime data, serialized state transactions,
  typed configuration, champion icons/Renata aliases, sync retry, single-instance startup, argument-complete logs,
  locked dependencies, pull-request-only CI, and hermetic tests/docs checks.
  Runtime failures also produce sanitized, rate-limited owner DMs.

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

Do not start or restart the bot solely because files have changed. When explicitly asked to run the bot, first stop every existing bot process, then start it with system Python 3.9.6 using `/usr/bin/python3 main.py`; never run `main.py` from `.venv`.

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

Read the relevant section of the [feature reference](docs/implemented-features.md)
before changing a subsystem. Document current behavior and the reasons for unusual
constraints; keep debugging history in commits. Update both root feature summaries
and the relevant reference section when functionality changes.

Keep everything from this heading onward identical in `AGENTS.md` and `CLAUDE.md`.
Each root file has a 12 KiB budget, including its protected hard rules; shorten
editable prose or move details to the reference before raising that budget.
`tests/test_agent_docs.py` checks synchronization, size, and local Markdown links
(including section anchors). It never rewrites either hard-rule section.

Declare slash-command options with `@discord.option(...)` decorators. Postponed
`discord.Option(...)` annotations can register as strings and fail at invocation.
Register commands explicitly through `bot_app.commands.register_all`; keep Riot
transport, retries, and rate limits in `RiotClient`.

## Project map

Paths below are relative to the repository root.

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
| Meetups | [meetups/](bot_app/meetups/), [command](bot_app/commands/meetup/meetup.py) |
| Regression tests | [tests/](tests/); named by subsystem |

## Verification

Run from the repository root with dependencies from `requirements.txt` installed
for system Python. These commands do not start the bot or require live credentials.
Choose the focused tests for the behavior changed; use discovery for a full run.
Registration and documentation checks are network-free. Some existing rendering
tests attempt metadata/API requests and exercise fallbacks when offline.

```sh
# Documentation synchronization, size, and links (standard library only)
/usr/bin/python3 -m unittest tests.test_agent_docs

# Slash-command registration and architecture, including option order/types
/usr/bin/python3 -m unittest tests.test_architecture

# Shared announcements, publishing, and rendering
/usr/bin/python3 -m unittest tests.test_announce tests.test_publish tests.test_render

# Meetup parsing, persistence, interactions, and cleanup
/usr/bin/python3 -m unittest tests.test_meetups

# Full suite, runnable offline (also includes documentation checks)
/usr/bin/python3 -m unittest discover -s tests -t .
```

For public command changes, also inspect the startup log for the required sync
message using `rg -n 'Synchronized application commands' bot.log`. A historical
match does not prove the changed schema synced. If a fresh check needs a bot start,
follow the hard rule requiring explicit run authorization and report any remaining
verification gap.

## Implemented features

- [League announcements and controls](docs/implemented-features.md#league-announcements-and-persistent-controls):
  shared `/match` and `/livegame` pipelines, all completed queues, rank LP tracking,
  rotating-mode live-id recovery, match-only Arena team colors, persistent
  Display/Chart controls, inventory boot recovery, and delayed automatic live-post cleanup.
- [Teamfight Tactics](docs/implemented-features.md#teamfight-tactics): separate
  identities/state, completed-match announcements, rank/trait displays, filtered
  history, and startup rank baselines. TFT live polling remains disabled.
- [Accounts and player resolution](docs/implemented-features.md#accounts-and-player-resolution):
  combined username lookup, NA/EUW/KR platform resolution, default linked accounts,
  match-position selection, unlinked tracking, and shared-lobby teammate LP changes.
- [Commands and statistics](docs/implemented-features.md#commands-and-champion-statistics):
  profiles, mastery, histories, OP.GG/Coachless builds, champion/counter/duo statistics,
  role-based OP.GG trends, rank graphs, public command directories, and server flake tier lists.
- [Timeline analysis and ratings](docs/implemented-features.md#timeline-analysis-and-ratings):
  jungle proximity, lane comparisons, role-aware match ratings, and manually
  collected aggregate lane statistics and population baselines.
- [Meetups](docs/implemented-features.md#meetup-planning): server-scoped activity/time
  polls, atomic lock-in, paginated lists, confirmation previews, shared attendance,
  persistent controls, discussion threads, and automatic closure.
- [Runtime and storage](docs/implemented-features.md#runtime-and-storage): per-key Riot
  limits, caching, private migrated runtime data, serialized state transactions,
  typed configuration, sync retry, single-instance startup, structured logs,
  locked dependencies, CI, and hermetic tests including documentation drift checks.

# Development instruction

Before running `main.py`, stop the currently running Discord bot with `Ctrl+C`.
Run `main.py` anytime you change a file with the system Python:
`/usr/bin/python3 main.py`. Do not use the project's `.venv` by default.

When you add a new public slash command, also add it to the `/commands` command directory with a description and usage instructions. Keep owner-only commands out of that directory.

For any new command that requires a player, default to the account linked to the invoking Discord user when no player is supplied. For commands that require a match, default to that account's most recent match. Player-oriented commands should provide optional `server`, `summoner`, and `user` options so callers can select another account when needed.

## Implemented features

- Discord slash commands for profiles, live games using the shared registered-player live-game announcement renderer with reversible Flex/Solo rank toggles, filtered match history (game mode and champion), match lists, OP.GG links, PUUID lookup, mastery, match recaps, timelines, champion rotation, server status, and command discovery.
- Account registry commands for tracking, untracking, listing, refreshing, ownership reassignment, duplicate prevention, roster limits, and defaulting commands to the invoking user's linked account.
- Automatic ranked-match announcements with LP changes, rank information, Flex-to-Solo rank toggle, team columns, tracked-player-highlighted damage-dealt, timeline-based team gold-difference, damage-taken, and healing-and-shielding charts with navigation buttons, chronological processing, and durable deduplication.
- Automatic live-game announcements with lobby queue, duration, participant champions, ranks, Ranked Solo toggle for Flex lobbies, win rates, inferred positions, and one announcement per shared lobby.
- Guest announcements that remain anonymous, trigger only when Guest and CrispyPineapple are in the same game, and allow every queue when both are present. CrispyPineapple alone follows normal tracked-player behavior.
- Per-guild announcement-channel configuration with a mandatory global fallback, guild membership routing, conservative handling of transient Discord failures, and cached batch lookups.
- Rank history with persisted snapshots, LP attribution, promotion and season-reset handling, local-day summaries, LP graphs, and a paginated leaderboard.
- Champion statistics and duo records backed by a versioned SQLite match cache that excludes remakes, respects team membership, migrates older schemas, prunes expired records, and degrades safely when unavailable.
- Centralized Riot API access with platform and regional routing, bounded retries, shared rate limiting, response caching, connection pooling, and optional match-cache injection.
- Data Dragon champion metadata, aliases, emoji rendering, profile icons, map images, patch-aware caching, and internal-ID-to-display-name resolution.
- Timeline analysis for kill and death locations, solo kills, lane opponents, gold and XP differences, role-aware level caps, five-minute series, and minimap/chart rendering.
- Shared pagination, consistent embeds, ephemeral permission errors, deferred slow commands, mention-safe responses, owner-only self-tests, and explicit command registration.
- Environment-first configuration with JSON fallback, validation for numeric and boolean settings, IANA timezone support, configurable polling, cache controls, logging, and clean startup/shutdown tasks.
- Atomic JSON persistence for accounts and tracker state, bounded match and rank history, thread-safe read-modify-write operations, and cleanup when accounts are removed or reassigned.
- Network-free automated tests covering domain logic, pollers, routing, publishing, commands, persistence, migrations, caching, rate limiting, chart fallbacks, and startup error handling.

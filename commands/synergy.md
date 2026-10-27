# `/synergy`

Compare champion or role lineups for exact known groups of two through five
players using cached completed games. Each page shows the group, each member's
pick, queue, W/L, games, and win rate.

Usage: `/synergy [server] [username] [teammate] [game_mode] [breakdown] [min_games] [size]`

- Defaults to your linked account. `username` and `teammate` accept League Riot
  IDs or Discord usernames; `server` is optional.
- Known group members include tracked accounts, linked teammates, saved
  partymates, and the selected player. An explicit teammate need not be registered.
- `size:2-5` selects the exact total number of known players, including you.
  Without it, shows all sizes. Five-player groups never also count as smaller subsets.
- `teammate` filters whole groups containing that person.
- `breakdown:Champions` (default) compares whole champion lineups;
  `breakdown:Roles` compares whole role combinations. Missing picks skip the lineup.
- Player and pick rows align, with the selected player first.
- `game_mode` filters queues. Exact rosters, lineups, and queues remain separate.
- `min_games` defaults to 3; accepts 1–1,000. Lineups sort by win rate, then games.
  Sample sizes are always shown; a high win rate in few games is weak evidence.
- Uses retained cache (normally up to 180 days), without fetching missing matches.
  Shared teams do not prove a premade or that the lineup caused the result.
- Excludes remakes, unavailable results, bots, co-op AI, Arena, unknown/custom
  queues, incomplete/duplicate team rosters, and inconsistent outcomes.
- Anyone viewing the report can navigate its pages. Empty and single-page reports
  display without navigation buttons.

Results use concise embeds; usage and counting details remain in this help.

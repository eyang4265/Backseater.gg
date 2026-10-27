# `/party`

Show cached records for exact groups of three, four, or five known players on
one team, grouped separately by queue. Each page shows W/L, win rate, games,
most-played roles, and the most-used champion lineup.

Usage: `/party [server] [username] [game_mode] [size]`

- Defaults to the account linked to the invoking Discord user.
- `username` accepts a League Riot ID or Discord username; `server` is optional.
- `game_mode` filters queues; `size` selects exactly 3, 4, or 5 known players.
- Known players are tracked accounts, linked teammates, saved partymates, and the
  selected player. A five-player known group never also counts as a trio or quartet.
- Uses retained completed-match cache (normally up to 180 days), without fetching
  missing matches. Groups reflect current account registries, not proof of premades.
- Remakes, unknown queues, Arena, incomplete teams, and unavailable results are excluded.
- Anyone viewing the report can navigate its pages.

Results use concise embeds; usage and counting details remain in this help.

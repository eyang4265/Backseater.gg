# `/bestplayer`

Rank all tracked League players' champion records from the last **180 days** by highest win rate.

Usage: `/bestplayer [min_games] [queue]`

Example: `/bestplayer min_games:20 queue:Ranked Solo/Duo` includes champions
with at least 20 Ranked Solo/Duo games in the last 180 days.

- Each row is a player–champion pair meeting the inclusive `min_games` threshold.
- `min_games` must be a positive integer; omitted, it defaults to **10 or more games**.
- `queue` defaults to `All Games`; select `Ranked` for Solo/Duo and Flex combined,
  or a named queue such as Ranked Solo/Duo, Ranked Flex, ARAM, or Arena.
- Games and win rates count only the selected queue scope, combining roles.
- Includes all qualifying champions for every tracked account. Ties in win rate use games played, then champion name and account ID.
- Shows aligned player, champion, win rate, W/L, and games columns with plain
  `#1` rank labels; anyone can use the page buttons.
- Fetches Riot history pages within a fixed rolling 180-day window for all tracked
  players, reusing cached matches and fetching missing details, without a game-count cap.
- Both cached and fetched matches must start within that window; undated matches
  are excluded. The window stays fixed for the duration of a scan.
- Riot may not provide every lifetime game. Partial histories and unavailable
  matches are disclosed; cached games still contribute if a request fails.
- Shows scan progress and updates the same message when finished, even for long
  scans. Only one `/bestplayer` history scan runs at a time.
- Shared cache retention remains 180 days; older games do not contribute.
- Remakes, duplicate matches, missing outcomes, and unavailable results are excluded.
- MonkeyKing is displayed and grouped as Wukong.
- Linked teammates and inferred partymates are included only if also tracked.

Results use concise embeds; usage and counting details remain in this help.

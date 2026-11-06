# `/stats`

Shows individual Worlds game stats: player K/D/A, champions, each player's share
of their team's champion damage, game winner.

**Usage:** `/stats` (no options)

Opens directly to Game 1 of the latest played Worlds series. The dropdown selects
any played series from this year’s Worlds by matchup and Pacific date, newest first. Older/Newer series options
page through lists exceeding 23 series: **Newer series** appears at the top,
**Older series** at the bottom. Choosing another series resets to Game 1.
Game buttons switch between its played games. The command shows only game stats,
without schedule rows, date tabs, or a back button. Controls expire after 30 minutes.

Stats and damage charts display directly without spoiler formatting. Initial
responses and switches use the shared game and chart renderer.
The complete current-year series list is saved locally and refreshed at most
hourly, with recent scores merged from the shared 30-second schedule cache.
Saved final game stats and winners survive restarts and are reused; missing played-game stats are requested
after acknowledgement. Unavailable data shows a brief status; no played series
shows a concise empty status. Charts require Attach Files permission; text stats
remain available without it. Switching games/series removes any previous chart
when the selected game has none.

Use [`/worlds`](worlds.md) for schedules/results and [`/bracket`](bracket.md) for
the tournament bracket. Automatic final results show only the series score; game stats are available here.

Winners reuse saved game metadata and previously received series scores. A sweep,
the deciding game, or consecutive saved scores can establish a game winner;
ambiguous earlier games remain unavailable. Bans and result links are omitted.

Uses official LoL Esports feeds only. Game winners reuse saved metadata and scores;
missing winners do not trigger external metadata requests.

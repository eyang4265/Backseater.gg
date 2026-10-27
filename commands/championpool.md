# `/championpool`

Show the champions you have played by role, with champion icons, each champion's
record, and win rate. Champions with the most games appear first; the report
shows up to five per role. Names remain visible when an icon is unavailable.
Riot’s MonkeyKing appears as Wukong, with records combined under that name.
The player name in the report title links to their OP.GG profile.

You can start one report per 60 seconds across `/championpool` and `/champstats`.
Wait for your current scan to finish before starting another. Repeated calls
receive a private reply and do not start extra scans.

Usage: `/championpool [queue:<All SR|Ranked Solo/Duo|Ranked Flex>] [server:<platform>] [username:<League or Discord username>]`

The command defaults to your linked account and all standard Summoner's Rift
queues. It checks your latest 100 match IDs, using cached matches where possible.
Only games longer than 15 minutes with a recorded role count. The sample size is
shown in the report so short streaks are easy to interpret.

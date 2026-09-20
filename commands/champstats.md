# `/champstats`

Show your cached record for a champion, including rune, final-item, and boot
breakdowns.

By default, results include all available history. The `patch` option offers
the six newest patches and accepts a major/minor version such as `26.1` or a
full game version such as `26.1.1` to select one patch instead. Set
`since_patch:true` with a patch to include that patch and every newer patch.

The command posts a scan status message, then replaces it with the complete
report after checking available Riot history and required timelines. The
status message contains no partial cache-only results.

Usage: `/champstats champion:<name> [queue:<scope>] [role:<role>] [patch:<version>] [since_patch:<true|false>] [server:<platform>] [username:<League or Discord username>] [filter:<true|false>] [position:<1-10>]`

The Discord `filter` option defaults to true and hides choices played in fewer
than 1% of the eligible games or in 2 or fewer games. Set it to false to show
all choices. Choices at exactly 1% remain visible when they have at least 3
picks.

Anyone who can see the result can use its breakdown selector and pagination.
The controls remain usable after a bot restart.

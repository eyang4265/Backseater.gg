# `/champstats`

Show your cached record for a champion, including rune, final-item, and boot
breakdowns.

By default, results include all available history. The `patch` option offers
the six newest patches and accepts a major/minor version such as `26.1` or a
full game version such as `26.1.1` to select one patch instead. Set
`since_patch:true` with a patch to include that patch and every newer patch.

The command waits for the complete available Riot history and required
timelines before sending the result, so it never shows a partial cache-only
preview.

Usage: `/champstats champion:<name> [queue:<scope>] [role:<role>] [patch:<version>] [since_patch:<true|false>] [server:<platform>] [username:<League or Discord username>] [filter:<true|false>]`

The Discord `filter` option defaults to true and hides choices played in fewer
than 1% of the eligible games or in 2 or fewer games. Set it to false to show
all choices. Choices at exactly 1% remain visible when they have at least 3
picks.

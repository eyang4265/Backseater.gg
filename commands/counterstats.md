# `/counterstats`

Show a selected champion's win rate against every enemy champion in the
invoking player's completed match history.

Usage: `/counterstats champion:<name> role:<role>`

Optional options: `queue`, `filter`, `server`, and `username`. Set `filter:true`
to show only matchups above 1% usage. Results are sorted by highest win rate.
The required `role` selects the selected champion's role. The initial table
compares against enemies in every role. After the result appears, use the five buttons
for Top, Jungle, Mid, ADC, and Support to switch the enemy role. Large matchup lists include
Previous/Next buttons so every result stays within Discord's embed limits.

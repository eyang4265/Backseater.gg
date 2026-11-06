# `/unsetchannel`

Disable selected automatic announcements in this server. Requires **Manage Server**.

**Usage:** `/unsetchannel [type]`

`type` offers **Tracked Players**, **Worlds**, and **Both** (default).

Tracked Players disables automatic player match/live-game posts and daily recaps.
Worlds disables automatic schedules and series start/score/final alerts. Both
clears and disables both routes. The selected opt-outs also block default
fallback posts, persist across restarts, and leave other servers unaffected.
A single-category choice preserves the other category's destination and status.
Existing messages and manual commands remain available.

Re-enable with `/setchannel type:<Tracked Players|Worlds|Both> [channel]`.
An omitted channel uses the channel where you invoke the command.
`/setworldschannel [channel]` remains available for Worlds only.

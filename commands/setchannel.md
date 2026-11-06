# `/setchannel`

Set or re-enable this server's announcement destination. Requires **Manage Server**.

**Usage:** `/setchannel [channel] [type]`

- `channel`: defaults to the channel where you invoke the command.
- `type`: **Tracked Players** (default), **Worlds**, or **Both**.

Tracked Players includes automatic League/TFT player match/live-game posts and
player daily recaps. Worlds includes daily schedules, series starts, game scores,
and final results. Both sets both destinations to the selected channel.

Only the selected family is re-enabled after an opt-out. Existing settings for
the other family are retained. An existing inherited Worlds route is pinned to
its previous destination when changing only a configured player route.

Worlds/Both require View Channel, Send Messages, Embed Links, Add Reactions, and
Read Message History. `/setworldschannel [channel]` remains a Worlds-only shortcut.

# `/setworldschannel`

Choose a dedicated channel for this server's automatic Worlds posts: the 4 a.m.
Pacific schedule, series-start alerts, game scores, and final results.
Requires **Manage Server**. Equivalent to `/setchannel type:Worlds [channel]`.

**Usage:** `/setworldschannel [channel]`

`channel` defaults to the channel where you run the command. The bot checks for
View Channel, Send Messages, Embed Links, Add Reactions, and Read Message History
before saving. Attach Files is also needed for damage-share charts; without it,
player stats remain available as text. The setting persists across restarts and does not alter the
server's player-match announcement channel.

Before a Worlds override or opt-out is configured, Worlds uses the existing
announcement routes. After an override, Worlds posts only in the chosen channel for that
server. Running the command again replaces that destination and re-enables
Worlds after `/unsetchannel`. Player announcements remain disabled until
`/setchannel` re-enables them. `/setchannel` does
not change an existing Worlds override.

If set after 4 a.m., the new channel receives today's numbered schedule on the
next background check. React to that post for series-start pings in the new
channel; subscriptions on old-channel posts are not transferred. Existing old
posts remain. An ongoing series may receive a live notice in the new channel;
completed historical results are not backfilled. Manual `/worlds` and `/bracket`
still respond in the invoking channel.

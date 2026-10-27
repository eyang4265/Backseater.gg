# `/dailyrecap`

Show the last fully published automatic daily recap. Run `/dailyrecap` with no options.

- Uses the current channel's saved recap when available; otherwise uses this
  server's `/setchannel` announcement route, or the fallback announcement channel
  if it belongs to this server. Available only in servers.
- Shows Player (Games), W/L, and LP columns. Games appear beside each name;
  W/L and LP align under their own headers. Games includes unavailable
  results; W/L counts known outcomes (top four/bottom four for TFT), and leaves the record blank
  when none are known. Missing LP is also blank. Unavailable-game counts are hidden. LP sums
  recorded changes across ranked queues. Anyone
  viewing the command result can navigate its pages.
- Player rows show summoner names without Riot tags.
- Linked teammate names remain available for ARAM: Mayhem even when Riot
  omits names from its completion data.
- Automatic recaps use one message with page buttons when needed. Anyone can
  navigate, and automatic recap buttons survive bot restarts.
- Recaps post at 4 a.m. Pacific and cover the preceding 4 a.m.–4 a.m. gaming day.
- If no recap has posted yet, the command explains that the first is pending.
- The latest recap survives restarts and journal pruning. A partially posted
  newer recap does not replace the last complete one.

Results use concise embeds; usage and counting details remain in this help.

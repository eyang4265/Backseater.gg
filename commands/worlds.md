# `/worlds`

Shows the full Worlds schedule for today and the next date with scheduled games,
including every series on those dates. Tomorrow and Yesterday replace adjacent Pacific dates; other dated headings/buttons include the weekday. Dates use America/Los_Angeles; Discord
renders each start time in your local timezone. Each row shows the matchup,
stage, best-of format, start time, and current score/status. Team codes have
custom team-logo emojis; start/final announcements use official full team
names with those logos. Completed results
remain visible for the whole day. Upcoming series include countdowns; overdue
series say Awaiting start. Undrawn matchups appear as TBD.
Swiss rows show the entering record bracket, such as **Swiss · 2-2 Bracket**; knockout
rows say **Quarterfinal**, **Semifinal**, or **Final**. These labels also appear on
series-start alerts and result footers. Swiss records count series wins/losses;
incomplete history omits the record.

**Usage:** `/worlds` (no options)

Use [`/stats`](stats.md) to select a Worlds series and inspect individual game stats.

Matchups, scores, winners, live records, champions, and player stats
are visible without spoiler formatting. Damage charts appear directly in the
embed. Automatic Worlds schedules/results and `/bracket` also display directly.

On off days, shows a brief empty status for today and the full next game day.
Live series that started on an earlier Pacific date are also included under Today.
The message initially shows Today. Buttons run left to right: **Past Results**,
**Today**, then the next game date (or **Tomorrow**). Use the **Today** and next game date (for
example **Thu, Oct 29**) buttons to switch days in that same message. **Past Results**
(labelled **Yesterday** when applicable) shows every series from the most recent Pacific match date before today, including
any unfinished series. It skips off days and always excludes today, and shows a
brief empty status if no past match date is available. Only one day
is visible at a time; long days have Previous/Next page buttons. Scores and status
refresh automatically about once a minute for 14 days, keeping your selected tab/page.
Date controls and refreshes survive restarts. Deleted posts are not recreated. Compact date headings and
aligned columns have no spacer fields, footer, or broadcast links. The Worlds · Schedule title links
to the [LoL Esports schedule](https://lolesports.com/en-US/lolesports/leagues/first_stand,lck,lpl,msi,worlds).

Uses the same combined LoL Esports schedule/live feeds and shared 30-second cache
as automatic announcements. Published status can lag the broadcast. If no later games are
scheduled, the message says so; source failures show a brief retry message.

Use `/setworldschannel [channel]` (Manage Server) to choose a separate Worlds
announcement destination.

At 4 a.m. Pacific the bot automatically posts only today's schedule in Worlds
channels (catching up today's post after downtime). Today's series have numbered
reactions: 1️⃣ for series 1, 2️⃣ for series 2, and so on. React to receive a mention
when that series is confirmed live; remove your reaction to opt out. The automatic post has no next-day rows; on off days it reports no games today. Number subscriptions apply to the automatic post, not a
manual `/worlds` reply. Only the series start sends a live alert; later game
starts are silent. A green **Worlds · Series started** alert leads with the bold
matchup, then the stage, best-of format, and verified entering Swiss
record when available, with no game number. Subscriber pings happen once at series
start; final results do not ping again. Already finished games do not get retroactive live alerts. No roles are used.

Automatic results post the cumulative score after each completed game and the
final series score, without player stats, charts, or game tabs.
Use [`/stats`](stats.md) for player K/D/A, champions, damage-share charts, and
game winners. Results and Past Results omit title links.

Series/game live notices and intermediate score posts are removed when the series finishes and its final
result has posted. Cleanup survives restarts and retries failed deletions.

Uses official LoL Esports feeds only. Game winners reuse saved metadata and scores;
missing winners do not trigger external metadata requests.

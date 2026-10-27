# `/match`

Shows a completed League match using the same embed and controls as automatic
match announcements. Leave options blank for your linked account's latest match.

Finished ARAM: Mayhem, Clash, and ARAM Clash embeds use purple. Automatic
announcements reuse saved lobby/champion rows if Riot returns 403 after the
game ends, showing an approximate duration minus two minutes for polling delay.
These notices omit scores and post-game controls when results are unavailable.
Arena chart bars match the team heading colors and retain placement labels.

Usage: `/match [server] [username] [match_id] [position]`. `username` accepts a
League or Discord name. `match_id` accepts a full match ID or recent-game number
from 1 to 20; `position` selects a player from the match.

Partymates are admitted during a current same-team live game after two prior
completed games together in the same queue within seven days. Viewing a
completed match never adds new partymates. Saved identities retain bold names
and 👥 in shared team rows.

ARAM: Mayhem requires three distinct observed same-team live lobbies; finished
Mayhem games do not add evidence. The seven-day and half-of-last-ten rules apply.

Player markers: ⭐ tracked account, ⭐ linked teammate, 👥 inferred partymate.
When exactly two players would show ⭐ after solo/five-stack overrides, both
use 🫂 (`:people_hugging:`) in name, rank, and mastery views.
👤 marks a previous ally on your team again; 😈 marks a previous enemy opposing
you again. These use only your immediately previous cached match or observed lobby and remain
in name, rank, and mastery views. 🔄 marks a previous ally now opposing you or
a previous enemy now allied with you. Older games do not contribute. ⭐/👥 override 👤/😈/🔄.

Tracked players with no known same-team tracked, teammate, or partymate account
show 😢 instead of ⭐. This is inferred solo play, not a confirmed party roster.
Unknown queues/teams, bots, and Arena skip this marker.

Inferred five-stacks show 🤡 beside all five players instead of their other
markers, including ⭐ in rank and mastery views. A full team of tracked, linked
teammate, or saved partymate identities qualifies immediately. Otherwise all
five must share two prior
cached same-team games (three observed live lobbies for Mayhem), within seven
days and at least half of each player's last ten games.

Top player summaries show 🔥 for 3+ consecutive wins or 🥶 for 3+ consecutive
losses across League queues except ARAM: Mayhem, Clash, and ARAM Clash.
Completed games include that result; live games use the preceding results.
Unavailable results and remakes do not qualify.

🎉 replaces the streak emoji when that ranked game promoted the player; 📉
replaces it for demotions to a lower division or tier. Historical matches need
saved rank-change evidence.

🖐️ appears beside a featured player's K/D/A for at least one pentakill reported
by Riot in that match, alongside any streak or rank-change icon. It applies to
every League queue with available results, including Mayhem and Clash.

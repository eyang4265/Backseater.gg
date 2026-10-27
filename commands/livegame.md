# `/livegame`

Shows a live League lobby using the same embed and controls as automatic live
announcements. Leave options blank to use your linked account.

Usage: `/livegame [server] [username]`. `username` accepts a League or Discord name.

ARAM: Mayhem, Clash, and ARAM Clash omit the elapsed timer and streak icons.
The relative start time remains visible. Their automatic finished announcements
use purple and preserve saved lobby details if Riot denies the results.

On a third live game together, two prior cached same-team games in the same queue
within seven days can qualify players as inferred partymates, provided those
games are at least half of each player's last ten cached games in that window.
Eligible players are saved as inferred partymates and get bold names with 👥.
Saved partymates retain that marker in later team rows.

For ARAM: Mayhem, the third observed live lobby together can qualify the pair.
The current lobby counts; finished-game notices do not. Repeated views of the
same lobby count once. The seven-day and half-of-last-ten rules still apply.

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

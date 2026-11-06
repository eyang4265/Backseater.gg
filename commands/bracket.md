# `/bracket`

Shows the current Worlds bracket as a visible image in the embed, without a
spoiler cover. During Swiss: five round columns,
matchups grouped by their entering series record, team logos/names, game scores,
winner highlights, and eight advancing and eight eliminated slots with series
records. Undrawn matchups appear as TBD. The reply has no watch links or title link.

**Usage:** `/bracket` (no options)

Uses the same LoL Esports data and 30-second cache as `/worlds`. It reconstructs
series records from completed Swiss matches, independently of game scores.
Incomplete or inconsistent history shows a brief unavailable status rather than
an incorrect bracket. Logos are cached; missing logos use neutral circles.
The image updates when the command is run again; previously posted images do
not refresh automatically.

After Swiss completes, or once a knockout series starts (including its scheduled
start), the command switches to four quarterfinals, two semifinals, and a final
in a connected tree. Future knockout fixtures alone do not hide Swiss. Confirmed
teams, logos, and scores populate the cards; unassigned slots remain TBD.
Connections between known matches use published participants rather than assumed
pairings. The latest tournament in the feed determines the stage.

# /meetup

Plan an activity and time, lock the poll, and request attendance confirmations.

- `/meetup propose title:<name> activities:<a, b> times:<fri 7pm, sat 8pm> [location] [timezone]` creates a poll. Times use the supplied IANA timezone or the bot default.
- Use **Lock it in** to choose the final activity and time. Only an open poll can be locked; stale pickers cannot change an existing plan.
- `/meetup confirm [meetup_id] [include]` previews recipients for the organizer or a member with Manage Server. Click the preview button to send the ping.
- `/meetup list` shows all open meetups in the current server, five per page. Anyone who can see the list can use its page buttons.
- `/meetup cancel [meetup_id]` cancels a meetup for its organizer or a member with Manage Server.

Omitted meetup IDs use the current channel's latest open meetup. Explicit IDs must belong to the current server.

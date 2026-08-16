# VibeCode Bot

A Discord bot that tracks League of Legends accounts: it announces ranked
matches with LP changes and a damage chart, renders live lobbies with each
player's rank, and answers profile/mastery/timeline lookups — `/timeline`
plots that player's kills and deaths on the minimap and tracks their gold lead
over the lane opponent at every five-minute mark. `/matchhistory` shows up to
ten recent games with their result, champion, KDA, CS, queue, duration, and a
W/L score; mode and champion filters search recent games for up to ten matches.

## Running it

```bash
python3 -m pip install -r requirements.txt
```

Configuration is read from environment variables first, then from
`json/secrets.json` (gitignored — copy `json/secrets.example.json` and fill it
in). Required: `DISCORD_TOKEN`, `RIOT_API_KEY`, `DISCORD_OWNER_ID`,
`ANNOUNCEMENT_CHANNEL_ID`. Optional: `GUILD_IDS`, `TFT_API_KEY`,
`POLL_INTERVAL_SECONDS`, `MAX_TRACKED_ACCOUNTS`, `TIMEZONE` (IANA name),
`MATCH_CACHE_ENABLED`, `LOG_LEVEL`.

```bash
/usr/bin/python3 main.py
```

## Tests

```bash
python3 -m unittest discover -s tests -t .
```

The suite covers the pure logic, pollers, registry, LP history, guild routing,
and SQLite match cache, and needs neither network access nor a Discord token.

## Layout

`bot_app` is layered; each module only imports from the ones above it.

| Layer | Modules | Responsibility |
| --- | --- | --- |
| Configuration | `config`, `runtime` | Settings, and the handle to the Discord client |
| Domain | `routing`, `queues`, `positions`, `timeline` | Pure logic, no I/O |
| Services | `ranks`, `history`, `lp_history`, `leaderboard` | Rank math and stored-state queries; `ranks.fetch_ranks` delegates to Riot |
| Adapters | `riot`, `ddragon`, `store`, `match_cache` | Riot API, Data Dragon, JSON and SQLite state |
| Presentation | `emoji`, `render`, `charts` | Embeds, team columns, images |
| Features | `announce`, `tracker`, `guest_tracker`, `account_registry` | Polling, formatting, publishing, account management |
| Interface | `commands/` | Slash-command cogs |

Two rules keep it that way:

- **`riot.RiotClient` owns every call to the Riot API.** Rate limiting,
  retries, and response caching live there and nowhere else.
- **Commands are registered explicitly** via `commands.register_all(bot)`, not
  as an import side effect, so no module depends on import order.

## Notes for maintainers

- Data Dragon data is indexed once per patch; look champions up through
  `ddragon.catalog()` rather than scanning the payload.
- Per-player lookups in a ten-player lobby are fanned out concurrently in
  `render`. Adding a new per-player Riot call means adding it to
  `render._lookup_player`, not calling it in the row loop.
- Poller state files are written atomically. Anything persisted per player
  belongs on `store.PlayerState`.

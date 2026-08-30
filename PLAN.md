# `/champstats` implementation plan

## 1. Goal

Add a public `/champstats` command that shows how a player has performed on one
champion with the runes, final items, and boots they personally used.

Example:

```text
/champstats champion:Aphelios queue:Ranked Solo/Duo
```

The command should show:

- The player's total cached record and win rate on the champion.
- Win rate and sample size for each selected rune.
- Win rate and sample size for each non-boot item in the player's final inventory.
- Win rate and sample size for each boot choice in the final inventory.
- Filters for All Games, Ranked, Ranked Solo/Duo, and Ranked Flex.

This is descriptive personal history, not a recommendation engine. Every rate
must include its game count so a one-game 100% result is never presented as
strong evidence.

## 2. Command contract

Create one public command module named `bot_app/commands/champstats.py`.

```text
/champstats champion:<required> queue:<optional> server:<optional> username:<optional>
```

| Option | Behavior |
|---|---|
| `champion` | Required champion name, resolved through the Data Dragon catalog so aliases and punctuation work like other champion inputs. |
| `queue` | Optional fixed choice; defaults to `All Games`. |
| `server` | Optional preferred lookup platform using existing `SERVERS` choices. |
| `username` | Optional combined League or Discord username; omitted means the invoking user's linked account. |

Use `shared.target_for` and the existing `not_found_embed` path. Never add
separate `user` and `summoner` options. Defer before database/Data Dragon work,
run synchronous aggregation with `asyncio.to_thread`, and log supplied options
through `log_command`.

## 3. Queue semantics

Use exact queue IDs rather than name substring matching:

| Choice | Accepted queue IDs |
|---|---|
| All Games | Every cached queue ID |
| Ranked | `RANKED_QUEUE_IDS` (`420` and `440`) |
| Ranked Solo/Duo | `SOLO_QUEUE_ID` (`420`) |
| Ranked Flex | `FLEX_QUEUE_ID` (`440`) |

Keep this mapping in the pure statistics module, not the Discord handler.
Reject unknown scope values instead of silently treating them as All Games.

`All Games` is intentionally literal and may combine Summoner's Rift, ARAM,
Arena, and rotating modes. Show the selected scope prominently in the embed.
Every scope excludes remakes through the cache's existing `remake = 0` flag.

## 4. Data source and retention

Use completed matches already in `matches.sqlite`, then supplement them with a
paginated scan of all match IDs from Riot's Match-V5 history endpoint.
The immutable `matches.payload` Match-V5 JSON contains queue, PUUID, champion,
result, perks, and `item0` through `item6`. The indexed `participants` table can
find matching cached games by PUUID and champion; join it to `matches`, select
matching non-remake rows, and parse only those payloads. Riot-fetched payloads
are written through the existing client cache and merged with local rows using
match-id deduplication.

If Riot history cannot be fetched, continue with local cached results. If both
sources have no rows, say there are no completed games for that
player/champion/filter rather than implying the player never played it.

The footer should say results combine the latest Riot history scan with local
completed matches, and that local results are subject to the current 180-day
retention window.

No schema migration is needed initially. Add an aggregate table only if
measured latency later proves the bounded payload scan is too slow.

## 5. Code organization

Keep aggregation independent from Discord.

| File | Responsibility |
|---|---|
| `bot_app/champstats.py` | Queue scopes, immutable result dataclasses, extraction, aggregation, deduplication, and sorting. |
| `bot_app/match_cache.py` | Indexed query for relevant cached payloads. |
| `bot_app/ddragon.py` | Reusable item metadata needed to classify item IDs and boots. |
| `bot_app/commands/champstats.py` | Command, resolution, embeds, dropdown, paging, and errors. |
| `bot_app/commands/__init__.py` | Explicit command registration. |
| `bot_app/commands/info/commands.py` | `/help` directory and focused usage entry. |
| `tests/test_champstats.py` | Pure aggregation/classification tests. |
| `tests/test_match_cache.py` | Cache query and malformed-row tests. |
| `tests/test_champstats_command.py` | Options, embeds, controls, and error tests. |

## 6. Domain model

Define immutable dataclasses in `bot_app/champstats.py`:

```python
@dataclass(frozen=True)
class ChoiceRecord:
    identifier: int
    name: str
    games: int
    wins: int

    @property
    def losses(self) -> int: ...

    @property
    def win_rate(self) -> float: ...


@dataclass(frozen=True)
class ChampionStatsReport:
    champion: str
    queue_scope: str
    games: int
    wins: int
    keystones: tuple[ChoiceRecord, ...]
    runes: tuple[ChoiceRecord, ...]
    items: tuple[ChoiceRecord, ...]
    boots: tuple[ChoiceRecord, ...]
```

Do not put Discord formatting in domain objects. Sort choice lists by games
descending, win rate descending, display name case-insensitively, then numeric
ID. Do not hide low-sample rows.

## 7. Rune aggregation

For each eligible participant's `perks.styles`:

1. Treat the first selection in the primary style as the keystone.
2. Add every remaining primary and secondary selection to ordinary runes.
3. Count a rune at most once per match, even if malformed input repeats it.
4. Increment games once and wins once when the participant won.
5. Resolve names through `ddragon.rune_name`.
6. Fall back to `Rune <id>` instead of dropping unknown historical IDs.

Do not include `statPerks` shards in version one; they need separate naming and
would make the output less readable. Do not aggregate exact full rune pages in
version one because they fragment personal samples too aggressively.

## 8. Item and boot aggregation

Use `item0` through `item5`. Never use `item6`, which is the trinket slot.

For each eligible match:

1. Ignore zero or missing IDs.
2. Resolve metadata from the current Data Dragon item catalog.
3. Exclude entries tagged as trinkets or consumables.
4. Classify entries tagged `Boots` as boots.
5. Classify remaining entries as items.
6. Count the same ID at most once per match; two copies still represent one
   game using that item.

Call the view `Final Items`, not `Built Items` or `Completed Items`. Match-V5
shows end-of-game inventory, not purchase order, sold items, or completion
time. Components left at game end are valid observations and must not be
presented as completed legendaries.

Exclude boots from Final Items because they have their own view. Basic Boots
may appear when a game ends before an upgrade. If metadata is unavailable,
retain unknown nonzero IDs as `Item <id>` in Final Items rather than erasing
data. Inject metadata in tests so the suite stays network-free.

## 9. Data Dragon support

Extend `bot_app/ddragon.py` with a cached item-metadata accessor exposing item
ID, display name, and tags. Reuse the existing patch-keyed asset cache and
timeout. Prefer having `item_name()` read the same normalized map so item
metadata is not maintained twice.

The pure aggregator accepts the metadata mapping as an argument. That makes
classification deterministic and unknown-ID behavior easy to test.

## 10. Cache API

Add a narrow read method such as:

```python
def champion_match_payloads(
    self, puuid: str, champion: str
) -> list[dict[str, Any]]:
    ...
```

The SQL should join `participants AS p` to `matches AS m`, filter exact PUUID,
case-insensitive canonical champion name, and `p.remake = 0`, then order by
`m.game_end_ms ASC`. Select only `m.payload`; queue and win in the immutable
payload remain the aggregation source of truth.

Parse JSON inside the cache boundary. Skip and log malformed rows without
failing the whole command. Return an empty list when the database is unusable,
matching existing safe degradation.

The pure extractor must find the exact PUUID in each payload and verify the
champion again. Never substitute another match participant.

## 11. Discord presentation

Default to the `Runes` display.

```text
Aphelios Stats — Player Name
Ranked Solo/Duo · 42 games · 24W–18L · 57.1% WR
```

Dropdown choices:

- Runes
- Final Items
- Boots

Runes shows a Keystone section and an Other Runes section. The other views show
one table each. Follow `/jungleproximity`'s tabular layout rule with three
separate inline fields:

| Choice | Record | Win Rate |
|---|---|---|
| Fleet Footwork | 12W–8L (20) | 60.0% |

The parenthesized total makes sample size explicit without a fourth field that
would wrap. Use rune/item emotes when available, but always include plain names.

Show at most ten rows per table page. Add Previous/Next buttons only when the
selected view exceeds ten rows. Reset to page one when changing views and
disable impossible directions.

The interaction view should be scoped to the invoking user, acknowledge within
Discord's deadline, edit in place, and use a normal finite timeout. It does not
need persistent SQLite restoration. Disable controls on timeout when safe.

If a view has no rows, retain the overall record and state that no details were
present in the cached payloads.

## 12. Errors and empty states

Handle explicitly:

- Unknown champion: concise failure plus the standard supplied-options trailer.
- Player failure: `not_found_embed(username, server, ctx=ctx)`.
- No matches: name champion/scope and explain that the local cache and latest Riot history scan were searched.
- Missing perks/items: keep the match in overall W/L and omit only those details.
- Unknown rune/item IDs: stable numeric fallback labels.
- Zero games: never divide by zero.

Overall games is the count of eligible matches, never the sum of observations.

## 13. Registration and documentation

Register `champstats.setup` in `bot_app/commands/__init__.py` and add this public
entry to `bot_app/commands/info/commands.py`:

```text
**`/champstats`** — Show your cached win rates with a champion's runes, final items, and boots.
Usage: `champion:<name>`; optionally choose a queue or another player.
```

Update the implemented-features sections in both `AGENTS.md` and `CLAUDE.md`,
keep their development instructions synchronized, and update affected
docstrings. Never alter the Hard rules section.

Do not start or restart the bot merely because files changed.

## 14. Tests

All tests must be network-free; mock the Riot history and Match-V5 calls.

Pure aggregation coverage:

- Exact queue subsets for All, Ranked, Solo, and Flex.
- Overall W/L counted once per eligible match.
- Keystone and other-rune extraction; shards excluded.
- Duplicate rune/item IDs counted once per match.
- `item6`, consumables, and trinkets excluded.
- Boots separated from Final Items.
- Unknown IDs receive fallback names.
- Missing sections retain the overall result.
- Deterministic sorting and safe zero-game behavior.

Cache coverage:

- Exact PUUID and champion filtering.
- Case-insensitive champion matching.
- Remake exclusion and chronological order.
- Malformed JSON skipped without losing valid rows.
- Unavailable database returns an empty result.

Command coverage:

- Required champion and standard `server`/`username` options.
- Default player resolution and default All Games scope.
- Correct heading, record, games, and WR.
- Runes initial view and equal-length aligned fields.
- Pagination only beyond ten rows and reset on view change.
- Author-only controls.
- Unknown champion, lookup failure, and empty-data messages.
- Registration and `/help` discovery.

Run focused tests, then the complete suite:

```text
/usr/bin/python3 -m unittest tests.test_champstats tests.test_match_cache tests.test_champstats_command
/usr/bin/python3 -m unittest discover -s tests
```

## 15. Implementation order

1. Add failing aggregation tests and `bot_app/champstats.py`.
2. Add `MatchCache.champion_match_payloads` and cache tests.
3. Add reusable Data Dragon item metadata and classification tests.
4. Implement queue, rune, item, boot, fallback, and sorting logic.
5. Build the command, default embed, dropdown, and pagination.
6. Register it and update `/help`.
7. Update docstrings and synchronized implemented-features documentation.
8. Run focused tests and then the full network-free suite.
9. Review the diff for schema changes, Discord limits, and unrelated edits.

## 16. Acceptance criteria

Complete means:

- `/champstats champion:Aphelios` defaults to the invoking user's linked
  account and All Games.
- Queue scopes produce exact intended queue-ID subsets.
- Overall, rune, final-item, and boot records use only cached, completed,
  non-remake matches for the selected player/champion.
- Every displayed win rate includes exact sample size.
- Runes, Final Items, and Boots switch in one author-scoped view.
- Unknown metadata and partially malformed payloads degrade safely.
- The command paginates through all Riot match-history IDs and gracefully falls back to local cache results when those requests fail.
- It is registered and documented in `/help`.
- `AGENTS.md` and `CLAUDE.md` describe it and remain synchronized.
- Focused and full tests pass.
- The bot is not started or restarted unless explicitly requested.

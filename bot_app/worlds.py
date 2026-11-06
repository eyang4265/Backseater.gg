"""Worlds series starts and score-only game and final announcements.

This reads the site's schedule and live event JSON, never executes JavaScript, and
does not use the player Spectator API. Website schema changes fail closed.
"""

import asyncio
from datetime import datetime, timezone
import json
import logging
import re
import time
import threading
from zoneinfo import ZoneInfo

import discord

from .config import DATA_DIR
from .emoji import named_emoji
from .lolesports import fetch_worlds_page
from .store import read_json, write_json
from .worlds_routing import resolve_worlds_channels
from .worlds_stages import stage_label, label_swiss_brackets, enrich_stage_labels

LOGGER = logging.getLogger(__name__)
WATCH_URL = "https://www.twitch.tv/caedrel"
WATCH_LINKS = (f"Caedrel: [Twitch]({WATCH_URL})\n"
               "Official: [Twitch](https://www.twitch.tv/riotgames), "
               "[YouTube](https://www.youtube.com/@lolesports/live), "
               "[Kick](https://kick.com/riotgames)")
SCHEDULE_URL = "https://lolesports.com/en-US/lolesports/leagues/first_stand,lck,lpl,msi,worlds"
STATE_PATH = DATA_DIR / "worlds.json"
_LOCK = asyncio.Lock()
_CACHE_LOCK = threading.Lock()
_cached_series = None
_cache_until = 0.0
TEAM_EMOJI_NAMES = {"TLAW": "TL", "TL": "TL", "TES": "TOP", "TOP": "TOP", "GEN": "GenG"}


def team_label(series, index, *, official=False):
    """Prefix the team's configured logo; announcements use Riot's official name."""
    code = series["names"][index]
    names = series.get("team_names", series["names"]) if official else series["names"]
    name = discord.utils.escape_markdown(names[index][:100])
    icon = named_emoji(TEAM_EMOJI_NAMES.get(code.upper(), code))
    if icon is not None and getattr(icon, "available", True):
        return f"{icon} {name}"
    return name


def parse_series(html, *, include_unknown=False):
    """Validate events and label Swiss starts with verified entering series records."""
    decoder = json.JSONDecoder()
    events = {}
    for match in re.finditer(r'\{"__typename"\s*:\s*"EventMatch"', html):
        try:
            event, _ = decoder.raw_decode(html, match.start())
        except ValueError:
            continue
        if event.get("league", {}).get("slug") != "worlds":
            continue
        try:
            teams = event["matchTeams"]
            details = event["match"]
            best_of = details["strategy"]["count"]
            state = event["state"]
            if len(teams) != 2 or state not in ("unstarted", "inProgress", "completed"):
                continue
            ids = [str(team["teamId"]) for team in teams]
            names = [team.get("code") or team["name"] for team in teams]
            known = len(set(ids)) == 2 and "0" not in ids and all(name.upper() != "TBD" for name in names)
            if not known and not (include_unknown and state == "unstarted"):
                continue
            score = [(team.get("result") or {}).get("gameWins", 0) if not known
                     else team["result"]["gameWins"] for team in teams]
            if (type(best_of) is not int or best_of not in (1, 3, 5, 7)
                    or any(type(n) is not int or n < 0 or n > best_of // 2 + 1 for n in score)):
                continue
            completed = sum(game["state"] == "completed" for game in details["games"])
            if sum(score) != completed or sum(score) > best_of:
                continue
            if state == "completed" and (max(score) != best_of // 2 + 1 or score[0] == score[1]):
                continue
            if state == "unstarted" and completed:
                continue
            start = datetime.fromisoformat(event["startTime"].replace("Z", "+00:00"))
            if start.tzinfo is None:
                continue
            series = {"id": str(event["id"]), "teams": ids, "names": names,
                      "known_teams": known,
                      "team_names": [team.get("name") or name for team, name in zip(teams, names)],
                      "images": [team.get("image") or "" for team in teams],
                      "tournament": str(event.get("tournament", {}).get("id") or ""),
                      "games": [{"id": str(game["id"]), "number": game["number"]}
                                for game in details["games"] if game.get("state") == "completed"
                                and str(game.get("id", "")).isdigit()
                                and type(game.get("number")) is int and game["number"] > 0],
                      "active_games": [{"id": str(game["id"]), "number": game["number"]}
                                       for game in details["games"] if state == "inProgress"
                                       and max(score) < best_of // 2 + 1
                                       and game.get("state") == "inProgress"
                                       and str(game.get("id", "")).isdigit()
                                       and type(game.get("number")) is int
                                       and game["number"] == completed + 1 <= best_of],
                      "score": score, "state": state, "best_of": best_of,
                      "start": start.timestamp(), "stage": str(event.get("blockName") or "")}
        except (KeyError, TypeError, ValueError, AttributeError):
            continue
        previous = events.get(series["id"])
        priority = {"unstarted": 0, "inProgress": 1, "completed": 2}
        if previous is None or (sum(series["score"]), priority[state]) > (sum(previous["score"]), priority[previous["state"]]):
            events[series["id"]] = series
    if not events:
        raise ValueError("LoL Esports page contains no valid Worlds series")
    series_list = sorted(events.values(), key=lambda event: (event["start"], event["id"]))
    label_swiss_brackets(series_list)
    return series_list


def fetch_series(*, include_unknown=False):
    """Share cached scores and verified stage labels; schedules also receive TBD slots."""
    global _cached_series, _cache_until
    with _CACHE_LOCK:
        if _cached_series is not None and time.monotonic() < _cache_until:
            series = _cached_series
        else:
            series = parse_series(fetch_worlds_page(), include_unknown=True)
            enrich_stage_labels(series)
            _cached_series = series
            _cache_until = time.monotonic() + 30
        return series if include_unknown else [s for s in series if s["known_teams"]]


def status_embeds(series_list, *, now=None, series_markers=None, include_next=True):
    """Show dated matchups/results, Swiss brackets or knockout rounds, and markers."""
    now = now or datetime.now(timezone.utc)
    pacific = ZoneInfo("America/Los_Angeles")
    today = now.astimezone(pacific).date()
    days = {}
    for series in sorted(series_list, key=lambda s: (s["start"], s["id"])):
        day = datetime.fromtimestamp(series["start"], pacific).date()
        days.setdefault(day, []).append(series)
    later = [day for day in days if day > today]
    selected = [("Live", day) for day in sorted(days) if day < today
                and any(s["state"] == "inProgress" for s in days[day])]
    selected.append(("Today", today))
    if later and include_next:
        selected.append(("Next game day", min(later)))
    embeds = []
    for label, day in selected:
        rows = []
        for series in days.get(day, []):
            a, b = (team_label(series, i) for i in range(2))
            timestamp = int(series["start"])
            x, y = series["score"]
            if series["state"] == "completed":
                status = f"{x}–{y} · Final"
            elif series["state"] == "inProgress":
                status = f"{x}–{y} · Live"
            else:
                status = "Scheduled" if timestamp > now.timestamp() else "Awaiting start"
            start = f"<t:{timestamp}:t>"
            if series["state"] == "unstarted" and timestamp > now.timestamp():
                start += f" · <t:{timestamp}:R>"
            stage = discord.utils.escape_markdown(stage_label(series))[:80]
            marker = (series_markers or {}).get(series["id"], "")
            prefix = f"{marker} " if marker else ""
            matchup = f"**{a} vs {b}** · Bo{series['best_of']} · {stage}"
            rows.append((f"{prefix}{matchup}",
                         start, status))
        pages, current = [], [[], [], []]
        for row in rows:
            if current[0] and any(len("\n".join(column + [cell])) > 1000
                                  for column, cell in zip(current, row)):
                pages.append(current)
                current = [[], [], []]
            for column, cell in zip(current, row):
                column.append(cell)
        if current[0] or not pages:
            pages.append(current)
        for page, columns in enumerate(pages, 1):
            date_label = ({1: "Tomorrow", -1: "Yesterday"}.get((day - today).days)
                          or f"{day:%a, %b %d, %Y}")
            title = f"Worlds · {label} · {date_label}"
            if len(pages) > 1:
                title += f" · {page}/{len(pages)}"
            description = ""
            if not rows:
                description = "No Worlds series scheduled today."
            embed = discord.Embed(title=title, description=description, url=WATCH_URL, color=0xC8AA6E)
            for name, column in zip(("Series", "Start", "Score / Status"), columns):
                if column:
                    embed.add_field(name=name, value="\n".join(column), inline=True)
            if not later and include_next:
                embed.description += "\nNo later games scheduled."
            embeds.append(embed)
    return embeds


def schedule_message_embeds(day_embeds):
    """Combine compact, aligned day columns without spacer fields or a footer.

    Discord can hide later embeds with the same URL. A single embed keeps both
    dates visible with a LoL Esports title link and no broadcast-link block.
    """
    pages = []

    def new_page():
        return discord.Embed(title="Worlds · Schedule", description="",
                             url=SCHEDULE_URL, color=0xC8AA6E)

    current = new_page()
    for day in day_embeds:
        heading = re.sub(r", \d{4}", "", day.title.removeprefix("Worlds · "))
        heading = heading.replace("Next game day", "Next games")
        if day.fields:
            section = [(heading if index == 0 else "\u200b",
                        f"**{field.name}**\n{field.value}", True)
                       for index, field in enumerate(day.fields)]
        else:
            section = [(heading, "No Worlds series scheduled.", False)]
        extra_size = sum(len(name) + len(value) for name, value, _ in section)
        # Reserve space for a page number and optional empty-schedule status.
        if current.fields and (len(current.fields) + len(section) > 25 or len(current) + extra_size > 5700):
            pages.append(current)
            current = new_page()
        for name, value, inline in section:
            current.add_field(name=name, value=value, inline=inline)
        if "No later games scheduled." in day.description and "No later games scheduled." not in current.description:
            current.description += "\nNo later games scheduled."
    pages.append(current)
    if len(pages) > 1:
        for index, page in enumerate(pages, 1):
            page.title += f" · {index}/{len(pages)}"
    return pages


def announcement(series, *, final=False, score=False, game_start=None):
    """Render series starts, cumulative game scores, and final series results."""
    a, b = (team_label(series, i, official=True) for i in range(2))
    x, y = series["score"]
    live_title = series.get("live_title") or ("Worlds Swiss Stage" if series["stage"].lower() == "swiss"
                                               else f"Worlds {series['stage']}".strip())
    if game_start is not None:
        title = f"{live_title} · Game {game_start}"
        text = f"**{a} vs {b}**\nSeries: {x}–{y}"
    elif final:
        title = "Worlds · Final result"
        text = f"**{a} {x}–{y} {b}**\n{a if x > y else b} wins the series."
    elif score:
        title = f"Worlds · Game {x + y} result"
        text = f"**{a} {x}–{y} {b}**"
    else:
        title = "Worlds · Series started"
        text = f"**{a} vs {b}**\nWorlds {stage_label(series)} · Best of {series['best_of']}"
    if game_start is not None:
        text = title + "\n" + text
        title = "Worlds · Live game"
    elif score:
        title = "Worlds · Game result"
    color = 0x2ECC71 if game_start is None and not (final or score) else 0xC8AA6E
    embed = discord.Embed(title=title, description=text if final or score else text + "\n\n" + WATCH_LINKS, color=color)
    embed.set_footer(text=f"{stage_label(series)[:100]} · Best of {series['best_of']} · LoL Esports")
    return embed


async def poll_worlds(bot, *, now=None):
    """Post series starts and cumulative scores after games, including the final.

    Delivery receipts survive restarts and seed historical results without spam.
    Failed sends/deletions retry; live and score posts are removed after final delivery.
    Player stats remain available through the manual /stats command.
    """
    async with _LOCK:
        current = now or datetime.now(timezone.utc)
        channel_ids = await resolve_worlds_channels(bot)
        if not channel_ids:
            return
        series_list = await asyncio.to_thread(fetch_series)
        state = await asyncio.to_thread(read_json, STATE_PATH, {"channels": {}})
        for channel_id in sorted(channel_ids):
            saved = state["channels"].setdefault(str(channel_id),
                       {"since": current.timestamp(), "series": {}})
            for series in series_list:
                receipts = saved["series"]
                key = series["id"]
                if key not in receipts:
                    old = series["state"] == "completed" and series["start"] < saved["since"]
                    receipts[key] = {"started": old, "score": series["score"] if old else [0, 0],
                                     "final": old, "teams": series["teams"], "historical": old,
                                     "live_notice_scan_done": True, "score_cleanup_tracked": True}
                    await asyncio.to_thread(write_json, STATE_PATH, state)
                receipt = receipts[key]
                if not receipt.get("score_cleanup_tracked"):
                    # Recover intermediate posts sent before their IDs were tracked.
                    receipt["live_notice_scan_done"] = False
                    receipt["score_cleanup_tracked"] = True
                    await asyncio.to_thread(write_json, STATE_PATH, state)
                receipt["series_snapshot"] = series
                # The live query can reverse the schedule's team order.
                if receipt["teams"] != series["teams"]:
                    if set(receipt["teams"]) != set(series["teams"]):
                        continue
                    order = [series["teams"].index(team) for team in receipt["teams"]]
                    series = dict(series)
                    for field in ("teams", "names", "team_names", "images", "score"):
                        if field in series:
                            series[field] = [series[field][index] for index in order]
                score = series["score"]
                if any(n < old for n, old in zip(score, receipt["score"])):
                    continue
                final = series["state"] == "completed" and not receipt["final"]
                updated = sum(score) > sum(receipt["score"])
                started = series["state"] == "inProgress" and not receipt["started"]
                cleanup_due = (series["state"] == "completed" and receipt.get("final")
                               and (bool(receipt.get("live_message_ids"))
                                    or (current.timestamp() - series["start"] <= 12 * 3600
                                        and not receipt.get("live_notice_scan_done"))))
                if not (started or final or updated or cleanup_due):
                    continue
                try:
                    channel = bot.get_channel(channel_id) or await bot.fetch_channel(channel_id)
                    if channel is None:
                        continue
                    if started:
                        from .worlds_schedule import send_start_alert
                        await send_start_alert(channel, series, announcement(series), receipt,
                                               lambda: asyncio.to_thread(write_json, STATE_PATH, state))
                        receipt["started"] = True
                        await asyncio.to_thread(write_json, STATE_PATH, state)
                    if final:
                        message = await channel.send(embed=announcement(series, final=True),
                                                     allowed_mentions=discord.AllowedMentions.none())
                        receipt["final_message_id"] = message.id
                    elif updated and not receipt["final"]:
                        message = await channel.send(embed=announcement(series, score=True),
                                                     allowed_mentions=discord.AllowedMentions.none())
                        receipt.setdefault("live_message_ids", []).append(message.id)
                    if final or updated:
                        # Preserve observed cumulative scores for manual game-winner lookup.
                        for game in series.get("games", []):
                            if game["number"] <= sum(score):
                                receipt.setdefault("stats_games", {}).setdefault(game["id"], {
                                    "series": series, "status": "unrequested"})
                        receipt.update(score=score, final=receipt["final"] or final, started=True)
                        await asyncio.to_thread(write_json, STATE_PATH, state)
                    if series["state"] == "completed" and receipt.get("final"):
                        from .worlds_live_cleanup import delete_series_live_notices
                        await delete_series_live_notices(bot, channel, series, receipt,
                                                        lambda: asyncio.to_thread(write_json, STATE_PATH, state))
                    LOGGER.info("Worlds announcement channel=%s series=%s score=%s state=%s",
                                channel_id, key, score, series["state"])
                except discord.DiscordException:
                    LOGGER.exception("Could not post Worlds update channel=%s; will retry", channel_id)

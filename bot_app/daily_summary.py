"""Persistent per-channel announcement recaps for 4 a.m. Pacific gaming days."""

import asyncio
from collections import defaultdict
from datetime import datetime, time, timedelta, timezone
import logging
import re
import threading
from zoneinfo import ZoneInfo

import discord

from .config import DATA_DIR, get_settings
from .paginator import Paginator
from .render import make_embed
from .store import (load_accounts, load_guild_channels, load_teammates, load_tft_accounts,
                    read_json, remember_embed_button_state, write_json)

LOGGER = logging.getLogger(__name__)
STATE_PATH = DATA_DIR / "daily_summary.json"
PACIFIC = ZoneInfo("America/Los_Angeles")
_STATE_LOCK = threading.RLock()
_POLL_LOCK = asyncio.Lock()


class RecapPaginator(Paginator):
    """One public recap message with persistent, immediately acknowledged page buttons."""

    def __init__(self, embeds, *, page=0):
        """Restore aligned pages and the selected page with stable button IDs."""
        if not embeds:
            raise ValueError("A recap needs at least one page")
        embeds = [_align_recap_embed(embed) for embed in embeds]
        super().__init__(embeds, author_id=0, page_size=1, timeout=None,
                         render_page=lambda items, _page, _pages: items[0])
        for original, action in zip(tuple(self.children), ("first", "previous", "next", "last")):
            button = discord.ui.Button(label=original.label, style=original.style,
                                       custom_id=f"daily_recap:{action}", disabled=original.disabled)
            button.callback = original.callback
            self.remove_item(original)
            self.add_item(button)
            setattr(self, f"{action}_button", button)
        self.page = min(max(page, 0), self.max_page)
        self._sync_buttons()

    async def _go_to(self, page, interaction):
        """Acknowledge before editing or saving the page for restart restoration."""
        await interaction.response.defer()
        self.page = min(max(page, 0), self.max_page)
        self._sync_buttons()
        await interaction.edit_original_response(embed=self.render(), view=self)
        await remember_recap_view(interaction.message, interaction.channel_id, self)


async def remember_recap_view(message, channel_id, view):
    """Save every page and the selected page in the existing persistent-view store."""
    await asyncio.to_thread(remember_embed_button_state, message.id, channel_id, "daily_recap",
                            {"embeds": [embed.to_dict() for embed in view.items], "page": view.page})


def completed_window(now):
    """Return the latest closed local 4-to-4 window, preserving DST boundaries."""
    local = now.astimezone(PACIFIC)
    end_day = local.date() if local.hour >= 4 else local.date() - timedelta(days=1)
    end = datetime.combine(end_day, time(4), PACIFIC)
    start = datetime.combine(end_day - timedelta(days=1), time(4), PACIFIC)
    return start, end


def _prune(state, now):
    """Bound journal and receipt retention even during days with no activity."""
    cutoff = int(now.timestamp()*1000) - 14 * 86400000
    oldest_day = (now.astimezone(PACIFIC).date() - timedelta(days=14)).isoformat()
    for saved in state["channels"].values():
        saved["events"] = {k: e for k, e in saved["events"].items() if e["posted_ms"] >= cutoff}
        saved["sent"] = {k: v for k, v in saved["sent"].items() if k >= oldest_day}


def _state(now_ms):
    """Load private journal state; a new install starts collecting from now."""
    state = read_json(STATE_PATH, {})
    state.setdefault("started_ms", now_ms)
    state.setdefault("channels", {})
    return state


def _known_names(game_type):
    """Resolve registered identities, including League teammates missing from Mayhem payloads."""
    registries = (load_tft_accounts(),) if game_type == "tft" else (load_teammates(), load_accounts())
    return {account.puuid: account.riot_id for registry in registries for account in registry.values()
            if account.riot_id and account.riot_id not in ("Unknown player", "Unknown teammate", "Unknown TFT player")}


def record_announcement(channel_id, announcement, *, now=None, names=None):
    """Journal automatic matches with registered names even for nameless Mayhem participants."""
    metadata = announcement.match.get("metadata", {})
    match_id = metadata.get("matchId") or metadata.get("match_id")
    if not isinstance(channel_id, int) or not match_id:
        return
    now = now or datetime.now(timezone.utc)
    now_ms = int(now.timestamp() * 1000)
    known = _known_names(announcement.game_type)
    known.update({puuid: name for puuid, name in (names or {}).items()
                  if name and name != "Unknown player"})
    players = []
    for p in announcement.match.get("info", {}).get("participants", []):
        puuid = p.get("puuid")
        if puuid not in announcement.highlight_puuids:
            continue
        name = p.get("riotIdGameName") or p.get("summonerName")
        tag = p.get("riotIdTagline")
        if name and tag:
            name += "#" + tag
        name = known.get(puuid) or name or p.get("riotId") or "Unknown player"
        lp_text = announcement.lp_changes.get(puuid, "")
        lp = re.fullmatch(r"([+-]?\d+) LP", lp_text)
        players.append({"puuid": puuid, "name": name, "win": p.get("win"),
                        "placement": p.get("placement"), "lp": int(lp[1]) if lp else None})
    event = {"posted_ms": now_ms, "game_type": announcement.game_type,
             "unavailable": announcement.result_unavailable, "players": players}
    with _STATE_LOCK:
        state = _state(now_ms)
        channel = state["channels"].setdefault(str(channel_id), {"events": {}, "sent": {}, "started_ms": now_ms})
        channel["events"].setdefault(f"{announcement.game_type}:{match_id}", event)
        _prune(state, now)
        write_json(STATE_PATH, state)


def _align_recap_embed(embed):
    """Keep W/L and LP in native columns, with games beside each tag-free name."""
    embed = embed.copy()
    fields = {field.name: field.value.splitlines() for field in embed.fields}
    game_key = "G" if "G" in fields else "Games"
    if set(fields) == {"Player", game_key, "W/L · LP"}:
        players, games, records = fields["Player"], fields[game_key], fields["W/L · LP"]
        if len(players) == len(games) == len(records) and all(" · " in row for row in records):
            stats, lps = [], []
            for games_played, row in zip(games, records):
                record, lp = row.rsplit(" · ", 1)
                record = "" if record in ("-", r"\-", "—") else record.replace(" / ", "/")
                stats.append(f"{games_played} {record}".rstrip())
                lps.append("−" + lp[1:] if lp.startswith("-") else lp)
            embed.clear_fields()
            for name, values in (("Player", players), ("Games W/L", stats), ("LP", lps)):
                embed.add_field(name=name, value="\n".join(values), inline=True)
    for index, field in enumerate(embed.fields):
        if field.name == "G · W/L":
            stats = []
            for line in field.value.splitlines():
                games_played, _separator, record = line.partition(" · ")
                record = "" if record in ("-", r"\-", "—") else record
                stats.append(f"{games_played} {record}".rstrip())
            embed.set_field_at(index, name="Games W/L", value="\n".join(stats), inline=field.inline)
        if field.name == "G W/L":
            embed.set_field_at(index, name="Games W/L", value=field.value, inline=field.inline)
        if field.name == "LP":
            # Discord strips empty lines; invisible cells retain each player's row.
            values = ["\u200b" if line in ("", "-", r"\-", "—") else line
                      for line in field.value.splitlines()]
            embed.set_field_at(index, name=field.name, value="\n".join(values) or "\u200b", inline=field.inline)
        if field.name == "Player":
            names = []
            for line in field.value.splitlines():
                suffix = " (TFT)" if line.endswith(" (TFT)") else ""
                name = line[:-len(suffix)] if suffix else line
                names.append(name.rsplit("#", 1)[0] + suffix)
            embed.set_field_at(index, name=field.name, value="\n".join(names), inline=field.inline)
        if field.name == "Games":
            embed.set_field_at(index, name="G", value=field.value, inline=field.inline)
        if field.name == "W/L · LP":
            value = "\n".join("\\" + line if line.startswith("- · ") else line
                              for line in field.value.splitlines())
            embed.set_field_at(index, name=field.name, value=value, inline=field.inline)
    fields = {field.name: field.value.splitlines() for field in embed.fields}
    if set(fields) == {"Player", "Games W/L", "LP"}:
        names, stats, lps = fields["Player"], fields["Games W/L"], fields["LP"]
        records = [re.fullmatch(r"(\d+)(?:\s+(\d+/\d+))?", row) for row in stats]
        if len(names) == len(records) == len(lps) and all(records):
            embed.clear_fields()
            values = (("Player (Games)", [f"{name} ({row[1]})" for name, row in zip(names, records)]),
                      ("W/L", [row[2] or "\u200b" for row in records]), ("LP", lps))
            for name, rows in values:
                embed.add_field(name=name, value="\n".join(rows), inline=True)
    return embed


def _record_with_lp(record, lp):
    """Keep W/L and LP on aligned rows without Markdown list markers."""
    record = re.sub(r" · \d+ unavailable$", "", record)
    if re.fullmatch(r"0\s*/\s*0", record):
        record = "-"
    if record == "-":
        record = r"\-"
    lp = re.sub(r" \(\d+ games\)$", "", lp)
    return f"{record} · {lp}"


def summary_embeds(events, start, end):
    """Render tag-free aligned records, recovering older registered identities by PUUID."""
    totals = defaultdict(lambda: {"name": "", "games": 0, "wins": 0, "unknown": 0, "lp": 0, "lp_games": 0})
    known = {}
    for event in events:
        for player in event["players"]:
            key = (event["game_type"], player["puuid"])
            row = totals[key]
            name = player["name"]
            if not name or name == "Unknown player":
                kind = event["game_type"]
                if kind not in known:
                    known[kind] = _known_names(kind)
                name = known[kind].get(player["puuid"], name)
            if name and (name != "Unknown player" or not row["name"]):
                row["name"] = name
            if event["game_type"] == "tft":
                place = player.get("placement")
                win = 0 < place <= 4 if isinstance(place, int) and place > 0 else None
            else:
                win = player.get("win")
            if event["unavailable"] or not isinstance(win, bool):
                row["unknown"] += 1
            else:
                row["games"] += 1
                row["wins"] += int(win)
            if player.get("lp") is not None and not event["unavailable"]:
                row["lp"] += player["lp"]
                row["lp_games"] += 1
    rows = sorted(totals.items(), key=lambda item: (item[0][0], item[1]["name"].casefold()))
    chunks = [rows[i:i+8] for i in range(0, len(rows), 8)] or [[]]
    embeds = []
    for index, chunk in enumerate(chunks):
        embed = make_embed(
            "",
            title=f"Daily Recap — {start:%b %d}",
        )
        if chunk:
            names, games, records = [], [], []
            for (game_type, _puuid), row in chunk:
                names.append(discord.utils.escape_markdown(row["name"][:80])[:100] + (" (TFT)" if game_type == "tft" else ""))
                games.append(str(row["games"] + row["unknown"]))
                record = f'{row["wins"]} / {row["games"]-row["wins"]}' if row["games"] else "-"
                lp = f'{row["lp"]:+d}' if row["lp_games"] else "—"
                records.append(_record_with_lp(record, lp))
            embed.add_field(name="Player", value="\n".join(names), inline=True)
            embed.add_field(name="G", value="\n".join(games), inline=True)
            embed.add_field(name="W/L · LP", value="\n".join(records), inline=True)
        elif not events:
            embed.description = "No completed-game announcements this gaming day."
        embed.set_footer(text=f"Page {index+1}/{len(chunks)}")
        embeds.append(_align_recap_embed(embed))
    return embeds


def _load_due(channel_ids, now, start, end):
    """Snapshot closed days, including up to 14 days of downtime or send retries."""
    now_ms = int(now.timestamp() * 1000)
    due = []
    with _STATE_LOCK:
        state = _state(now_ms)
        _prune(state, now)
        started = datetime.fromtimestamp(state["started_ms"] / 1000, PACIFIC)
        first_day = max(started.date() - timedelta(days=1), start.date() - timedelta(days=13))
        day = first_day
        while day <= start.date():
            window_start = datetime.combine(day, time(4), PACIFIC)
            window_end = datetime.combine(day + timedelta(days=1), time(4), PACIFIC)
            start_ms, end_ms = int(window_start.timestamp()*1000), int(window_end.timestamp()*1000)
            if end_ms > state["started_ms"]:
                for channel_id in sorted(channel_ids):
                    saved = state["channels"].setdefault(str(channel_id), {"events": {}, "sent": {}, "started_ms": now_ms})
                    # New routes start on their first observed gaming day, never backfill unrelated channels.
                    if end_ms <= saved.get("started_ms", state["started_ms"]):
                        continue
                    key = day.isoformat()
                    if saved["sent"].get(key) == "done":
                        continue
                    events = [e for e in saved["events"].values() if start_ms <= e["posted_ms"] < end_ms]
                    due.append((channel_id, events, int(saved["sent"].get(key, 0)), window_start, window_end))
            day += timedelta(days=1)
        for channel_id in channel_ids:
            state["channels"].setdefault(str(channel_id), {"events": {}, "sent": {}, "started_ms": now_ms})
        write_json(STATE_PATH, state)
    return due


def _mark_page(channel_id, day, progress, embeds=None):
    """Persist delivery receipts and the complete snapshot behind a recap message."""
    with _STATE_LOCK:
        state = _state(0)
        saved = state["channels"][str(channel_id)]
        saved["sent"][day] = progress
        if progress == "done" and embeds is not None:
            saved["latest"] = {"day": day, "embeds": [embed.to_dict() for embed in embeds]}
        write_json(STATE_PATH, state)


def latest_recap(channel_id):
    """Read saved embeds with three columns and no unavailable-result counts.

    The latest completed recap remains available after journal events are pruned.
    A partially sent newer recap does not replace it. Legacy layouts are upgraded
    without rewriting saved state; games sit beside player names, W/L and LP have
    separate columns, and missing values are blank without losing row alignment.
    """
    with _STATE_LOCK:
        state = read_json(STATE_PATH, {})
        saved = state.get("channels", {}).get(str(channel_id), {}).get("latest", {})
        payloads = saved.get("embeds", [])
        embeds = [discord.Embed.from_dict(payload) for payload in payloads]
        for embed in embeds:
            if [field.name for field in embed.fields] == ["Player", "Record", "Recorded LP change"]:
                records = [re.fullmatch(
                    r"(\d+) / (\d+) (?:W / L|Top 4 / Bottom 4)(?: · (\d+) unavailable)?", line,
                ) for line in embed.fields[1].value.splitlines()]
                names = embed.fields[0].value
                lps = embed.fields[2].value.splitlines()
                if records and all(records) and len(records) == len(names.splitlines()) == len(lps):
                    games = [str(int(row[1]) + int(row[2]) + int(row[3] or 0)) for row in records]
                    values = [f"{row[1]} / {row[2]}" + (f" · {row[3]} unavailable" if row[3] else "")
                              for row in records]
                    lps = [re.sub(r" \(\d+ games\)$", "", value) for value in lps]
                    embed.clear_fields()
                    for name, value in [("Player", names), ("Games", "\n".join(games)),
                                        ("W/L", "\n".join(values)), ("LP Change", "\n".join(lps))]:
                        embed.add_field(name=name, value=value, inline=True)
            if [field.name for field in embed.fields] == ["Player", "Games", "W/L", "LP Change"]:
                names, games, records, lps = [field.value.splitlines() for field in embed.fields]
                if len(names) == len(games) == len(records) == len(lps):
                    embed.clear_fields()
                    for name, values in [("Player", names), ("Games", games),
                                         ("W/L · LP", [_record_with_lp(record, lp)
                                                       for record, lp in zip(records, lps)])]:
                        embed.add_field(name=name, value="\n".join(values), inline=True)
            if embed.description:
                embed.description = re.sub(
                    r"\A[^\n]+ 4 a\.m\. → [^\n]+ 4 a\.m\. \(Pacific\)\n"
                    r"\*\*\d+ completed-game announcements\*\*\n",
                    "", embed.description,
                )
            if embed.description:
                embed.description = embed.description.replace(
                    "Counts use announcements posted in this channel during the gaming day; "
                    "collection starts when this feature is enabled.", "",
                ).strip()
            if embed.footer and embed.footer.text:
                embed.set_footer(text=embed.footer.text.split(" · LP sums", 1)[0])
        return [_align_recap_embed(embed) for embed in embeds]


async def poll_daily_summary(bot, *, now=None):
    """Post each closed day as one message with persistent buttons for extra pages."""
    async with _POLL_LOCK:
        now = now or datetime.now(timezone.utc)
        start, end = completed_window(now)
        routes = await asyncio.to_thread(load_guild_channels)
        channel_ids = set(routes.values()) | {get_settings().announcement_channel_id}
        channel_ids.discard(0)
        due = await asyncio.to_thread(_load_due, channel_ids, now, start, end)
        for channel_id, events, _progress, start, end in due:
            try:
                channel = bot.get_channel(channel_id) or await bot.fetch_channel(channel_id)
                if channel is None:
                    continue
                embeds = summary_embeds(events, start, end)
                view = RecapPaginator(embeds) if len(embeds) > 1 else None
                options = {"view": view} if view is not None else {}
                message = await channel.send(embed=embeds[0], allowed_mentions=discord.AllowedMentions.none(), **options)
                if view is not None:
                    await remember_recap_view(message, channel_id, view)
                await asyncio.to_thread(_mark_page, channel_id, start.date().isoformat(), "done", embeds)
                LOGGER.info("Posted daily recap channel=%s gaming_day=%s", channel_id, start.date())
            except discord.HTTPException:
                LOGGER.exception("Could not post daily recap to channel=%s; will retry", channel_id)

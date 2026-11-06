"""Confirmed final Worlds player stats, cached snapshots, and retryable message enrichment."""

import asyncio
from datetime import datetime, timezone
import logging
import math

import discord

from .charts import build_worlds_damage_share_chart
from .config import DATA_DIR
from .lolesports import EsportsUnavailable, fetch_game_stats
from .store import read_json, write_json
from .worlds import announcement, team_label
from .emoji import champion_emoji
from .worlds_player_names import player_name
from .worlds_game_details import load_game_details

LOGGER = logging.getLogger(__name__)
CACHE_DIR = DATA_DIR / "worlds_stats"


def parse_final_stats(window, details, series, game):
    """Require a finished frame and matching game, team, participant, and K/D/A identities."""
    if str(window.get("esportsGameId")) != game["id"] or str(window.get("esportsMatchId")) != series["id"]:
        raise ValueError("Stats game identity mismatch")
    finished = [f for f in window.get("frames", []) if f.get("gameState") == "finished"]
    if not finished:
        raise ValueError("Final game frame not available")
    frame = max(finished, key=lambda f: f["rfc460Timestamp"])
    detail = next((f for f in details.get("frames", [])
                   if f.get("rfc460Timestamp") == frame["rfc460Timestamp"]), None)
    if detail is None:
        raise ValueError("Final damage frame not available")
    detail_players = {p["participantId"]: p for p in detail["participants"]}
    if len(detail_players) != 10 or len(detail["participants"]) != 10:
        raise ValueError("Incomplete final participants")
    teams, used = [], set()
    for side in ("blue", "red"):
        metadata = window["gameMetadata"][side + "TeamMetadata"]
        team_id = str(metadata["esportsTeamId"])
        counters = {p["participantId"]: p for p in frame[side + "Team"]["participants"]}
        players = []
        if len(metadata["participantMetadata"]) != 5 or len(counters) != 5:
            raise ValueError("Incomplete final team")
        for player in metadata["participantMetadata"]:
            pid = player["participantId"]
            if pid in used:
                raise ValueError("Duplicate final participant")
            used.add(pid)
            stats = detail_players[pid]
            kda = [stats[key] for key in ("kills", "deaths", "assists")]
            if any(type(n) is not int or n < 0 for n in kda):
                raise ValueError("Invalid final K/D/A")
            if kda != [counters[pid][key] for key in ("kills", "deaths", "assists")]:
                raise ValueError("Final counters have not synchronized")
            share = stats.get("championDamageShare")
            if type(share) not in (int, float) or not math.isfinite(share) or not 0 <= share <= 1:
                raise ValueError("Invalid champion damage share")
            if not player.get("summonerName") or not player.get("championId"):
                raise ValueError("Missing player identity")
            players.append({"id": pid, "name": player["summonerName"], "champion": player["championId"],
                            "kda": kda, "damage_share": share})
        if not .98 <= sum(p["damage_share"] for p in players) <= 1.02:
            raise ValueError("Incomplete team damage shares")
        teams.append({"id": team_id, "side": side, "players": players})
    if {t["id"] for t in teams} != set(series["teams"]) or len(used) != 10:
        raise ValueError("Stats teams do not match this series")
    return {"schema": 1, "game_id": game["id"], "series_id": series["id"], "game_number": game["number"],
            "timestamp": frame["rfc460Timestamp"], "teams": teams}


def load_saved_stats(series, game):
    """Read a previously validated game snapshot without network requests."""
    path = CACHE_DIR / (game["id"] + ".json")
    cached = read_json(path, None)
    if (isinstance(cached, dict) and cached.get("schema") == 1
            and cached.get("game_id") == game["id"] and cached.get("series_id") == series["id"]
            and {t["id"] for t in cached.get("teams", [])} == set(series["teams"])):
        return cached
    return None


def final_stats(series, game, now):
    """Cache validated final snapshots; timestamped requests avoid opening-game defaults."""
    cached = load_saved_stats(series, game)
    if cached is not None:
        return cached
    path = CACHE_DIR / (game["id"] + ".json")
    # The stats ingestion edge can reject timestamps too close to wall time.
    timestamp = datetime.fromtimestamp(int((now.timestamp() - 30) // 10) * 10, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    window, details = fetch_game_stats(game["id"], timestamp)
    try:
        stats = parse_final_stats(window, details, series, game)
    except (KeyError, TypeError, AttributeError) as error:
        raise ValueError("Incomplete final Worlds stats") from error
    write_json(path, stats)
    return stats


def add_stats_fields(embed, stats, series):
    """Show Player · Champion labels and statistics in aligned team columns."""
    by_team = {team["id"]: team for team in stats["teams"]}
    for index, team_id in enumerate(series["teams"]):
        players = by_team[team_id]["players"]
        names = []
        for player in players:
            icon = champion_emoji(None, name=player["champion"])
            label = str(icon) if icon is not None and getattr(icon, "available", True) else player["champion"]
            name = player_name(player["name"], series, team_id)
            names.append(f"{discord.utils.escape_markdown(name[:50])} · {label}")
        values = (names, ["/".join(map(str, p["kda"])) for p in players],
                  [f"{p['damage_share'] * 100:.1f}%" for p in players])
        for title, rows in zip((f"Team {index + 1}", "K/D/A", "Damage share"), values):
            embed.add_field(name=title, value="\n".join(rows), inline=True)
    footer = embed.footer.text if embed.footer else ""
    embed.set_footer(text=f"{footer} · Game {stats['game_number']} final stats".strip(" ·"))
    return embed


def game_stats_embed(series, number, stats=None, *, unavailable=False):
    """Show saved game winners and stat columns; unused slots show series results."""
    finished = series.get("state") == "completed" or max(series["score"]) >= series["best_of"] // 2 + 1
    unused = number > sum(series["score"])
    embed = announcement(series, final=finished and unused, score=not unused)
    embed.url = None
    embed.description = embed.description.split("\n\nCaedrel:", 1)[0]
    if unused:
        if not finished:
            embed = announcement(series, score=True)
            embed.title = "Worlds · Series result"
            embed.url = None
            embed.description = embed.description.split("\n\nCaedrel:", 1)[0]
        return embed
    embed.title = "Worlds · Game stats"
    embed.description = f"Game {number}\n" + embed.description
    game = next((g for g in series.get("games", []) if g["number"] == number), None)
    details = load_game_details(series, game) if game else None
    winner = details.get("winner") if details else None
    if winner in series["teams"]:
        team = team_label(series, series["teams"].index(winner), official=True)
        embed.description += f"\n**{team} wins Game {number}.**"
    else:
        embed.description += "\nWinner unavailable."
    if stats is not None:
        add_stats_fields(embed, stats, series)
    else:
        status = "Stats unavailable" if unavailable else "Stats pending"
        embed.set_footer(text=f"{embed.footer.text} · {status}")
    return embed


def stats_due(series, receipt, now):
    """Fetch current games for final-series tabs without historical backfills or extra posts."""
    if receipt.get("historical") or not receipt.get("started") or now.timestamp() - series["start"] > 12 * 3600:
        return False
    for game in series.get("games", []):
        if game["number"] > sum(receipt["score"]):
            continue
        record = receipt.get("stats_games", {}).get(game["id"])
        if record is None or (record.get("status") == "pending" and record.get("next_retry", 0) <= now.timestamp()):
            return True
    return False


async def publish_game_stats(channel, series, receipt, now, save):
    """Cache each game silently; only the final series message receives stats and game tabs."""
    for game in sorted(series.get("games", []), key=lambda g: g["number"]):
        if game["number"] > sum(receipt["score"]):
            continue
        record = receipt.setdefault("stats_games", {}).setdefault(game["id"], {
            "series": series, "created": now.timestamp(), "status": "pending"})
        if record["status"] != "pending" or record.get("next_retry", 0) > now.timestamp():
            continue
        if now.timestamp() - record["created"] >= 3600:
            if record.get("message_id") and record.get("final"):
                try:
                    message = await channel.fetch_message(record["message_id"])
                    embed = message.embeds[0]
                    footer = embed.footer.text if embed.footer else ""
                    embed.set_footer(text=footer.replace("Stats pending", "Stats unavailable"))
                    await message.edit(embed=embed, allowed_mentions=discord.AllowedMentions.none())
                except discord.DiscordException:
                    LOGGER.debug("Could not update unavailable stats status", exc_info=True)
            record["status"] = "unavailable"
            await save()
            continue
        snapshot = series if record.get("final") else record["series"]
        try:
            stats = await asyncio.to_thread(final_stats, snapshot, game, now)
            if not record.get("final") or not record.get("message_id"):
                record["status"] = "cached"
                await save()
                continue
            embed = game_stats_embed(snapshot, game["number"], stats)
            from .worlds_stats_views import WorldsStatsView
            view = WorldsStatsView(series, channel.id, game["number"])
            guild = getattr(channel, "guild", None)
            can_attach = (guild is None or not hasattr(channel, "permissions_for")
                          or channel.permissions_for(guild.me).attach_files)
            chart = await asyncio.to_thread(build_worlds_damage_share_chart, stats, snapshot) if can_attach else None
            options = {"view": view}
            if chart is not None:
                embed.set_image(url="attachment://" + chart.filename)
                options["file"] = chart
            else:
                embed.set_footer(text=embed.footer.text + " · Chart unavailable")
            message = await channel.fetch_message(record["message_id"])
            await message.edit(embed=embed, attachments=[], allowed_mentions=discord.AllowedMentions.none(), **options)
            record["status"] = "sent"
            record["tabs_version"] = 9
            await save()
            LOGGER.info("Worlds final player stats channel=%s game=%s", channel.id, game["id"])
        except discord.NotFound:
            record["status"] = "deleted"
            await save()
        except (EsportsUnavailable, ValueError, discord.DiscordException) as error:
            record["attempts"] = record.get("attempts", 0) + 1
            record["next_retry"] = now.timestamp() + min(60 * 2 ** min(record["attempts"] - 1, 3), 300)
            await save()
            LOGGER.info("Worlds final player stats pending game=%s: %s", game["id"], error)

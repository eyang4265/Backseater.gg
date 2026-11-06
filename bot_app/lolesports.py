"""Public esports website transports, separate from Riot's player API."""

import requests
import json
import time
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from urllib.parse import urlsplit, urlunsplit

WORLDS_URL = "https://lolesports.com/en-US/leagues/worlds"
LIVE_URL = "https://lolesports.com/api/gql"
# homeEvents operation ID from the public site's persisted-query manifest.
LIVE_QUERY_ID = "e4da9fd0ab89ecdb783912d74799688d1f52f1edc9b87497d3409c74d9b4291a"
STATS_URL = "https://feed.lolesports.com/livestats/v1"


class EsportsUnavailable(RuntimeError):
    """Public LoL Esports data could not be fetched."""


def fetch_game_stats(game_id, starting_time):
    """Read window/detail frames at the same timestamp; never use the default opening frames."""
    if not str(game_id).isdigit():
        raise EsportsUnavailable("Invalid esports game ID")
    try:
        payloads = []
        for kind in ("window", "details"):
            response = requests.get(f"{STATS_URL}/{kind}/{game_id}",
                                    params={"startingTime": starting_time}, timeout=(3, 8))
            response.raise_for_status()
            payloads.append(response.json())
        return tuple(payloads)
    except (requests.RequestException, ValueError) as error:
        raise EsportsUnavailable("Worlds player stats unavailable") from error


@lru_cache(maxsize=128)
def fetch_team_logo(url):
    """Cache optional logos from Riot's known image hosts; failures use initials."""
    parts = urlsplit(url)
    if parts.hostname not in {"static.lolesports.com", "am-a.akamaihd.net", "cmsassets.rgpub.io"}:
        return None
    try:
        response = requests.get(urlunsplit(("https", parts.netloc, parts.path, parts.query, "")),
                                timeout=(3, 5), allow_redirects=False)
        response.raise_for_status()
        if response.status_code != 200 or len(response.content) > 2_000_000:
            return None
        return response.content
    except requests.RequestException:
        return None


def fetch_worlds_page():
    """Combine page events with fresh live and recently completed series.

    Live series are absent from the server-rendered schedule. A minute-bucket
    refresh key avoids the CDN's multi-minute stale score cache without creating
    a new cache entry for every command invocation. Query failures
    fail closed rather than silently treating a missing live series as ended.
    """
    try:
        response = requests.get(WORLDS_URL, timeout=(5, 20), headers={"Cache-Control": "no-cache"})
        response.raise_for_status()
        page = response.text
        now = datetime.fromtimestamp(time.time(), timezone.utc)
        variables = {"hl": "en-US", "sport": ["lol"], "leagues": ["98767975604431411"],
                     "eventState": ["inProgress", "completed"], "eventType": "match", "pageSize": 40,
                     "eventDateStart": (now - timedelta(days=2)).strftime("%Y-%m-%dT00:00:00.000Z"),
                     "eventDateEnd": (now + timedelta(days=1)).strftime("%Y-%m-%dT23:59:59.999Z")}
        extensions = {"persistedQuery": {"version": 1, "sha256Hash": LIVE_QUERY_ID}}
        live = requests.get(LIVE_URL, params={"operationName": "homeEvents",
                            "variables": json.dumps(variables, separators=(",", ":")),
                            "extensions": json.dumps(extensions, separators=(",", ":")),
                            "_refresh": str(int(time.time() // 60))},
                            headers={"x-apollo-operation-name": "homeEvents",
                                     "apollographql-client-name": "Backseater.gg",
                                     "apollographql-client-version": "1.0",
                                     "Cache-Control": "no-cache"}, timeout=(5, 20))
        live.raise_for_status()
        payload = live.json()
        if payload.get("errors") or not isinstance(payload["data"]["esports"]["events"], list):
            raise ValueError("Invalid Worlds live-event response")
        return page + "\n" + json.dumps(payload, separators=(",", ":"))
    except (requests.RequestException, ValueError, KeyError, TypeError, AttributeError) as error:
        raise EsportsUnavailable("LoL Esports unavailable") from error


def fetch_worlds_year(year):
    """Read every played Worlds event in a UTC year, splitting saturated date ranges."""
    extensions = {"persistedQuery": {"version": 1, "sha256Hash": LIVE_QUERY_ID}}

    def read_range(start, end):
        variables = {"hl": "en-US", "sport": ["lol"], "leagues": ["98767975604431411"],
                     "eventState": ["inProgress", "completed"], "eventType": "match", "pageSize": 40,
                     "eventDateStart": start.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
                     "eventDateEnd": (end - timedelta(milliseconds=1)).isoformat(timespec="milliseconds").replace("+00:00", "Z")}
        try:
            response = requests.get(LIVE_URL, params={"operationName": "homeEvents",
                                    "variables": json.dumps(variables, separators=(",", ":")),
                                    "extensions": json.dumps(extensions, separators=(",", ":")),
                                    "_refresh": str(int(time.time() // 3600))},
                                    headers={"x-apollo-operation-name": "homeEvents",
                                             "apollographql-client-name": "Backseater.gg",
                                             "apollographql-client-version": "1.0"}, timeout=(5, 20))
            response.raise_for_status()
            payload = response.json()
            events = payload["data"]["esports"]["events"]
            if payload.get("errors") or not isinstance(events, list):
                raise ValueError("Invalid Worlds history response")
            # A full page cannot establish completeness. Split rather than guessing
            # unsupported pagination arguments or silently dropping older games.
            if len(events) >= 40:
                if end - start <= timedelta(days=1):
                    raise ValueError("Worlds history date range remains truncated")
                middle = start + timedelta(days=max(1, (end - start).days // 2))
                return read_range(start, middle) + read_range(middle, end)
            if any(not isinstance(event, dict) or not event.get("id") for event in events):
                raise ValueError("Invalid Worlds history event")
            return events
        except (requests.RequestException, ValueError, KeyError, TypeError, AttributeError) as error:
            raise EsportsUnavailable("Worlds history unavailable") from error

    events = read_range(datetime(year, 1, 1, tzinfo=timezone.utc),
                        datetime(year + 1, 1, 1, tzinfo=timezone.utc))
    return json.dumps({"events": events}, separators=(",", ":"))

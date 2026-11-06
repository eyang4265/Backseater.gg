"""Persistent opt-outs that prevent default channels from bypassing /unsetchannel."""

import threading

from .config import DATA_DIR
from .store import read_json, write_json

STATE_PATH = DATA_DIR / "announcement_preferences.json"
_LOCK = threading.Lock()


def disabled_guilds(kind):
    """Return servers explicitly disabled for player announcements or Worlds."""
    state = read_json(STATE_PATH, {})
    if not isinstance(state, dict):
        return set()
    return {str(guild) for guild, prefs in state.items()
            if isinstance(prefs, dict) and prefs.get(kind) is False}


def set_route_enabled(guild_id, kind, enabled):
    """Re-enable only the announcement family selected by its channel command."""
    with _LOCK:
        state = read_json(STATE_PATH, {})
        state.setdefault(str(guild_id), {})[kind] = enabled
        write_json(STATE_PATH, state)


def disable_server(guild_id):
    """Disable both automatic families before removing a server's channel routes."""
    with _LOCK:
        state = read_json(STATE_PATH, {})
        state[str(guild_id)] = {"matches": False, "worlds": False}
        write_json(STATE_PATH, state)


def clear_server_preferences(guild_id):
    """Remove preferences when the bot leaves the server."""
    with _LOCK:
        state = read_json(STATE_PATH, {})
        state.pop(str(guild_id), None)
        write_json(STATE_PATH, state)


def channel_is_disabled(channel, disabled):
    """Apply opt-outs to a resolved channel, including a legacy default channel."""
    guild = getattr(channel, "guild", None)
    return guild is not None and str(guild.id) in disabled

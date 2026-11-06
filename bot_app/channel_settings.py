"""Shared category-specific configuration for player and Worlds announcements."""

from .channel_preferences import disable_server, disabled_guilds, set_route_enabled
from .guild_routing import set_guild_channel, unset_guild_channel
from .store import load_guild_channels
from .worlds_routing import load_worlds_channels, remove_worlds_channel, set_worlds_channel

TYPE_LABELS = {"players": "Tracked player announcements and recaps", "worlds": "Worlds posts", "both": "Tracked player and Worlds posts"}
WORLDS_PERMISSIONS = ("view_channel", "send_messages", "embed_links", "add_reactions", "read_message_history")
WORLDS_PERMISSION_ERROR = "I need View Channel, Send Messages, Embed Links, Add Reactions, and Read Message History there."


def worlds_permissions_available(channel, member):
    """Check the permissions required by numbered Worlds schedules."""
    permissions = channel.permissions_for(member)
    return all(getattr(permissions, name, False) for name in WORLDS_PERMISSIONS)


def _preserve_worlds_route(guild_id):
    """Keep an inherited Worlds destination stable while only player routing changes."""
    guild_id = str(guild_id)
    general = load_guild_channels()
    if guild_id in general and guild_id not in load_worlds_channels() and guild_id not in disabled_guilds("worlds"):
        set_worlds_channel(guild_id, general[guild_id])


def configure_channel(guild_id, channel_id, kind):
    """Set/re-enable only the requested announcement families."""
    if kind not in TYPE_LABELS:
        raise ValueError("Unknown announcement type")
    if kind == "players":
        _preserve_worlds_route(guild_id)
    if kind in ("players", "both"):
        set_guild_channel(guild_id, channel_id)
        set_route_enabled(guild_id, "matches", True)
    if kind in ("worlds", "both"):
        set_worlds_channel(guild_id, channel_id)
        set_route_enabled(guild_id, "worlds", True)


def disable_channels(guild_id, kind):
    """Disable selected families, including their fallback routes, and remove their settings."""
    if kind not in TYPE_LABELS:
        raise ValueError("Unknown announcement type")
    if kind == "both":
        disable_server(guild_id)
    elif kind == "players":
        _preserve_worlds_route(guild_id)
        set_route_enabled(guild_id, "matches", False)
    else:
        set_route_enabled(guild_id, "worlds", False)
    if kind in ("players", "both"):
        unset_guild_channel(guild_id)
    if kind in ("worlds", "both"):
        remove_worlds_channel(guild_id)

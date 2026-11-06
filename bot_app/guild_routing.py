"""Shared atomic updates for per-server player announcement routes."""

from .store import update_guild_channels


def set_guild_channel(guild_id, channel_id):
    """Set the server's player announcement route without affecting Worlds."""
    update_guild_channels(lambda channels: channels.__setitem__(str(guild_id), channel_id))


def unset_guild_channel(guild_id):
    """Remove a player route and report whether one was configured."""
    removed = False

    def mutate(channels):
        nonlocal removed
        removed = channels.pop(str(guild_id), None) is not None

    update_guild_channels(mutate)
    return removed

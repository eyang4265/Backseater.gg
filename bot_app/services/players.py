"""Player lookup service boundary for command handlers."""

from __future__ import annotations

from typing import Any

from ..commands.shared import Target, resolve_target

__all__ = ["Target", "resolve_target"]


def target_from_options(
    ctx: Any,
    server: str | None,
    summoner: str | None,
    user: str | None = None,
) -> Target | None:
    """Resolve a command's player options through the shared lookup path."""
    return resolve_target(ctx, server, summoner, user)

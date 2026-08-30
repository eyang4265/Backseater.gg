"""Player lookup service boundary for command handlers."""

from __future__ import annotations

import logging
from typing import Any

from ..commands.shared import Target, resolve_username

LOGGER = logging.getLogger(__name__)

__all__ = ["Target", "resolve_username"]


def target_from_options(
    ctx: Any,
    server: str | None,
    username: str | None,
) -> Target | None:
    """Resolve a command's player options through the shared lookup path."""
    LOGGER.debug("Resolving target: server=%s username=%s", server, username)
    return resolve_username(ctx, server, username)

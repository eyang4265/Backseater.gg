"""Pure player target value object shared by commands and services."""

from __future__ import annotations

from dataclasses import dataclass

from ..routing import opgg_url


@dataclass(frozen=True)
class Target:
    """The player a command should act on."""

    puuid: str
    server: str
    riot_id: str = "Unknown player"
    icon_url: str | None = None

    @property
    def opgg_url(self) -> str | None:
        """Return this player's region-correct OP.GG profile URL."""
        return opgg_url(self.server, self.riot_id)

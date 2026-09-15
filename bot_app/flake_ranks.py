"""Shared rendering and ordering for server flake tier lists."""

from __future__ import annotations

import discord

from .render import make_embed

FLAKE_TIERS = ("S", "A", "B", "C", "D", "F", "Unknown")


def ordered_flake_ranks(
    rankings: dict[str, dict[str, str]],
) -> list[tuple[str, dict[str, str]]]:
    """Order rankings from most flaky tier, then by saved display name."""
    return sorted(
        rankings.items(),
        key=lambda item: (
            FLAKE_TIERS.index(item[1]["tier"]),
            item[1].get("display_name", "").casefold(),
        ),
    )


def build_flake_rank_embed(
    rankings: dict[str, dict[str, str]],
    *,
    guild_name: str,
    page: int = 0,
    pages: int = 1,
) -> discord.Embed:
    """Render assigned users from most flaky (S) through unknown."""
    embed = make_embed(
        "S is the most flaky; F is the least flaky. Unknown is unranked.",
        title=f"Flake Tier List — {guild_name}",
    )
    for tier in FLAKE_TIERS:
        members = sorted(
            (
                (entry.get("display_name") or f"User {user_id}", user_id)
                for user_id, entry in rankings.items()
                if entry.get("tier") == tier
            ),
            key=lambda item: item[0].casefold(),
        )
        lines = [f"<@{user_id}>" for _name, user_id in members] or ["—"]
        chunks: list[str] = []
        current: list[str] = []
        for line in lines:
            candidate = "\n".join((*current, line))
            if current and len(candidate) > 1024:
                chunks.append("\n".join(current))
                current = []
            current.append(line)
        chunks.append("\n".join(current))
        for index, chunk in enumerate(chunks):
            name = f"{tier} Tier" if index == 0 else f"{tier} Tier (continued)"
            embed.add_field(name=name, value=chunk, inline=False)
    if pages > 1:
        embed.set_footer(text=f"Page {page + 1}/{pages}")
    return embed

"""The public ``/champ`` command and its OP.GG champion presentation."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict

import discord
from discord.ext import commands

from .. import ddragon
from ..emoji import item_emoji, rune_emoji
from ..opgg import ChampionStats, fetch_champion_stats
from ..render import make_embed
from ..routing import opgg_champion_url
from .shared import GUILD_IDS, SERVERS, log_command, remember_view_state

_CHAMPION_POSITIONS = ("top", "jungle", "mid", "adc", "support")
_CHAMPION_SERVERS = ("GLOBAL", *SERVERS)
_SITUATIONAL_EXCLUDED_ITEM_IDS = {"3041"}  # Mejai's Soulstealer
_SITUATIONAL_EXCLUDED_ITEM_NAMES = {"mejai's soulstealer"}
LOGGER = logging.getLogger(__name__)


def _items_with_ids(items, item_ids, item_counts=()):
    """Pair expanded item quantities with IDs while tolerating old payloads."""
    pairs = []
    for index, item in enumerate(items):
        item_id = item_ids[index] if index < len(item_ids) else None
        count = item_counts[index] if index < len(item_counts) else 1
        pairs.extend((item, item_id) for _ in range(max(count, 1)))
    return pairs


def _item_icons(items, item_ids, item_counts=()) -> str:
    """Render matched item icons, preserving repeated items."""
    return " ".join(
        str(emoji)
        for item, item_id in _items_with_ids(items, item_ids, item_counts)
        if (emoji := item_emoji(item, item_id=item_id))
    )


def _situational_icons(stats: ChampionStats) -> str:
    """Render a few fourth-item options that are absent from the core build."""
    core_ids = {item_id for item_id in stats.core_item_ids if item_id}
    core_names = {item.casefold() for item in stats.core_items}
    options = [
        (item, item_id)
        for item, item_id in zip(stats.situational_items, stats.situational_item_ids)
        if (
            item_id not in core_ids
            and item.casefold() not in core_names
            and item_id not in _SITUATIONAL_EXCLUDED_ITEM_IDS
            and item.casefold() not in _SITUATIONAL_EXCLUDED_ITEM_NAMES
        )
    ][:5]
    return _item_icons(
        tuple(item for item, _ in options),
        tuple(item_id for _, item_id in options),
    )


def _rune_row(runes, *, selected: bool = False) -> str:
    """Render one compact icon-only rune row with selected runes marked."""
    labels = []
    for rune in runes:
        emoji = rune_emoji(rune)
        label = str(emoji) if emoji else "▫️"
        labels.append(f"**{label}**" if selected else label)
    return " ".join(labels)


def _champion_stats_embed(
    champion_name: str, stats: ChampionStats, url: str, server: str
) -> discord.Embed:
    """Render a live OP.GG champion panel."""
    role_label = stats.position.title() if stats.position != "all" else "All roles"
    embed = make_embed(
        f"[View source on OP.GG]({url})",
        title=f"{champion_name} — OP.GG ({role_label}) · {server}",
    )
    embed.add_field(name="Tier", value=stats.tier or "Unavailable", inline=True)
    embed.add_field(name="Win rate", value=f"{stats.win_rate:.2f}%", inline=True)
    embed.add_field(name="Pick rate", value=f"{stats.pick_rate:.2f}%", inline=True)
    embed.add_field(name="Ban rate", value=f"{stats.ban_rate:.2f}%", inline=True)
    if stats.patch:
        embed.add_field(name="Patch", value=stats.patch, inline=True)

    if stats.starter_items or stats.core_items or stats.boots:
        build_lines = []
        if stats.starter_items:
            build_lines.append(
                f"**Start:** {_item_icons(stats.starter_items, stats.starter_item_ids, stats.starter_item_counts)}"
            )
        if stats.core_items:
            build_lines.append(
                f"**Core:** {_item_icons(stats.core_items, stats.core_item_ids, stats.core_item_counts)}"
            )
        if stats.boots:
            build_lines.append(
                f"**Boots:** {_item_icons(stats.boots, stats.boot_ids, stats.boot_counts)}"
            )
        if situational := _situational_icons(stats):
            build_lines.append(f"**Situational:** {situational}")
        embed.add_field(name="Build", value="\n".join(build_lines), inline=False)

    if stats.skill_order:
        embed.add_field(
            name="Skill order", value=" › ".join(stats.skill_order), inline=False
        )
    if stats.runes:
        if stats.primary_runes:
            embed.add_field(
                name=stats.primary_style or "Primary",
                value=_rune_row(stats.primary_runes, selected=True),
                inline=True,
            )
        if stats.secondary_runes:
            embed.add_field(
                name=stats.secondary_style or "Secondary",
                value=_rune_row(stats.secondary_runes, selected=True),
                inline=True,
            )
        if stats.shard_runes:
            embed.add_field(
                name="Shards",
                value=_rune_row(stats.shard_runes, selected=True),
                inline=True,
            )
        if not stats.primary_runes and not stats.secondary_runes and not stats.shard_runes:
            embed.add_field(
                name="Runes", value=_rune_row(stats.runes, selected=True), inline=False
            )
    embed.url = url
    return embed


class ChampionPositionView(discord.ui.View):
    """Buttons for switching between OP.GG positions."""

    def __init__(
        self,
        champion_name: str,
        server: str,
        internal_id: str,
        stats_by_position: dict[str, ChampionStats],
        selected_position: str | None = None,
    ) -> None:
        super().__init__(timeout=None)
        self.champion_name = champion_name
        self.server = server
        self.internal_id = internal_id
        self.stats_by_position = stats_by_position
        self.selected_position = selected_position
        for position in _CHAMPION_POSITIONS:
            if position not in stats_by_position:
                continue
            button = discord.ui.Button(
                label=position.title(),
                style=(
                    discord.ButtonStyle.primary
                    if position == self.selected_position
                    else discord.ButtonStyle.secondary
                ),
                custom_id=f"embed:champ:{position}",
            )

            async def switch(interaction: discord.Interaction, chosen=position) -> None:
                """Switch the displayed position."""
                stats = self.stats_by_position[chosen]
                url = opgg_champion_url(self.server, self.internal_id, chosen)
                await interaction.response.edit_message(
                    embed=_champion_stats_embed(self.champion_name, stats, url, self.server),
                    view=ChampionPositionView(
                        self.champion_name,
                        self.server,
                        self.internal_id,
                        self.stats_by_position,
                        chosen,
                    ),
                )
                await remember_view_state(
                    interaction.message,
                    "champ",
                    {
                        "champion_name": self.champion_name,
                        "server": self.server,
                        "internal_id": self.internal_id,
                        "stats_by_position": {
                            key: asdict(value) for key, value in self.stats_by_position.items()
                        },
                        "selected_position": chosen,
                    },
                )

            button.callback = switch
            self.add_item(button)


class ChampionCommands(commands.Cog):
    """Provide the public champion statistics command."""

    def __init__(self, bot: discord.Bot) -> None:
        """Initialize the command cog."""
        self.bot = bot

    @discord.slash_command(
        guild_ids=GUILD_IDS, description="Open OP.GG stats for a champion"
    )
    @discord.option("champion", description="Champion name or alias", required=True)
    @discord.option(
        "server",
        description="Server (defaults to Global)",
        choices=_CHAMPION_SERVERS,
        required=False,
    )
    @discord.option(
        "position",
        description="Role to view",
        choices=("all", "top", "jungle", "mid", "adc", "support"),
        required=False,
    )
    async def champ(self, ctx, champion, server=None, position=None):
        """Open live OP.GG champion statistics for a champion and role."""
        try:
            await ctx.defer()
        except discord.NotFound:
            LOGGER.warning("/champ interaction expired before acknowledgement")
            return
        log_command(ctx, champion=champion, server=server, position=position)

        catalog = await asyncio.to_thread(ddragon.catalog)
        found = catalog.by_query(champion) if catalog else None
        if found is None:
            LOGGER.info("/champ could not resolve champion query %r", champion)
            await ctx.respond(embed=make_embed(f"Unknown champion: **{champion}**."))
            return
        LOGGER.debug("/champ resolved %r to %s (internal_id=%s)", champion, found.name, found.internal_id)

        server = server or "GLOBAL"
        url = opgg_champion_url(server, found.internal_id, position)
        if url is None:
            await ctx.respond(
                embed=make_embed(f"OP.GG stats are unavailable for server `{server}`.")
            )
            return

        positions = (
            (position,) if position and position != "all" else _CHAMPION_POSITIONS
        )
        LOGGER.debug("/champ fetching OP.GG stats for %s positions %s on %s", found.name, positions, server)
        fetched = await asyncio.gather(
            *(
                asyncio.to_thread(fetch_champion_stats, server, found.internal_id, item)
                for item in positions
            ),
            return_exceptions=True,
        )
        stats_by_position = {
            result.position: result
            for result in fetched
            if isinstance(result, ChampionStats)
        }
        if not stats_by_position:
            message = next(
                (str(result) for result in fetched if isinstance(result, Exception)),
                "OP.GG returned no usable position data.",
            )
            LOGGER.warning("/champ found no usable OP.GG data for %s: %s", found.name, message)
            await ctx.respond(embed=make_embed(f"Could not fetch live OP.GG stats: {message}"))
            return

        selected = max(stats_by_position.values(), key=lambda item: item.pick_rate)
        LOGGER.info(
            "/champ showing %s (%s, %s) requested by %s",
            found.name, selected.position, server, ctx.author,
        )
        selected_url = opgg_champion_url(server, found.internal_id, selected.position)
        message = await ctx.respond(
            embed=_champion_stats_embed(found.name, selected, selected_url, server),
            view=ChampionPositionView(
                found.name,
                server,
                found.internal_id,
                stats_by_position,
                selected.position,
            ),
        )
        await remember_view_state(
            message,
            "champ",
            {
                "champion_name": found.name,
                "server": server,
                "internal_id": found.internal_id,
                "stats_by_position": {
                    key: asdict(value) for key, value in stats_by_position.items()
                },
                "selected_position": selected.position,
            },
        )


def setup(bot: discord.Bot) -> None:
    """Register the champion command cog."""
    bot.add_cog(ChampionCommands(bot))

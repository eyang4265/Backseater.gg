"""The public ``/coachless`` command and its Coachless.gg build view."""

from __future__ import annotations

import asyncio
import logging

import discord
from discord.ext import commands

from .. import ddragon
from ..coachless import CoachlessEntry, CoachlessError, fetch_build_stage
from ..emoji import item_emoji, rune_emoji, summoner_spell_emoji
from ..render import make_embed
from .shared import GUILD_IDS, log_command, remember_view_state

_ROLES = ("top", "jungle", "mid", "adc", "support")
_STAGES = ("runes", "summs", "starter", "1st", "2nd", "3rd", "4th+", "boots")
_STAGE_LABELS = {
    "runes": "Runes", "summs": "Summs", "starter": "Starter", "1st": "1st Item", "2nd": "2nd Item",
    "3rd": "3rd Item", "4th+": "4th+ Item", "boots": "Boots", "bravery": "Bravery",
}
_BUTTONS = _STAGES + ("bravery",)
_BRAVERY_LABELS = {
    "runes": "Rune", "summs": "Summ", "starter": "Start", "1st": "1st",
    "2nd": "2nd", "3rd": "3rd", "4th+": "4+", "boots": "Boots",
}
_BRAVERY_COUNTS = {"4th+": 3}  # "4th+" covers three purchase slots (4, 5, 6).
LOGGER = logging.getLogger(__name__)


def _number(value: int) -> str:
    """Format Coachless purchase totals compactly for an embed table."""
    return f"{value / 1000:.1f}K".replace(".0K", "K") if value >= 1000 else f"{value:,}"


_COLUMN_NOUNS = {"runes": ("Rune", "Picks"), "summs": ("Spell", "Picks")}


def _emoji_for(stage: str, entry: CoachlessEntry):
    """Handle emoji for."""
    if stage == "runes":
        # Riot's rune and item ids overlap (e.g. 8010 is both Conqueror and
        # Bloodletter's Curse), so a name match must win over an id match.
        return rune_emoji(entry.name) or rune_emoji(entry.identifier)
    if stage == "summs":
        return summoner_spell_emoji(entry.identifier) or summoner_spell_emoji(entry.name)
    return item_emoji(entry.name, item_id=entry.identifier)


def _pick_share(entry: CoachlessEntry, entries: tuple[CoachlessEntry, ...]) -> float:
    """This entry's share of the category's total picks."""
    total = sum(other.buys for other in entries) or 1
    return entry.buys / total


def _brave_picks(entries: tuple[CoachlessEntry, ...], count: int, *, exclude: frozenset[str] = frozenset()) -> tuple[CoachlessEntry, ...]:
    """The `count` highest-WPA options that aren't just the single most-picked, obvious choice."""
    candidates = tuple(entry for entry in entries if entry.identifier not in exclude)
    if not candidates:
        return ()
    most_picked = max(candidates, key=lambda entry: entry.buys)
    pool = [entry for entry in candidates if entry is not most_picked] or list(candidates)
    return tuple(sorted(pool, key=lambda entry: entry.wpa, reverse=True)[:count])


def _brave_pick(entries: tuple[CoachlessEntry, ...], *, exclude: frozenset[str] = frozenset()) -> CoachlessEntry | None:
    """The single highest-WPA option that isn't just the single most-picked, obvious choice."""
    picks = _brave_picks(entries, 1, exclude=exclude)
    return picks[0] if picks else None


def _stage_embed(champion_name: str, champion_slug: str, role: str, stage: str, entries: tuple[CoachlessEntry, ...]) -> discord.Embed:
    """Render one Coachless stage as three aligned columns: name, WPA, and picks/buys."""
    label = _STAGE_LABELS[stage]
    rows = entries[:10]
    if not rows:
        embed = make_embed("Coachless has no results for this champion and role.", title=f"{champion_name} — Coachless ({role.title()})")
        embed.set_author(name=f"{label} · Coachless.gg")
        embed.url = f"https://coachless.gg/builds/{champion_slug.lower()}?role={role}"
        return embed
    name_noun, count_noun = _COLUMN_NOUNS.get(stage, ("Item", "Buys"))
    # Coachless's own site hides picks below ~1% of a category's total sample;
    # replicate that cutoff to bold only the options it would actually display.
    def bold(text: str, entry: CoachlessEntry) -> str:
        return f"**{text}**" if _pick_share(entry, entries) >= 0.01 else text

    embed = make_embed("", title=f"{champion_name} — Coachless ({role.title()})")
    embed.set_author(name=f"{label} · Coachless.gg")
    embed.url = f"https://coachless.gg/builds/{champion_slug.lower()}?role={role}"
    embed.add_field(
        name=name_noun,
        value="\n".join(f"{_emoji_for(stage, entry) or '▫️'} {bold(entry.name, entry)}" for entry in rows),
        inline=True,
    )
    embed.add_field(name="WPA", value="\n".join(bold(f"{entry.wpa:+.2f}", entry) for entry in rows), inline=True)
    embed.add_field(name=count_noun, value="\n".join(bold(_number(entry.buys), entry) for entry in rows), inline=True)
    return embed


def _bravery_embed(champion_name: str, champion_slug: str, role: str, picks: dict[str, tuple[CoachlessEntry, ...]]) -> discord.Embed:
    """Render high-WPA options per category as three aligned columns: category, WPA, and picks."""
    rows: list[tuple[str, CoachlessEntry | None]] = []
    for stage in _STAGES:
        entries = picks.get(stage) or ()
        if entries:
            rows.extend((stage, entry) for entry in entries)
        else:
            rows.append((stage, None))

    embed = make_embed("", title=f"{champion_name} — Bravery Build ({role.title()})")
    embed.set_author(name="High-WPA, low-pick-rate picks · Coachless.gg")
    embed.url = f"https://coachless.gg/builds/{champion_slug.lower()}?role={role}"
    embed.add_field(
        name="Category",
        value="\n".join(
            f"{_BRAVERY_LABELS[stage]}: {_emoji_for(stage, entry) or '▫️'} {entry.name}" if entry else f"{_BRAVERY_LABELS[stage]}: —"
            for stage, entry in rows
        ),
        inline=True,
    )
    embed.add_field(name="WPA", value="\n".join(f"{entry.wpa:+.2f}" if entry else "—" for _, entry in rows), inline=True)
    embed.add_field(name="Picks", value="\n".join(_number(entry.buys) if entry else "—" for _, entry in rows), inline=True)
    return embed


class CoachlessView(discord.ui.View):
    """Switch among the requested Coachless overview stages."""

    def __init__(self, champion_name: str, champion_id: int, champion_slug: str, role: str, selected: str = "runes") -> None:
        super().__init__(timeout=None)
        self.champion_name, self.champion_id = champion_name, champion_id
        self.champion_slug, self.role, self.selected = champion_slug, role, selected
        for stage in _BUTTONS:
            button = discord.ui.Button(
                label=_STAGE_LABELS[stage],
                style=discord.ButtonStyle.primary if stage == selected else discord.ButtonStyle.secondary,
                custom_id=f"embed:coachless:{stage}",
            )

            async def switch(interaction: discord.Interaction, chosen: str = stage) -> None:
                """Load and display the chosen Coachless build stage."""
                await interaction.response.defer()
                LOGGER.debug("/coachless view switching to stage %s for %s", chosen, self.champion_name)
                try:
                    if chosen == "bravery":
                        picks: dict[str, tuple[CoachlessEntry, ...]] = {}
                        used_item_ids: set[str] = set()
                        for other_stage in _STAGES:
                            stage_entries = await asyncio.to_thread(
                                fetch_build_stage,
                                self.champion_id,
                                self.role,
                                other_stage,
                                champion_slug=self.champion_slug,
                            )
                            # Item stages (starter/boots/1st-4th+) share Riot's item
                            # id space, so the same item can't be picked twice.
                            is_item_stage = other_stage not in ("runes", "summs")
                            chosen_entries = _brave_picks(
                                stage_entries,
                                _BRAVERY_COUNTS.get(other_stage, 1),
                                exclude=frozenset(used_item_ids) if is_item_stage else frozenset(),
                            )
                            picks[other_stage] = chosen_entries
                            if is_item_stage:
                                used_item_ids.update(entry.identifier for entry in chosen_entries)
                        embed = _bravery_embed(self.champion_name, self.champion_slug, self.role, picks)
                    else:
                        entries = await asyncio.to_thread(
                            fetch_build_stage,
                            self.champion_id,
                            self.role,
                            chosen,
                            champion_slug=self.champion_slug,
                        )
                        embed = _stage_embed(self.champion_name, self.champion_slug, self.role, chosen, entries)
                except CoachlessError as error:
                    await interaction.edit_original_response(embed=make_embed(f"Could not fetch Coachless data: {error}"), view=self)
                    return
                await interaction.edit_original_response(
                    embed=embed,
                    view=CoachlessView(self.champion_name, self.champion_id, self.champion_slug, self.role, chosen),
                )
                await remember_view_state(
                    interaction.message,
                    "coachless",
                    {
                        "champion_name": self.champion_name,
                        "champion_id": self.champion_id,
                        "champion_slug": self.champion_slug,
                        "role": self.role,
                        "selected": chosen,
                    },
                )

            button.callback = switch
            self.add_item(button)


class CoachlessCommands(commands.Cog):
    """Provide Coachless.gg build statistics to Discord."""

    def __init__(self, bot: discord.Bot) -> None:
        """Initialize the Coachless command cog."""
        self.bot = bot

    @discord.slash_command(guild_ids=GUILD_IDS, description="Show Coachless.gg build statistics")
    @discord.option("champion", description="Champion name or alias", required=True)
    @discord.option("role", description="Role to view", choices=_ROLES, required=True)
    async def coachless(self, ctx, champion, role):
        """Show Coachless runes by default and build-stage buttons for a champion."""
        try:
            await ctx.defer()
        except discord.NotFound:
            LOGGER.warning("/coachless interaction expired before acknowledgement")
            return
        log_command(ctx, champion=champion, role=role)
        catalog = await asyncio.to_thread(ddragon.catalog)
        found = catalog.by_query(champion) if catalog else None
        if found is None:
            LOGGER.info("/coachless could not resolve champion query %r", champion)
            await ctx.respond(embed=make_embed(f"Unknown champion: **{champion}**."))
            return
        LOGGER.debug("/coachless resolved %r to %s (key=%s)", champion, found.name, found.key)
        try:
            entries = await asyncio.to_thread(
                fetch_build_stage,
                found.key,
                role,
                "runes",
                champion_slug=found.internal_id,
            )
        except CoachlessError as error:
            LOGGER.warning("/coachless failed: champion=%s role=%s error=%s", found.name, role, error)
            await ctx.respond(embed=make_embed(f"Could not fetch Coachless data: {error}"))
            return
        LOGGER.info("/coachless showing %s (%s) requested by %s", found.name, role, ctx.author)
        message = await ctx.respond(
            embed=_stage_embed(found.name, found.internal_id, role, "runes", entries),
            view=CoachlessView(found.name, found.key, found.internal_id, role),
        )
        await remember_view_state(
            message,
            "coachless",
            {
                "champion_name": found.name,
                "champion_id": found.key,
                "champion_slug": found.internal_id,
                "role": role,
                "selected": "runes",
            },
        )


def setup(bot: discord.Bot) -> None:
    """Register the Coachless command cog."""
    bot.add_cog(CoachlessCommands(bot))

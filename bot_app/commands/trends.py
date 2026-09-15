"""The public ``/trends`` OP.GG role leaderboard."""

from __future__ import annotations

import asyncio
import logging

import discord
from discord.ext import commands

from ..opgg import ChampionTrend, OPGGError, fetch_champion_trends, opgg_trends_url
from ..render import make_embed
from .shared import GUILD_IDS, log_command, remember_view_state

LOGGER = logging.getLogger(__name__)
ROLES = ("top", "jungle", "mid", "adc", "support")
def game_length_label(game_length: int, game_lengths: tuple[int, ...]) -> str:
    """Format OP.GG's start timestamps as the game-length ranges they represent."""
    index = game_lengths.index(game_length)
    if index == len(game_lengths) - 1:
        return f"{game_length}+ min"
    upper = game_lengths[index + 1]
    return f"Under {upper} min" if game_length == 0 else f"{game_length}–{upper} min"


def trends_embed(
    role: str,
    game_length: int,
    game_lengths: tuple[int, ...],
    trends: tuple[ChampionTrend, ...],
) -> discord.Embed:
    """Render ten game-length win-rate leaders as aligned Discord columns."""
    url = opgg_trends_url(role)
    label = game_length_label(game_length, game_lengths)
    embed = make_embed(
        f"Scanned every champion in OP.GG's {role.title()} table. "
        f"[View role list on OP.GG]({url})",
        title=f"OP.GG Game-Time Trends — {role.title()} · {label}",
    )
    embed.url = url
    embed.add_field(
        name="Champion",
        value="\n".join(
            f"**{index}.** [{row.name}](https://op.gg/lol/champions/"
            f"{row.internal_id}/trends/{role}?region=global)"
            for index, row in enumerate(trends, 1)
        ),
        inline=True,
    )
    embed.add_field(
        name="Win rate",
        value="\n".join(f"**{row.win_rate:.2f}%**" for row in trends),
        inline=True,
    )
    return embed


class TrendsGameLengthView(discord.ui.View):
    """Persistent buttons for every OP.GG game-length timestamp."""

    def __init__(
        self,
        role: str,
        game_lengths: tuple[int, ...] = (0, 25, 30, 35, 40),
        selected: int = 0,
        trends_by_time: dict[int, tuple[ChampionTrend, ...]] | None = None,
    ) -> None:
        super().__init__(timeout=None)
        self.role = role
        self.selected = selected
        self.game_lengths = tuple(sorted(game_lengths))
        self.trends_by_time = trends_by_time
        for game_length in self.game_lengths:
            button = discord.ui.Button(
                label=game_length_label(game_length, self.game_lengths),
                style=(
                    discord.ButtonStyle.primary
                    if game_length == selected
                    else discord.ButtonStyle.secondary
                ),
                custom_id=f"embed:trends:{game_length}",
            )

            async def switch(
                interaction: discord.Interaction, chosen: int = game_length
            ) -> None:
                """Acknowledge immediately, then display the chosen game length."""
                await interaction.response.defer()
                try:
                    trends_by_time = self.trends_by_time or await asyncio.to_thread(
                        fetch_champion_trends, self.role
                    )
                except OPGGError as error:
                    await interaction.edit_original_response(
                        embed=make_embed(f"Could not fetch OP.GG trends: {error}"),
                        view=self,
                    )
                    return
                available = tuple(sorted(trends_by_time))
                trends = trends_by_time.get(chosen)
                if not trends:
                    await interaction.edit_original_response(
                        embed=make_embed("OP.GG has no results for that game length."),
                        view=self,
                    )
                    return
                view = TrendsGameLengthView(
                    self.role, available, chosen, trends_by_time
                )
                await interaction.edit_original_response(
                    embed=trends_embed(self.role, chosen, available, trends), view=view
                )
                await remember_view_state(
                    interaction.message,
                    "trends",
                    {
                        "role": self.role,
                        "game_lengths": available,
                        "selected": chosen,
                    },
                )

            button.callback = switch
            self.add_item(button)


class TrendsCommands(commands.Cog):
    """Provide OP.GG's highest-win-rate champions by role and time period."""

    def __init__(self, bot: discord.Bot) -> None:
        """Initialize the command cog."""
        self.bot = bot

    @discord.slash_command(
        guild_ids=GUILD_IDS, description="Show OP.GG's highest win rates by role"
    )
    @discord.option("role", description="Role to rank", choices=ROLES, required=True)
    async def trends(self, ctx, role):
        """Scan a role and show top-ten buttons for every game-length timestamp."""
        try:
            await ctx.defer()
        except discord.NotFound:
            LOGGER.warning("/trends interaction expired before acknowledgement")
            return
        log_command(ctx, role=role)
        try:
            trends_by_time = await asyncio.to_thread(fetch_champion_trends, role)
        except OPGGError as error:
            await ctx.respond(embed=make_embed(f"Could not fetch OP.GG trends: {error}"))
            return
        game_lengths = tuple(sorted(trends_by_time))
        selected = game_lengths[0]
        view = TrendsGameLengthView(role, game_lengths, selected, trends_by_time)
        message = await ctx.respond(
            embed=trends_embed(
                role, selected, game_lengths, trends_by_time[selected]
            ),
            view=view,
        )
        await remember_view_state(
            message,
            "trends",
            {"role": role, "game_lengths": game_lengths, "selected": selected},
        )


def setup(bot: discord.Bot) -> None:
    """Register the OP.GG trends command cog."""
    bot.add_cog(TrendsCommands(bot))

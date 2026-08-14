"""Match charts rendered as image attachments.

matplotlib is optional: if it isn't installed the announcement still goes out,
just without the graph.
"""

from __future__ import annotations

import io
import logging
from typing import Any, Sequence

import discord

from . import ddragon
from .timeline import KillEvent, MatchTimeline

LOGGER = logging.getLogger(__name__)

try:  # pragma: no cover - depends on the deployment environment
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.patheffects as path_effects
    import matplotlib.pyplot as plt

    MATPLOTLIB_AVAILABLE = True
except ImportError:  # pragma: no cover
    MATPLOTLIB_AVAILABLE = False

# Discord's dark theme, so the chart reads as part of the embed card rather
# than a bare matplotlib plot dropped into it.
_BACKGROUND = "#2b2d31"
_GRID = "#40444b"
_TEXT = "#dcddde"
_MUTED = "#949ba4"
_TEAM_BLUE = "#5383E8"
_TEAM_RED = "#E84057"
_HIGHLIGHT = "#F5C542"

# Discord scales any attached image to a fixed display width regardless of its
# pixel size, so this is sized for a crisp render at that width and no larger.
_FIGURE_SIZE = (9, 4.8)
_FIGURE_DPI = 150

DAMAGE_CHART_FILENAME = "damage.png"
KILL_MAP_FILENAME = "killmap.png"

_KILL_MAP_FIGURE_SIZE = (6.4, 6.4)
_KILL_COLOR = "#3BA55D"
_DEATH_COLOR = _TEAM_RED

#: Riot's static world bounds per map, as ``(min_x, min_y, max_x, max_y)``.
#: Timeline positions are game-world units, so plotting them over the minimap
#: art means mapping these bounds onto the image.
_MAP_BOUNDS: dict[int, tuple[int, int, int, int]] = {
    11: (-120, -120, 14870, 14980),  # Summoner's Rift
    12: (-28, -19, 12849, 12858),  # Howling Abyss
}
_DEFAULT_MAP_ID = 11


def build_damage_chart(
    match: dict[str, Any], highlight_puuids: Sequence[str] | set[str] = ()
) -> discord.File | None:
    """Horizontal bar chart of damage to champions, one bar per participant.

    Bars are coloured by team; highlighted players get a gold outline, a ★ in
    the label, and a gold axis label. Returns None when matplotlib is missing
    or the match carries no participants.
    """
    if not MATPLOTLIB_AVAILABLE:
        LOGGER.warning("matplotlib is not installed; skipping the damage chart")
        return None

    participants = match.get("info", {}).get("participants", []) or []
    if not participants:
        return None

    catalog = ddragon.catalog()
    highlighted = set(highlight_puuids)

    rows = sorted(participants, key=lambda p: p.get("totalDamageDealtToChampions", 0))
    labels: list[str] = []
    values: list[int] = []
    colors: list[str] = []
    edge_colors: list[str] = []
    edge_widths: list[float] = []
    is_highlighted: list[bool] = []

    for participant in rows:
        champion = catalog.by_key(participant.get("championId")) if catalog else None
        name = champion.name if champion else participant.get("championName", "Unknown")
        highlight = participant.get("puuid") in highlighted
        team_color = _TEAM_BLUE if participant.get("teamId") == 100 else _TEAM_RED

        labels.append(f"★  {name}" if highlight else name)
        values.append(participant.get("totalDamageDealtToChampions", 0))
        colors.append(team_color)
        edge_colors.append(_HIGHLIGHT if highlight else team_color)
        edge_widths.append(1.8 if highlight else 0.0)
        is_highlighted.append(highlight)

    figure, axes = plt.subplots(figsize=_FIGURE_SIZE, dpi=_FIGURE_DPI)
    try:
        figure.patch.set_facecolor(_BACKGROUND)
        axes.set_facecolor(_BACKGROUND)

        bars = axes.barh(
            labels,
            values,
            color=colors,
            edgecolor=edge_colors,
            linewidth=edge_widths,
            height=0.68,
            zorder=3,
        )

        axes.set_title(
            "Damage Dealt to Champions", color=_TEXT, fontsize=15, fontweight="bold", pad=14
        )
        axes.set_xlabel("")
        axes.bar_label(
            bars,
            labels=[f"{value:,.0f}" for value in values],
            padding=6,
            color=_TEXT,
            fontsize=10,
            fontweight="medium",
        )
        # Bar labels sit past the end of each bar; without headroom the longest
        # one gets clipped by the figure edge.
        axes.set_xlim(right=max(values, default=0) * 1.16 or 1)

        axes.tick_params(axis="y", colors=_TEXT, labelsize=11, length=0)
        axes.tick_params(axis="x", colors=_MUTED, labelsize=9, length=0)
        for tick_label, highlight in zip(axes.get_yticklabels(), is_highlighted):
            if highlight:
                tick_label.set_color(_HIGHLIGHT)
                tick_label.set_fontweight("bold")

        axes.grid(axis="x", color=_GRID, linewidth=0.8, alpha=0.6, zorder=0)
        axes.set_axisbelow(True)
        for side in ("top", "right", "left"):
            axes.spines[side].set_visible(False)
        axes.spines["bottom"].set_color(_GRID)

        axes.legend(
            [
                plt.Rectangle((0, 0), 1, 1, facecolor=_TEAM_BLUE, edgecolor="none"),
                plt.Rectangle((0, 0), 1, 1, facecolor=_TEAM_RED, edgecolor="none"),
            ],
            ["Blue Team", "Red Team"],
            loc="lower right",
            frameon=False,
            fontsize=9,
            labelcolor=_TEXT,
            handlelength=1.2,
            handleheight=1.2,
        )

        figure.tight_layout(pad=1.4)

        buffer = io.BytesIO()
        figure.savefig(buffer, format="png", facecolor=figure.get_facecolor())
    finally:
        plt.close(figure)

    buffer.seek(0)
    return discord.File(buffer, filename=DAMAGE_CHART_FILENAME)


def _draw_map_background(axes, map_id: int, bounds: tuple[int, int, int, int]) -> None:
    """Lay the minimap art under the plot, or leave a flat panel if it's missing."""
    art = ddragon.map_image(map_id)
    if art is None:
        return
    left, bottom, right, top = bounds
    try:
        image = plt.imread(io.BytesIO(art), format="png")
    except (ValueError, OSError) as error:  # pragma: no cover - corrupt CDN payload
        LOGGER.warning("Could not decode map %s art: %s", map_id, error)
        return
    # origin="upper" puts the art's first row at the top of the extent, which
    # is where the high-y end of the game world lives.
    axes.imshow(image, extent=(left, right, bottom, top), origin="upper", zorder=1)


def _plot_event(axes, event: KillEvent, order: int, is_kill: bool) -> None:
    """One numbered marker. Kills are green circles, deaths red diamonds.

    Kills cluster hard around objectives, so the number sits inside the marker
    rather than beside it: two adjacent labels would otherwise run together
    and read as a single number.
    """
    axes.scatter(
        event.x,
        event.y,
        marker="o" if is_kill else "D",
        s=260 if is_kill else 250,
        color=_KILL_COLOR if is_kill else _DEATH_COLOR,
        edgecolors="#F2F3F5",
        linewidths=1.2,
        alpha=0.94,
        zorder=3,
    )
    label = axes.annotate(
        str(order),
        (event.x, event.y),
        ha="center",
        va="center",
        color="#FFFFFF",
        fontsize=8,
        fontweight="bold",
        zorder=4,
    )
    label.set_path_effects([path_effects.withStroke(linewidth=1.6, foreground="#111214")])


def build_kill_map(
    match: dict[str, Any], timeline: MatchTimeline, participant: dict[str, Any]
) -> discord.File | None:
    """One player's kills and deaths plotted on the minimap, numbered in game order.

    Returns None when matplotlib is missing or the player neither killed nor
    died — an empty map is worse than no map.
    """
    if not MATPLOTLIB_AVAILABLE:
        LOGGER.warning("matplotlib is not installed; skipping the kill map")
        return None

    kills, deaths = timeline.kills_and_deaths(participant.get("participantId"))
    if not kills and not deaths:
        return None

    map_id = match.get("info", {}).get("mapId") or _DEFAULT_MAP_ID
    bounds = _MAP_BOUNDS.get(map_id)
    if bounds is None:
        # An unknown map's bounds would put every marker in the wrong place.
        LOGGER.info("No map bounds for mapId %s; skipping the kill map", map_id)
        return None

    catalog = ddragon.catalog()
    champion = catalog.by_key(participant.get("championId")) if catalog else None
    champion_name = champion.name if champion else participant.get("championName", "Unknown")

    left, bottom, right, top = bounds
    figure, axes = plt.subplots(figsize=_KILL_MAP_FIGURE_SIZE, dpi=_FIGURE_DPI)
    try:
        figure.patch.set_facecolor(_BACKGROUND)
        axes.set_facecolor(_BACKGROUND)

        _draw_map_background(axes, map_id, bounds)

        ordered = sorted(
            [(event, True) for event in kills] + [(event, False) for event in deaths],
            key=lambda pair: pair[0].timestamp,
        )
        for order, (event, is_kill) in enumerate(ordered, start=1):
            _plot_event(axes, event, order, is_kill=is_kill)

        axes.set_xlim(left, right)
        axes.set_ylim(bottom, top)
        axes.set_aspect("equal")
        axes.set_xticks([])
        axes.set_yticks([])
        for spine in axes.spines.values():
            spine.set_color(_GRID)

        axes.set_title(
            f"Kills & Deaths — {champion_name}", color=_TEXT, fontsize=14, fontweight="bold", pad=12
        )
        legend = axes.legend(
            [
                plt.Line2D([], [], marker="o", linestyle="none", color=_KILL_COLOR, markersize=9),
                plt.Line2D([], [], marker="D", linestyle="none", color=_DEATH_COLOR, markersize=8),
            ],
            [f"Kills ({len(kills)})", f"Deaths ({len(deaths)})"],
            loc="upper left",
            fontsize=9,
            labelcolor=_TEXT,
            facecolor=_BACKGROUND,
            edgecolor=_GRID,
            framealpha=0.85,
        )
        legend.set_zorder(5)

        figure.text(
            0.5,
            0.025,
            "Numbered in the order they happened",
            color=_MUTED,
            fontsize=8,
            ha="center",
        )
        figure.tight_layout(rect=(0, 0.04, 1, 1))

        buffer = io.BytesIO()
        figure.savefig(buffer, format="png", facecolor=figure.get_facecolor())
    finally:
        plt.close(figure)

    buffer.seek(0)
    return discord.File(buffer, filename=KILL_MAP_FILENAME)

"""Match charts rendered as image attachments.

matplotlib is optional: if it isn't installed the announcement still goes out,
just without the graph.
"""

from __future__ import annotations

import io
import logging
import threading
import time
from datetime import datetime
from typing import Any, Sequence

import discord

from . import ddragon
from .timeline import KillEvent, MatchTimeline

LOGGER = logging.getLogger(__name__)

try:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.patheffects as path_effects
    import matplotlib.pyplot as plt

    MATPLOTLIB_AVAILABLE = True
except ImportError:
    MATPLOTLIB_AVAILABLE = False


_BACKGROUND = "#2b2d31"
_GRID = "#40444b"
_TEXT = "#dcddde"
_MUTED = "#949ba4"
_TEAM_BLUE = "#5383E8"
_TEAM_RED = "#E84057"
_HIGHLIGHT = "#F5C542"


_FIGURE_SIZE = (9, 4.8)
_FIGURE_DPI = 150

DAMAGE_CHART_FILENAME = "damage.png"
KILL_MAP_FILENAME = "killmap.png"
LP_CHART_FILENAME = "lp-history.png"

_chart_lock = threading.RLock()

_KILL_MAP_FIGURE_SIZE = (6.4, 6.4)
_KILL_COLOR = "#3BA55D"
_DEATH_COLOR = _TEAM_RED


_MAP_BOUNDS: dict[int, tuple[int, int, int, int]] = {
    11: (-120, -120, 14870, 14980),
    12: (-28, -19, 12849, 12858),
}
_DEFAULT_MAP_ID = 11


def _build_damage_chart(
    match: dict[str, Any],
    highlight_puuids: Sequence[str] | set[str] = (),
    *,
    metric_field: str = "totalDamageDealtToChampions",
    chart_title: str = "Damage Dealt to Champions",
    filename: str = DAMAGE_CHART_FILENAME,
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

    if metric_field == "teamGoldDifference":
        blue_total = sum(
            int(p.get("goldEarned", 0) or 0)
            for p in participants
            if p.get("teamId") == 100
        )
        red_total = sum(
            int(p.get("goldEarned", 0) or 0)
            for p in participants
            if p.get("teamId") == 200
        )
        participants = [
            {"participantId": 100, "teamId": 100, "championName": "Blue Team", "_stat": blue_total - red_total},
            {"participantId": 200, "teamId": 200, "championName": "Red Team", "_stat": red_total - blue_total},
        ]

    catalog = ddragon.catalog()
    highlighted = set(highlight_puuids)

    gold_differences: dict[int, int] = {}
    if metric_field == "goldDifference":
        teams = {
            team_id: sorted(
                (p for p in participants if p.get("teamId") == team_id),
                key=lambda p: p.get("participantId", 0),
            )
            for team_id in (100, 200)
        }
        for index in range(max(len(teams[100]), len(teams[200]))):
            blue = teams[100][index] if index < len(teams[100]) else None
            red = teams[200][index] if index < len(teams[200]) else None
            if blue is not None and red is not None:
                difference = int(blue.get("goldEarned", 0) or 0) - int(
                    red.get("goldEarned", 0) or 0
                )
                gold_differences[blue.get("participantId", index)] = difference
                gold_differences[red.get("participantId", index)] = -difference

    def metric_value(participant: dict[str, Any]) -> int:
        """Return the selected statistic for one participant."""
        if metric_field == "healingAndShielding":
            return int(participant.get("totalHeal", 0) or 0) + int(
                participant.get("totalDamageShieldedOnTeammates", 0) or 0
            )
        if metric_field == "goldDifference":
            return gold_differences.get(participant.get("participantId", -1), 0)
        if metric_field == "teamGoldDifference":
            return int(participant.get("_stat", 0))
        return int(participant.get(metric_field, 0) or 0)

    rows = sorted(participants, key=metric_value)
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
        values.append(metric_value(participant))
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
            chart_title,
            color=_TEXT,
            fontsize=15,
            fontweight="bold",
            pad=14,
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
    return discord.File(buffer, filename=filename)


def _draw_map_background(axes, map_id: int, bounds: tuple[int, int, int, int]) -> None:
    """Lay the minimap art under the plot, or leave a flat panel if it's missing."""
    art = ddragon.map_image(map_id)
    if art is None:
        return
    left, bottom, right, top = bounds
    try:
        image = plt.imread(io.BytesIO(art), format="png")
    except (ValueError, OSError) as error:
        LOGGER.warning("Could not decode map %s art: %s", map_id, error)
        return

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
    label.set_path_effects(
        [path_effects.withStroke(linewidth=1.6, foreground="#111214")]
    )


def _build_kill_map(
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
        LOGGER.info("No map bounds for mapId %s; skipping the kill map", map_id)
        return None

    catalog = ddragon.catalog()
    champion = catalog.by_key(participant.get("championId")) if catalog else None
    champion_name = (
        champion.name if champion else participant.get("championName", "Unknown")
    )

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
            f"Kills & Deaths — {champion_name}",
            color=_TEXT,
            fontsize=14,
            fontweight="bold",
            pad=12,
        )
        legend = axes.legend(
            [
                plt.Line2D(
                    [],
                    [],
                    marker="o",
                    linestyle="none",
                    color=_KILL_COLOR,
                    markersize=9,
                ),
                plt.Line2D(
                    [],
                    [],
                    marker="D",
                    linestyle="none",
                    color=_DEATH_COLOR,
                    markersize=8,
                ),
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


def build_damage_chart(
    match: dict[str, Any],
    highlight_puuids: Sequence[str] | set[str] = (),
    *,
    metric_field: str = "totalDamageDealtToChampions",
    chart_title: str = "Damage Dealt to Champions",
    filename: str = DAMAGE_CHART_FILENAME,
) -> discord.File | None:
    """Thread-safe wrapper around matplotlib's process-global pyplot state."""
    with _chart_lock:
        return _build_damage_chart(
            match,
            highlight_puuids,
            metric_field=metric_field,
            chart_title=chart_title,
            filename=filename,
        )


def _build_team_gold_difference_chart(
    timeline: dict[str, Any], *, filename: str = "team-gold-difference.png"
) -> discord.File | None:
    """Render team gold difference over the course of a match."""
    if not MATPLOTLIB_AVAILABLE:
        return None
    frames = timeline.get("info", {}).get("frames", []) or []
    if not frames:
        return None
    minutes: list[float] = []
    differences: list[int] = []
    for frame in frames:
        totals = {100: 0, 200: 0}
        for participant in (frame.get("participantFrames", {}) or {}).values():
            team_id = 100 if int(participant.get("participantId", 0)) <= 5 else 200
            totals[team_id] += int(participant.get("totalGold", 0) or 0)
        minutes.append(float(frame.get("timestamp", 0)) / 60000)
        differences.append(totals[100] - totals[200])

    figure, axes = plt.subplots(figsize=_FIGURE_SIZE, dpi=_FIGURE_DPI)
    try:
        figure.patch.set_facecolor(_BACKGROUND)
        axes.set_facecolor(_BACKGROUND)
        positive = [value if value >= 0 else 0 for value in differences]
        negative = [value if value < 0 else 0 for value in differences]
        axes.axhline(0, color=_TEXT, linewidth=0.8)
        for index in range(len(minutes) - 1):
            x1, x2 = minutes[index], minutes[index + 1]
            y1, y2 = differences[index], differences[index + 1]
            if y1 == 0 or y2 == 0 or (y1 > 0) == (y2 > 0):
                axes.plot([x1, x2], [y1, y2], color=_TEAM_BLUE if y1 >= 0 and y2 >= 0 else _TEAM_RED, linewidth=2.2)
                continue
            crossing = x1 + (0 - y1) * (x2 - x1) / (y2 - y1)
            axes.plot([x1, crossing], [y1, 0], color=_TEAM_BLUE if y1 > 0 else _TEAM_RED, linewidth=2.2)
            axes.plot([crossing, x2], [0, y2], color=_TEAM_BLUE if y2 > 0 else _TEAM_RED, linewidth=2.2)
        axes.scatter(
            minutes,
            differences,
            color=[_TEAM_BLUE if value >= 0 else _TEAM_RED for value in differences],
            edgecolors=_TEXT,
            linewidths=0.7,
            s=22,
            zorder=3,
        )
        axes.fill_between(minutes, positive, 0, color=_TEAM_BLUE, alpha=0.35)
        axes.fill_between(minutes, negative, 0, color=_TEAM_RED, alpha=0.45)
        axes.set_title("Team Gold Difference", color=_TEXT, fontsize=15, fontweight="bold")
        axes.set_xlabel("Game Time", color=_MUTED)
        axes.set_ylabel("Blue − Red Gold", color=_MUTED)
        axes.tick_params(colors=_TEXT)
        axes.grid(axis="y", color=_GRID, alpha=0.6)
        for spine in axes.spines.values():
            spine.set_color(_GRID)
        figure.tight_layout(pad=1.4)
        buffer = io.BytesIO()
        figure.savefig(buffer, format="png", facecolor=figure.get_facecolor())
    finally:
        plt.close(figure)
    buffer.seek(0)
    return discord.File(buffer, filename=filename)


def build_team_gold_difference_chart(
    timeline: dict[str, Any], *, filename: str = "team-gold-difference.png"
) -> discord.File | None:
    """Thread-safe wrapper for the timeline gold-difference chart."""
    with _chart_lock:
        return _build_team_gold_difference_chart(timeline, filename=filename)


def build_kill_map(
    match: dict[str, Any], timeline: MatchTimeline, participant: dict[str, Any]
) -> discord.File | None:
    """Build kill map."""
    with _chart_lock:
        return _build_kill_map(match, timeline, participant)


def build_lp_chart(
    entries: Sequence[dict[str, Any]], *, days: int = 30
) -> discord.File | None:
    """Step plot of persisted rank values, with no network dependency."""
    if not MATPLOTLIB_AVAILABLE:
        return None
    cutoff = time.time() - max(days, 1) * 86400
    points = [
        entry
        for entry in entries
        if entry.get("v") is not None and entry.get("t", 0) >= cutoff
    ]
    if not points:
        return None
    with _chart_lock:
        figure, axes = plt.subplots(figsize=_FIGURE_SIZE, dpi=_FIGURE_DPI)
        try:
            figure.patch.set_facecolor(_BACKGROUND)
            axes.set_facecolor(_BACKGROUND)
            x_values = [datetime.fromtimestamp(entry["t"]) for entry in points]
            y_values = [entry["v"] for entry in points]
            axes.step(x_values, y_values, where="post", color=_HIGHLIGHT, linewidth=2)
            axes.scatter(x_values, y_values, color=_HIGHLIGHT, s=18, zorder=3)
            axes.set_title("LP History", color=_TEXT, fontsize=15, fontweight="bold")
            axes.tick_params(colors=_MUTED)
            axes.grid(color=_GRID, alpha=0.6)
            for spine in axes.spines.values():
                spine.set_color(_GRID)
            figure.autofmt_xdate()
            figure.tight_layout()
            buffer = io.BytesIO()
            figure.savefig(buffer, format="png", facecolor=figure.get_facecolor())
        finally:
            plt.close(figure)
    buffer.seek(0)
    return discord.File(buffer, filename=LP_CHART_FILENAME)

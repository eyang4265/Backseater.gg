"""Match charts rendered as image attachments.

matplotlib is optional: if it isn't installed the announcement still goes out,
just without the graph.
"""

from __future__ import annotations

import io
import logging
import math
import threading
import time
from datetime import datetime
from typing import Any, Sequence

import discord

from . import ddragon
from .ranks import TIER_LABELS, value_to_rank
from .timeline import KillEvent, MatchTimeline

LOGGER = logging.getLogger(__name__)

try:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.patheffects as path_effects
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    from matplotlib.colors import PowerNorm
    from matplotlib.lines import Line2D

    MATPLOTLIB_AVAILABLE = True
except ImportError:
    MATPLOTLIB_AVAILABLE = False
    LOGGER.warning("matplotlib is unavailable; chart rendering is disabled")


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
_FOUNTAINS: dict[int, tuple[tuple[int, int], ...]] = {
    11: ((400, 400), (14_400, 14_400)),
}
_FOUNTAIN_RADIUS = 2_200
_LANE_SIGMA = 2_000.0
_BOUNDARY_BLEND_RADIUS = 1_500.0
_HEATMAP_LANE_COLORS = {"Top": "#5383E8", "Mid": "#F5C542", "Bottom": "#E84057"}
_TOP_MID_BOUNDARY_COLOR = "#36D17C"
_MID_BOTTOM_BOUNDARY_COLOR = "#FF9F1C"
_HEATMAP_KILL_WEIGHT = 5


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
            {"participantId": 100, "teamId": 100, "championName": "Blue", "_stat": blue_total - red_total},
            {"participantId": 200, "teamId": 200, "championName": "Red", "_stat": red_total - blue_total},
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

        # Plot against numeric positions rather than the label strings: two
        # players on the same champion share a label, and passing duplicate
        # category strings to barh collapses both bars onto one row.
        positions = list(range(len(labels)))
        bars = axes.barh(
            positions,
            values,
            color=colors,
            edgecolor=edge_colors,
            linewidth=edge_widths,
            height=0.68,
            zorder=3,
        )
        axes.set_yticks(positions)
        axes.set_yticklabels(labels)

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
            ["Blue", "Red"],
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
    LOGGER.debug("Building damage chart: metric_field=%s title=%s", metric_field, chart_title)
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
    LOGGER.debug("Building team gold-difference chart")
    with _chart_lock:
        return _build_team_gold_difference_chart(timeline, filename=filename)


def _in_base(x: float, y: float, map_id: int = _DEFAULT_MAP_ID) -> bool:
    """Return whether a position is inside either team's fountain/shop area."""
    return any(
        (x - fountain_x) ** 2 + (y - fountain_y) ** 2 <= _FOUNTAIN_RADIUS ** 2
        for fountain_x, fountain_y in _FOUNTAINS.get(map_id, ())
    )


# The initial Scuttler shrines are the fixed river anchors for these routes.
# Do not use a later Crab kill position: Scuttler patrols before it is killed.
_BOT_SCUTTLE_SPAWN = (10_000, 5_000)
_TOP_SCUTTLE_SPAWN = (5_000, 10_000)

_BOT_SIDE_BOUNDARY = (
    (0, 0),          # blue nexus
    (7050, 4000),    # blue-side red buff
    _BOT_SCUTTLE_SPAWN,
    (10600, 6400),   # red-side blue buff
    (14500, 14500),  # red nexus
)
_TOP_SIDE_BOUNDARY = (
    (0, 0),          # blue nexus
    (3870, 7900),    # blue-side blue buff
    _TOP_SCUTTLE_SPAWN,
    (7450, 10500),   # red-side red buff
    (14500, 14500),  # red nexus
)
_MID_CORRIDOR = _BOT_SIDE_BOUNDARY + _TOP_SIDE_BOUNDARY[-2:0:-1]


def _in_mid_corridor(x: float, y: float) -> bool:
    """Return whether a point lies between the two requested jungle routes.

    The bottom boundary runs blue Nexus → blue-side red buff → bot Scuttle →
    red-side blue buff → red Nexus.  The top boundary runs blue Nexus →
    blue-side blue buff → top Scuttle → red-side red buff → red Nexus.
    The top route is reversed only while joining the two routes into a polygon.
    """
    inside = False
    points = _MID_CORRIDOR
    previous_x, previous_y = points[-1]
    for current_x, current_y in points:
        crosses = (current_y > y) != (previous_y > y)
        if crosses:
            intersection_x = (
                (previous_x - current_x) * (y - current_y)
                / (previous_y - current_y)
                + current_x
            )
            if x < intersection_x:
                inside = not inside
        previous_x, previous_y = current_x, current_y
    return inside


def _lane_distances(x: float, y: float) -> dict[str, float]:
    """Return squared distances from a position to each lane segment."""
    def distance(ax: int, ay: int, bx: int, by: int) -> float:
        dx, dy = bx - ax, by - ay
        scale = max(dx * dx + dy * dy, 1)
        t = max(0, min(1, ((x - ax) * dx + (y - ay) * dy) / scale))
        return (x - (ax + t * dx)) ** 2 + (y - (ay + t * dy)) ** 2

    distances = {
        "Top": distance(0, 14500, 7000, 7000),
        "Mid": distance(0, 0, 14500, 14500),
        "Bottom": distance(7000, 7000, 14500, 0),
    }
    if _in_mid_corridor(x, y):
        # The area bounded by the two requested nexus→buff→scuttle→buff→nexus
        # routes is Mid, even when it is closer to a side lane's centerline.
        distances["Mid"] = 0.0
    return distances


def _boundary_y_at_x(route: tuple[tuple[int, int], ...], x: float) -> float:
    """Interpolate a left-to-right boundary route at a map X coordinate."""
    for (left_x, left_y), (right_x, right_y) in zip(route, route[1:]):
        if left_x <= x <= right_x:
            fraction = (x - left_x) / max(right_x - left_x, 1)
            return left_y + fraction * (right_y - left_y)
    return route[0][1] if x < route[0][0] else route[-1][1]


def _lane_region(x: float, y: float) -> str:
    """Classify a position as Top, Mid, or Bottom from the route boundaries."""
    if _in_mid_corridor(x, y):
        return "Mid"
    top_boundary_y = _boundary_y_at_x(_TOP_SIDE_BOUNDARY, x)
    bottom_boundary_y = _boundary_y_at_x(_BOT_SIDE_BOUNDARY, x)
    if y >= top_boundary_y:
        return "Top"
    if y <= bottom_boundary_y:
        return "Bottom"
    # This covers a point on a boundary and any numerical edge case in the
    # polygon test. The region between the two routes is always Mid.
    return "Mid"


def _distance_to_route(x: float, y: float, route: tuple[tuple[int, int], ...]) -> float:
    """Return the shortest distance from a position to a boundary route."""
    nearest = math.inf
    for (start_x, start_y), (end_x, end_y) in zip(route, route[1:]):
        delta_x, delta_y = end_x - start_x, end_y - start_y
        length_squared = max(delta_x * delta_x + delta_y * delta_y, 1)
        progress = max(
            0.0,
            min(1.0, ((x - start_x) * delta_x + (y - start_y) * delta_y) / length_squared),
        )
        nearest = min(
            nearest,
            math.hypot(x - (start_x + progress * delta_x), y - (start_y + progress * delta_y)),
        )
    return nearest


def _lane_weights(x: float, y: float) -> dict[str, float]:
    """Return lane membership, blending the two lanes around route boundaries."""
    lane = _lane_region(x, y)
    weights = {candidate: float(candidate == lane) for candidate in ("Top", "Mid", "Bottom")}
    top_distance = _distance_to_route(x, y, _TOP_SIDE_BOUNDARY)
    bottom_distance = _distance_to_route(x, y, _BOT_SIDE_BOUNDARY)
    if top_distance <= _BOUNDARY_BLEND_RADIUS and top_distance <= bottom_distance:
        blend = top_distance / _BOUNDARY_BLEND_RADIUS
        if lane == "Top":
            weights["Top"], weights["Mid"] = 0.5 + blend / 2, 0.5 - blend / 2
        else:
            weights["Top"], weights["Mid"] = 0.5 - blend / 2, 0.5 + blend / 2
    elif bottom_distance <= _BOUNDARY_BLEND_RADIUS:
        blend = bottom_distance / _BOUNDARY_BLEND_RADIUS
        if lane == "Bottom":
            weights["Bottom"], weights["Mid"] = 0.5 + blend / 2, 0.5 - blend / 2
        else:
            weights["Bottom"], weights["Mid"] = 0.5 - blend / 2, 0.5 + blend / 2
    return weights


def _heatmap_event_color(x: float, y: float, lane: str) -> str:
    """Return a shared-region color for events within a boundary blend band."""
    weights = _lane_weights(x, y)
    if weights["Top"] and weights["Mid"]:
        return _TOP_MID_BOUNDARY_COLOR
    if weights["Mid"] and weights["Bottom"]:
        return _MID_BOTTOM_BOUNDARY_COLOR
    return _HEATMAP_LANE_COLORS.get(lane, _MUTED)


def _lane_from_position(position: dict[str, Any] | None) -> str:
    """Return the boundary-defined lane for a Summoner's Rift map position."""
    if not position or position.get("x") is None or position.get("y") is None:
        return "Mid"
    return _lane_region(position["x"], position["y"])


def _lane_for_jungle_involvement(
    event: dict[str, Any], jungler_id: int, participants: dict[int, dict[str, Any]],
) -> str:
    """Choose a lane from the fight location, falling back to participant roles."""
    position = event.get("position") or {}
    if position.get("x") is not None and position.get("y") is not None:
        return _lane_from_position(position)
    lane_by_role = {"TOP": "Top", "MIDDLE": "Mid", "BOTTOM": "Bottom", "UTILITY": "Bottom"}
    victim = participants.get(event.get("victimId")) or {}
    victim_role = (victim.get("teamPosition") or "").upper()
    if victim_role in lane_by_role:
        return lane_by_role[victim_role]
    if victim_role == "JUNGLE":
        involved_ids = [event.get("killerId"), *(event.get("assistingParticipantIds") or [])]
        if event.get("killerId") == jungler_id:
            involved_ids = involved_ids[1:]
        for participant_id in involved_ids:
            if participant_id == jungler_id:
                continue
            role = ((participants.get(participant_id) or {}).get("teamPosition") or "").upper()
            if role in lane_by_role:
                return lane_by_role[role]
    return _lane_from_position(event.get("position"))


def jungle_checkpoint_minutes(game_duration: int | float) -> tuple[int, ...]:
    """Return every completed five-minute checkpoint through match end."""
    duration_seconds = float(game_duration or 0)
    if duration_seconds > 100_000:
        duration_seconds /= 1000
    final_checkpoint = int(duration_seconds // 300) * 5
    return tuple(range(5, final_checkpoint + 1, 5)) or (5,)


def jungle_proximity_percentages(
    timeline: dict[str, Any], jungler_id: int, participants: list[dict[str, Any]] | None = None,
    max_minutes: int = 15,
) -> dict[str, float]:
    """Return the sampling-invariant blended proximity score for each lane."""
    breakdown = jungle_proximity_breakdown(
        timeline, jungler_id, participants, max_minutes
    )
    return {lane: values["score"] for lane, values in breakdown.items()}


def jungle_proximity_breakdown(
    timeline: dict[str, Any], jungler_id: int,
    participants: list[dict[str, Any]] | None = None,
    max_minutes: int = 15, *, alpha: float = 0.65,
) -> dict[str, dict[str, float]]:
    """Return presence, involvement, camp-hover, and blended lane percentages.

    Presence measures on-map dwell time plus lightly weighted camp locations;
    involvement counts kills, assists, and deaths. Each channel is normalized
    before blending so timelines sampled at different rates remain comparable.
    Hover is the camp-derived portion of the reported presence percentage.
    """
    lanes = ("Top", "Mid", "Bottom")
    presence = dict.fromkeys(lanes, 0.0)
    involvement = dict.fromkeys(lanes, 0.0)
    hover = dict.fromkeys(lanes, 0.0)
    cutoff = max_minutes * 60 * 1000
    info = timeline.get("info", {}) or {}
    frames = sorted(
        (
            frame for frame in (info.get("frames", []) or [])
            if frame.get("timestamp", 0) <= cutoff
        ),
        key=lambda frame: frame.get("timestamp", 0),
    )
    frame_interval = float(info.get("frameInterval", 60_000) or 60_000)
    map_id = int(info.get("mapId", _DEFAULT_MAP_ID) or _DEFAULT_MAP_ID)

    for index, frame in enumerate(frames):
        timestamp = frame.get("timestamp", 0)
        next_timestamp = (
            frames[index + 1].get("timestamp", timestamp)
            if index + 1 < len(frames)
            else min(timestamp + frame_interval, cutoff)
        )
        duration_minutes = max(min(next_timestamp, cutoff) - timestamp, 0) / 60_000
        position = (frame.get("participantFrames", {}) or {}).get(
            str(jungler_id), {}
        ).get("position") or {}
        x, y = position.get("x"), position.get("y")
        if (
            duration_minutes
            and x is not None
            and y is not None
            and not _in_base(x, y, map_id)
        ):
            for lane, weight in _lane_weights(x, y).items():
                presence[lane] += duration_minutes * weight

    participant_by_id = {
        p.get("participantId"): p for p in (participants or [])
    }
    for frame in frames:
        for event in frame.get("events", []) or []:
            if event.get("timestamp", frame.get("timestamp", 0)) > cutoff:
                continue
            if event.get("type") == "CHAMPION_KILL":
                involved = (
                    event.get("killerId") == jungler_id
                    or event.get("victimId") == jungler_id
                    or jungler_id in (event.get("assistingParticipantIds") or [])
                )
                if not involved:
                    continue
                position = event.get("position") or {}
                if position.get("x") is not None and position.get("y") is not None:
                    weights = _lane_weights(position["x"], position["y"])
                else:
                    lane = _lane_for_jungle_involvement(
                        event, jungler_id, participant_by_id
                    )
                    weights = {candidate: float(candidate == lane) for candidate in lanes}
                for lane, weight in weights.items():
                    involvement[lane] += weight
                continue
            if (
                event.get("type") not in {"MONSTER_KILL", "ELITE_MONSTER_KILL"}
                or event.get("killerId") != jungler_id
            ):
                continue
            position = event.get("position") or {}
            if position.get("x") is None or position.get("y") is None:
                continue
            for lane, weight in _lane_weights(position["x"], position["y"]).items():
                camp_weight = 0.4 * weight
                presence[lane] += camp_weight
                hover[lane] += camp_weight

    presence_total = sum(presence.values())
    involvement_total = sum(involvement.values())
    presence_share = {
        lane: presence[lane] / presence_total * 100 if presence_total else 0.0
        for lane in lanes
    }
    involvement_share = {
        lane: involvement[lane] / involvement_total * 100 if involvement_total else 0.0
        for lane in lanes
    }
    hover_share = {
        lane: hover[lane] / presence_total * 100 if presence_total else 0.0
        for lane in lanes
    }
    if not presence_total:
        blend_alpha = 0.0
    elif not involvement_total:
        blend_alpha = 1.0
    else:
        blend_alpha = alpha
    return {
        lane: {
            "presence": presence_share[lane],
            "involvement": involvement_share[lane],
            "hover": hover_share[lane],
            "score": (
                blend_alpha * presence_share[lane]
                + (1 - blend_alpha) * involvement_share[lane]
            )
        }
        for lane in lanes
    }


def jungle_lane_involvement(
    timeline: dict[str, Any], jungler_id: int, participants: list[dict[str, Any]],
    max_minutes: int = 15,
) -> dict[str, dict[str, int]]:
    """Count every jungler kill, assist, and death once by lane."""
    result = {
        lane: {"kills": 0, "assists": 0, "deaths": 0}
        for lane in ("Top", "Mid", "Bottom")
    }
    by_id = {p.get("participantId"): p for p in participants}
    for frame in timeline.get("info", {}).get("frames", []) or []:
        for event in frame.get("events", []) or []:
            if event.get("type") != "CHAMPION_KILL":
                continue
            if event.get("timestamp", frame.get("timestamp", 0)) > max_minutes * 60 * 1000:
                continue
            killer_id = event.get("killerId")
            assistants = event.get("assistingParticipantIds") or []
            lane = _lane_for_jungle_involvement(event, jungler_id, by_id)
            if killer_id == jungler_id:
                result[lane]["kills"] += 1
            if jungler_id in assistants:
                result[lane]["assists"] += 1
            if event.get("victimId") == jungler_id:
                result[lane]["deaths"] += 1
    return result


def jungle_kda_at(
    timeline: dict[str, Any], jungler_id: int, max_minutes: int
) -> tuple[int, int, int]:
    """Return cumulative jungler KDA using each kill event's timestamp cutoff."""
    kills = deaths = assists = 0
    for frame in timeline.get("info", {}).get("frames", []) or []:
        for event in frame.get("events", []) or []:
            if event.get("type") != "CHAMPION_KILL":
                continue
            if event.get("timestamp", frame.get("timestamp", 0)) > max_minutes * 60 * 1000:
                continue
            if event.get("killerId") == jungler_id:
                kills += 1
            if event.get("victimId") == jungler_id:
                deaths += 1
            if jungler_id in (event.get("assistingParticipantIds") or []):
                assists += 1
    return kills, deaths, assists


def jungle_kill_positions(
    timeline: dict[str, Any], jungler_id: int, max_minutes: int = 20,
) -> list[tuple[int, int]]:
    """Return the jungler's valid kill positions in chronological order before a cutoff."""
    kills: list[tuple[int, int, int]] = []
    cutoff = max_minutes * 60 * 1000
    for frame in timeline.get("info", {}).get("frames", []) or []:
        for event in frame.get("events", []) or []:
            timestamp = event.get("timestamp", frame.get("timestamp", 0))
            position = event.get("position") or {}
            if (
                event.get("type") == "CHAMPION_KILL"
                and event.get("killerId") == jungler_id
                and timestamp <= cutoff
                and position.get("x") is not None
                and position.get("y") is not None
            ):
                kills.append((timestamp, position["x"], position["y"]))
    kills.sort(key=lambda kill: kill[0])
    return [(x, y) for _, x, y in kills]


def jungle_position_samples(
    timeline: dict[str, Any], jungler_id: int, max_minutes: int = 20,
    *, limit: int | None = None, include_initial: bool = False,
) -> list[tuple[int, float, float, str]]:
    """Return numbered, non-base position samples after the ignored first sample.

    The initial valid position is commonly the spawn location, so it is omitted
    by default. Fountain/shop samples are then removed without consuming the
    marker limit; ``include_initial`` can retain an initial on-map sample. Every
    remaining sample is numbered and rendered by default (``limit=None``).
    """
    samples = _jungle_position_records(
        timeline, jungler_id, max_minutes, include_initial=include_initial
    )
    retained = samples if limit is None else samples[:max(limit, 0)]
    return [
        (number, x, y, lane)
        for number, (_, x, y, lane) in enumerate(retained, start=1)
    ]


def jungle_position_sample_timestamps(
    timeline: dict[str, Any], jungler_id: int, max_minutes: int = 20,
    *, limit: int | None = None,
) -> list[tuple[int, int]]:
    """Return retained non-base hexagon numbers and timeline timestamps."""
    timestamps = [
        timestamp
        for timestamp, _, _, _ in _jungle_position_records(
            timeline, jungler_id, max_minutes
        )
    ]
    retained = timestamps if limit is None else timestamps[:max(limit, 0)]
    return list(enumerate(retained, start=1))


def _jungle_position_records(
    timeline: dict[str, Any], jungler_id: int, max_minutes: int,
    *, include_initial: bool = False,
) -> list[tuple[int, float, float, str]]:
    """Return chronological on-map records, optionally retaining the spawn sample."""
    cutoff = max_minutes * 60 * 1000
    info = timeline.get("info", {}) or {}
    map_id = int(info.get("mapId", _DEFAULT_MAP_ID) or _DEFAULT_MAP_ID)
    records: list[tuple[int, float, float, dict[str, Any]]] = []
    for frame in info.get("frames", []) or []:
        timestamp = frame.get("timestamp", 0)
        position = (frame.get("participantFrames", {}) or {}).get(
            str(jungler_id), {}
        ).get("position") or {}
        x, y = position.get("x"), position.get("y")
        if (
            timestamp <= cutoff
            and x is not None
            and y is not None
        ):
            records.append((timestamp, x, y, position))
    records.sort(key=lambda record: record[0])
    if not include_initial:
        records = records[1:]
    return [
        (timestamp, x, y, _lane_from_position(position))
        for timestamp, x, y, position in records
        if not _in_base(x, y, map_id)
    ]


def _jungle_heatmap_end_timestamp(
    timeline: dict[str, Any], jungler_id: int, max_minutes: int,
) -> int:
    """Timestamp of the last available jungler position in the heatmap window."""
    cutoff = max_minutes * 60 * 1000
    timestamps = []
    for frame in timeline.get("info", {}).get("frames", []) or []:
        position = (frame.get("participantFrames", {}) or {}).get(
            str(jungler_id), {}
        ).get("position") or {}
        if (
            frame.get("timestamp", 0) <= cutoff
            and position.get("x") is not None
            and position.get("y") is not None
        ):
            timestamps.append(frame.get("timestamp", 0))
    return max(timestamps) if timestamps else -1


def jungle_heatmap_takedowns(
    timeline: dict[str, Any], jungler_id: int, max_minutes: int = 20,
    participants: list[dict[str, Any]] | None = None,
) -> list[tuple[str, str, int, int, int]]:
    """Return positioned jungler kills, assists, deaths, and camps through 15 minutes."""
    end_timestamp = _jungle_heatmap_end_timestamp(timeline, jungler_id, max_minutes)
    if end_timestamp < 0:
        return []
    cutoff = min(end_timestamp + 1, max_minutes * 60 * 1000)
    participant_by_id = {
        participant.get("participantId"): participant
        for participant in (participants or [])
    }
    takedowns: list[tuple[str, str, int, int, int]] = []
    for frame in timeline.get("info", {}).get("frames", []) or []:
        for event in frame.get("events", []) or []:
            timestamp = event.get("timestamp", frame.get("timestamp", 0))
            position = event.get("position") or {}
            is_kill = event.get("killerId") == jungler_id
            is_assist = jungler_id in (event.get("assistingParticipantIds") or [])
            is_death = event.get("victimId") == jungler_id
            is_camp = (
                event.get("type") in {"MONSTER_KILL", "ELITE_MONSTER_KILL"}
                and event.get("killerId") == jungler_id
            )
            if (
                event.get("type") == "CHAMPION_KILL"
                and (is_kill or is_assist or is_death)
                and timestamp < cutoff
                and position.get("x") is not None
                and position.get("y") is not None
            ):
                takedowns.append(
                    (
                        "kill" if is_kill else "assist" if is_assist else "death",
                        (_lane_from_position(position) if is_death else
                         _lane_for_jungle_involvement(event, jungler_id, participant_by_id)),
                        timestamp, position["x"], position["y"],
                    )
                )
            elif is_camp and timestamp < cutoff and position.get("x") is not None and position.get("y") is not None:
                takedowns.append(("camp", _lane_from_position(position), timestamp, position["x"], position["y"]))
    takedowns.sort(key=lambda takedown: takedown[2])
    return takedowns


def jungle_heatmap_kill_positions(
    timeline: dict[str, Any], jungler_id: int, max_minutes: int = 20,
) -> list[tuple[int, int]]:
    """Return jungler kill positions in the heatmap window."""
    return [
        (x, y)
        for kind, _, _, x, y in jungle_heatmap_takedowns(
            timeline, jungler_id, max_minutes
        )
        if kind == "kill"
    ]


def jungle_heatmap_density_points(
    timeline: dict[str, Any], jungler_id: int, max_minutes: int = 20,
    participants: list[dict[str, Any]] | None = None,
) -> list[tuple[float, float]]:
    """Return movement and combat points used to calculate heatmap density.

    The ignored initial movement sample stays excluded. Each jungler kill,
    assist, death, or camp is repeated five times as a rendering choice so
    activity locations contribute heat; this is separate from proximity scoring.
    """
    movement = [
        (x, y)
        for _, x, y, _ in jungle_position_samples(timeline, jungler_id, max_minutes)
    ]
    takedowns = [
        (x, y)
        for _, _, _, x, y in jungle_heatmap_takedowns(
            timeline, jungler_id, max_minutes, participants
        )
    ]
    return movement + takedowns * _HEATMAP_KILL_WEIGHT


def jungle_path_points(
    timeline: dict[str, Any], jungler_id: int, max_minutes: int = 20,
    participants: list[dict[str, Any]] | None = None,
) -> list[tuple[float, float]]:
    """Interleave movement samples and jungler takedowns into one timed path.

    The first valid movement sample remains ignored. A kill between two sampled
    positions is inserted between those positions, so the rendered route visits
    the combat location at the correct point in time.
    """
    cutoff = _jungle_heatmap_end_timestamp(timeline, jungler_id, max_minutes)
    if cutoff < 0:
        return []
    map_id = int((timeline.get("info", {}) or {}).get("mapId", _DEFAULT_MAP_ID) or _DEFAULT_MAP_ID)
    timed_points: list[tuple[int, int, float, float]] = []
    movement: list[tuple[int, float, float]] = []
    for frame in timeline.get("info", {}).get("frames", []) or []:
        timestamp = frame.get("timestamp", 0)
        position = (frame.get("participantFrames", {}) or {}).get(
            str(jungler_id), {}
        ).get("position")
        if (
            timestamp <= cutoff
            and position
            and position.get("x") is not None
            and position.get("y") is not None
        ):
            movement.append((timestamp, position["x"], position["y"]))
    timed_points.extend(
        (timestamp, 1, x, y)
        for _, _, timestamp, x, y in jungle_heatmap_takedowns(
            timeline, jungler_id, max_minutes, participants
        )
    )
    movement.sort(key=lambda point: point[0])
    timed_points.extend(
        (timestamp, 0, x, y)
        for timestamp, x, y in movement[1:]
        if not _in_base(x, y, map_id)
    )
    timed_points.sort(key=lambda point: (point[0], point[1]))
    return [(x, y) for _, _, x, y in timed_points]


def build_jungle_heatmap(
    match: dict[str, Any], timeline: dict[str, Any], jungler_id: int,
    *, filename: str = "jungle-heatmap.png", max_minutes: int = 20,
    show_boundaries: bool = False,
) -> discord.File | None:
    """Render a jungler density heatmap showing every recorded position and takedown.

    Overlapping position/takedown markers are spread apart in a spiral, and their
    labels are staggered outward, so tightly clustered events stay legible.
    """
    if not MATPLOTLIB_AVAILABLE:
        return None
    LOGGER.debug("Building jungle heatmap: jungler_id=%s max_minutes=%s", jungler_id, max_minutes)
    points = jungle_position_samples(timeline, jungler_id, max_minutes)
    participants = match.get("info", {}).get("participants", []) or []
    point_timestamps = dict(
        jungle_position_sample_timestamps(timeline, jungler_id, max_minutes)
    )
    density_points = jungle_heatmap_density_points(
        timeline, jungler_id, max_minutes, participants
    )
    path_points = jungle_path_points(
        timeline, jungler_id, max_minutes, participants
    )
    if not density_points:
        return None
    takedowns = jungle_heatmap_takedowns(
        timeline, jungler_id, max_minutes, participants
    )
    with _chart_lock:
        figure = plt.figure(figsize=(7.4, 8.8), dpi=_FIGURE_DPI)
        try:
            figure.patch.set_facecolor(_BACKGROUND)
            axes = figure.add_axes((0.03, 0.15, 0.94, 0.76))
            axes.set_facecolor(_BACKGROUND)
            map_id = match.get("info", {}).get("mapId", _DEFAULT_MAP_ID)
            bounds = _MAP_BOUNDS.get(map_id, _MAP_BOUNDS[_DEFAULT_MAP_ID])
            _draw_map_background(axes, map_id, bounds)
            heat_xs, heat_ys = zip(*density_points)
            axes.hexbin(
                heat_xs, heat_ys, gridsize=30,
                extent=(bounds[0], bounds[2], bounds[1], bounds[3]),
                cmap="magma", mincnt=1, alpha=0.7, linewidths=0,
                norm=PowerNorm(gamma=0.55),
            )
            if path_points:
                path_xs, path_ys = zip(*path_points)
                axes.plot(
                    path_xs, path_ys, color="#f5c542", alpha=0.28,
                    linewidth=1, zorder=4,
                )
            if show_boundaries:
                grid_step = 2500
                axes.set_xticks(range(bounds[0], bounds[2] + 1, grid_step))
                axes.set_yticks(range(bounds[1], bounds[3] + 1, grid_step))
                axes.grid(
                    True, color="#ffffff", alpha=0.22, linewidth=0.7,
                    linestyle=":", zorder=3,
                )
                axes.set_xlabel("World X", color="white", labelpad=8)
                axes.set_ylabel("World Y", color="white", labelpad=8)
                axes.tick_params(colors="white", labelsize=7)
                for route, band_color, line_color, label in (
                    (_TOP_SIDE_BOUNDARY, _TOP_MID_BOUNDARY_COLOR, "#00e5ff", "Top-side boundary"),
                    (_BOT_SIDE_BOUNDARY, _MID_BOTTOM_BOUNDARY_COLOR, "#ff334f", "Bot-side boundary"),
                ):
                    route_xs = [point[0] for point in route]
                    route_ys = [point[1] for point in route]
                    axes.plot(
                        route_xs, route_ys, color=band_color, linewidth=52,
                        alpha=0.22, solid_capstyle="round", zorder=4.5,
                    )
                    axes.plot(
                        route_xs, route_ys, color=line_color, linestyle="--", linewidth=1.8,
                        alpha=0.95, label=label, zorder=5,
                    )
                for x, y, label, color in (
                    (*_TOP_SCUTTLE_SPAWN, "Top Scuttler spawn", "#00e5ff"),
                    (*_BOT_SCUTTLE_SPAWN, "Bot Scuttler spawn", "#ff334f"),
                ):
                    axes.scatter(
                        [x], [y], s=72, marker="o", facecolor=color,
                        edgecolor="white", linewidth=1.2, zorder=7,
                        label=label,
                    )
                    axes.annotate(
                        label, (x, y), xytext=(6, 6),
                        textcoords="offset points", color="white",
                        fontsize=8, fontweight="bold", zorder=8,
                    )

            span_x = bounds[2] - bounds[0]
            span_y = bounds[3] - bounds[1]
            label_bucket_counts: dict[tuple[int, int], int] = {}

            def _stagger(x: float, y: float, base_dy: float) -> tuple[float, float]:
                """Push a label's offset further from its marker each time another lands nearby."""
                key = (int(x / max(span_x / 24, 1)), int(y / max(span_y / 24, 1)))
                count = label_bucket_counts.get(key, 0)
                label_bucket_counts[key] = count + 1
                if count == 0:
                    return 0.0, base_dy
                direction = 1 if base_dy >= 0 else -1
                dy = base_dy + (count // 2 + 1) * 15 * direction
                dx = 22 * (count % 2 * 2 - 1) if count else 0
                return dx, dy

            def _spread_markers(cell: float, radius: float):
                """Nudge markers that land in the same small cell apart in a spiral."""
                bucket_counts: dict[tuple[int, int], int] = {}

                def jitter(x: float, y: float) -> tuple[float, float]:
                    key = (round(x / cell), round(y / cell))
                    count = bucket_counts.get(key, 0)
                    bucket_counts[key] = count + 1
                    if count == 0:
                        return x, y
                    angle = count * 2.399963229728653
                    r = radius * math.sqrt(count)
                    return x + r * math.cos(angle), y + r * math.sin(angle)

                return jitter

            text_outline = [
                path_effects.Stroke(linewidth=2.2, foreground=_BACKGROUND),
                path_effects.Normal(),
            ]

            marker_jitter = _spread_markers(span_x / 90, span_x / 26)
            events: list[tuple[int, float, float, float, str, str]] = [
                (point_timestamps[number], x, y, 130, "h", lane)
                for number, x, y, lane in points
            ]
            events.extend(
                (
                    timestamp, takedown_x, takedown_y, 220,
                    {"kill": "o", "assist": "D", "death": "X", "camp": "s"}[kind],
                    lane,
                )
                for kind, lane, timestamp, takedown_x, takedown_y in takedowns
            )
            events.sort(key=lambda event: event[0])
            for order, (_, x, y, size, marker, lane) in enumerate(events, start=1):
                jx, jy = marker_jitter(x, y)
                axes.scatter(
                    [jx], [jy], marker=marker, s=size,
                    color=_heatmap_event_color(x, y, lane),
                    edgecolors="#ffffff", linewidths=1.2, alpha=0.95, zorder=6,
                )
                axes.annotate(
                    str(order),
                    (jx, jy), xytext=_stagger(jx, jy, 15), textcoords="offset points",
                    color="#ffffff", fontsize=8, fontweight="bold",
                    ha="center", va="center", zorder=8, path_effects=text_outline,
                    arrowprops={
                        "arrowstyle": "-", "color": "#ffffff", "alpha": 0.5, "linewidth": 0.9,
                    },
                )
            axes.set_xlim(bounds[0], bounds[2])
            axes.set_ylim(bounds[1], bounds[3])
            axes.set_title(
                f"Jungle Proximity Heatmap — First {max_minutes} Minutes",
                color=_TEXT, fontweight="bold", fontsize=13, pad=8,
            )
            axes.set_axis_off()

            legend_handles = [
                Line2D([0], [0], marker="h", linestyle="", markerfacecolor=_HEATMAP_LANE_COLORS["Top"], markeredgecolor="#ffffff", markersize=10, label="Near Top"),
                Line2D([0], [0], marker="h", linestyle="", markerfacecolor=_HEATMAP_LANE_COLORS["Mid"], markeredgecolor="#ffffff", markersize=10, label="Near Mid"),
                Line2D([0], [0], marker="h", linestyle="", markerfacecolor=_HEATMAP_LANE_COLORS["Bottom"], markeredgecolor="#ffffff", markersize=10, label="Near Bot"),
                Line2D([0], [0], marker="o", linestyle="", markerfacecolor=_TOP_MID_BOUNDARY_COLOR, markeredgecolor="#ffffff", markersize=8, label="Top + Mid"),
                Line2D([0], [0], marker="o", linestyle="", markerfacecolor=_MID_BOTTOM_BOUNDARY_COLOR, markeredgecolor="#ffffff", markersize=8, label="Mid + Bot"),
                Line2D([0], [0], color="#f5c542", alpha=0.6, linewidth=2, label="Movement path"),
                Line2D([0], [0], marker="o", linestyle="", markerfacecolor=_MUTED, markeredgecolor="#ffffff", markersize=9, label="Kill"),
                Line2D([0], [0], marker="D", linestyle="", markerfacecolor=_MUTED, markeredgecolor="#ffffff", markersize=8, label="Assist"),
                Line2D([0], [0], marker="X", linestyle="", markerfacecolor=_MUTED, markeredgecolor="#ffffff", markersize=9, label="Death"),
                Line2D([0], [0], marker="s", linestyle="", markerfacecolor=_MUTED, markeredgecolor="#ffffff", markersize=8, label="Camp (through 15:00)"),
            ]
            figure.legend(
                handles=legend_handles, loc="lower center", ncol=4,
                bbox_to_anchor=(0.5, 0.005), frameon=True, fontsize=8.5,
                labelcolor=_TEXT, facecolor=_BACKGROUND, edgecolor=_GRID,
            )
            buffer = io.BytesIO()
            figure.savefig(buffer, format="png", facecolor=figure.get_facecolor())
        finally:
            plt.close(figure)
        buffer.seek(0)
        return discord.File(buffer, filename=filename)


def build_jungle_proximity_comparison_chart(
    checkpoints: Sequence[tuple[int, dict[int, dict[str, dict[str, float]] | None]]],
    *, filename: str = "jungle-proximity.png",
) -> discord.File | None:
    """Grouped bar chart comparing both team junglers' lane proximity at each checkpoint.

    `checkpoints` is a sequence of (minutes, breakdowns) pairs, where
    breakdowns maps team id (100/200) to that team's
    `jungle_proximity_breakdown` result, or None if no jungler was
    identified for that team. Checkpoints sit side by side on one axes, each
    with a Top/Mid/Bottom bar pair colored by team (blue vs red) only.
    """
    if not MATPLOTLIB_AVAILABLE or not checkpoints:
        LOGGER.debug("Skipping jungle proximity chart: matplotlib=%s checkpoints=%d", MATPLOTLIB_AVAILABLE, len(checkpoints))
        return None
    LOGGER.debug("Building jungle proximity comparison chart: checkpoints=%d", len(checkpoints))

    lanes = ("Top", "Mid", "Bottom")
    teams = (100, 200)
    team_colors = {100: _TEAM_BLUE, 200: _TEAM_RED}
    team_labels = {100: "Blue Jungler", 200: "Red Jungler"}
    lane_labels = {"Bottom": "Bot"}
    bar_width = 0.34
    lane_gap = 0.18
    slots = [lane_index * (2 * bar_width + lane_gap) for lane_index in range(len(lanes))]
    group_span = slots[-1] + 2 * bar_width
    group_centers = [
        group_index * (group_span + 0.5)
        for group_index in range(len(checkpoints))
    ]

    with _chart_lock:
        figure, axes = plt.subplots(figsize=_FIGURE_SIZE, dpi=_FIGURE_DPI)
        try:
            figure.patch.set_facecolor(_BACKGROUND)
            axes.set_facecolor(_BACKGROUND)

            for group_center, (minutes, breakdowns) in zip(group_centers, checkpoints):
                for lane_index, lane in enumerate(lanes):
                    for team_index, team_id in enumerate(teams):
                        breakdown = breakdowns.get(team_id)
                        value = breakdown[lane]["score"] if breakdown else 0.0
                        x = group_center - group_span / 2 + slots[lane_index] + team_index * bar_width + bar_width / 2
                        bars = axes.bar(
                            [x], [value], width=bar_width * 0.94,
                            color=team_colors[team_id],
                            edgecolor=_BACKGROUND, linewidth=0.8, zorder=3,
                        )
                        axes.bar_label(
                            bars, labels=[f"{value:.0f}"], padding=2,
                            color=_TEXT, fontsize=8, fontweight="medium",
                        )
                    lane_center = group_center - group_span / 2 + slots[lane_index] + bar_width
                    axes.text(
                        lane_center, -4, lane_labels.get(lane, lane), ha="center", va="top",
                        color=_TEXT, fontsize=9.5,
                    )
                axes.text(
                    group_center, -13, f"{minutes}m", ha="center", va="top",
                    color=_TEXT, fontsize=12, fontweight="bold",
                )

            axes.set_xlim(group_centers[0] - group_span / 2 - 0.25, group_centers[-1] + group_span / 2 + 0.25)
            axes.set_xticks([])
            axes.set_ylim(0, 108)
            axes.tick_params(axis="y", colors=_MUTED, labelsize=9, length=0)
            axes.grid(axis="y", color=_GRID, linewidth=0.8, alpha=0.5, zorder=0)
            axes.set_axisbelow(True)
            for side in ("top", "right", "left"):
                axes.spines[side].set_visible(False)
            axes.spines["bottom"].set_visible(False)
            for group_center in group_centers[:-1]:
                axes.axvline(
                    group_center + group_span / 2 + 0.25, color=_GRID,
                    linewidth=0.8, alpha=0.5, zorder=1,
                )

            figure.suptitle(
                "Jungle Proximity by Checkpoint (%)",
                color=_TEXT, fontsize=15, fontweight="bold", y=0.99,
            )
            legend_handles = [
                plt.Rectangle((0, 0), 1, 1, facecolor=team_colors[team_id], edgecolor="none", label=team_labels[team_id])
                for team_id in teams
            ]
            figure.legend(
                handles=legend_handles, loc="upper center", ncol=2,
                bbox_to_anchor=(0.5, 0.93), frameon=False,
                fontsize=9.5, labelcolor=_TEXT, handlelength=1.3, handleheight=1.3,
                columnspacing=1.6,
            )
            figure.tight_layout(pad=1.4, rect=(0, 0.05, 1, 0.86))

            buffer = io.BytesIO()
            figure.savefig(buffer, format="png", facecolor=figure.get_facecolor())
        finally:
            plt.close(figure)
        buffer.seek(0)
        return discord.File(buffer, filename=filename)


_LANING_SIDES = ("you", "opponent")
_LANING_SIDE_LABELS = {"you": "You", "opponent": "Opponent"}


def build_laning_comparison_chart(
    checkpoints: Sequence[tuple[int, dict[str, dict[str, float] | None]]],
    *, filename: str = "laning.png",
) -> discord.File | None:
    """Grouped bar chart comparing a player's Gold/XP against their lane opponent by checkpoint.

    `checkpoints` is a sequence of (minutes, stats) pairs, where stats maps
    "you"/"opponent" to that side's raw ``MatchTimeline.stats_at`` result
    (``{"Gold": ..., "XP": ...}``), or None if that minute never happened.
    Checkpoints sit side by side on one axes, each with a Gold/XP bar pair
    colored by side (you vs opponent) only — the same shape as
    :func:`build_jungle_proximity_comparison_chart`, with metrics standing
    in for lanes and sides standing in for teams.
    """
    if not MATPLOTLIB_AVAILABLE or not checkpoints:
        LOGGER.debug("Skipping laning comparison chart: matplotlib=%s checkpoints=%d", MATPLOTLIB_AVAILABLE, len(checkpoints))
        return None
    LOGGER.debug("Building laning comparison chart: checkpoints=%d", len(checkpoints))

    metrics = ("Gold", "XP")
    side_colors = {"you": _TEAM_BLUE, "opponent": _TEAM_RED}
    bar_width = 0.34
    metric_gap = 0.18
    slots = [metric_index * (2 * bar_width + metric_gap) for metric_index in range(len(metrics))]
    group_span = slots[-1] + 2 * bar_width
    group_centers = [
        group_index * (group_span + 0.5)
        for group_index in range(len(checkpoints))
    ]

    all_values = [
        stats[metric]
        for _, sides in checkpoints
        for stats in sides.values() if stats is not None
        for metric in metrics
    ]
    ceiling = max(all_values, default=0) or 1

    with _chart_lock:
        figure, axes = plt.subplots(figsize=_FIGURE_SIZE, dpi=_FIGURE_DPI)
        try:
            figure.patch.set_facecolor(_BACKGROUND)
            axes.set_facecolor(_BACKGROUND)

            for group_center, (minutes, sides) in zip(group_centers, checkpoints):
                for metric_index, metric in enumerate(metrics):
                    for side_index, side in enumerate(_LANING_SIDES):
                        stats = sides.get(side)
                        value = stats[metric] if stats else 0.0
                        x = group_center - group_span / 2 + slots[metric_index] + side_index * bar_width + bar_width / 2
                        bars = axes.bar(
                            [x], [value], width=bar_width * 0.94,
                            color=side_colors[side],
                            edgecolor=_BACKGROUND, linewidth=0.8, zorder=3,
                        )
                        axes.bar_label(
                            bars, labels=[f"{value:,.0f}"], padding=2,
                            color=_TEXT, fontsize=8, fontweight="medium",
                        )
                    metric_center = group_center - group_span / 2 + slots[metric_index] + bar_width
                    axes.text(
                        metric_center, -ceiling * 0.05, metric, ha="center", va="top",
                        color=_TEXT, fontsize=9.5,
                    )
                axes.text(
                    group_center, -ceiling * 0.14, f"{minutes}m", ha="center", va="top",
                    color=_TEXT, fontsize=12, fontweight="bold",
                )

            axes.set_xlim(group_centers[0] - group_span / 2 - 0.25, group_centers[-1] + group_span / 2 + 0.25)
            axes.set_xticks([])
            axes.set_ylim(0, ceiling * 1.15)
            axes.tick_params(axis="y", colors=_MUTED, labelsize=9, length=0)
            axes.grid(axis="y", color=_GRID, linewidth=0.8, alpha=0.5, zorder=0)
            axes.set_axisbelow(True)
            for side in ("top", "right", "left"):
                axes.spines[side].set_visible(False)
            axes.spines["bottom"].set_visible(False)
            for group_center in group_centers[:-1]:
                axes.axvline(
                    group_center + group_span / 2 + 0.25, color=_GRID,
                    linewidth=0.8, alpha=0.5, zorder=1,
                )

            figure.suptitle(
                "Laning Comparison by Checkpoint",
                color=_TEXT, fontsize=15, fontweight="bold", y=0.99,
            )
            legend_handles = [
                plt.Rectangle((0, 0), 1, 1, facecolor=side_colors[side], edgecolor="none", label=_LANING_SIDE_LABELS[side])
                for side in _LANING_SIDES
            ]
            figure.legend(
                handles=legend_handles, loc="upper center", ncol=2,
                bbox_to_anchor=(0.5, 0.93), frameon=False,
                fontsize=9.5, labelcolor=_TEXT, handlelength=1.3, handleheight=1.3,
                columnspacing=1.6,
            )
            figure.tight_layout(pad=1.4, rect=(0, 0.05, 1, 0.86))

            buffer = io.BytesIO()
            figure.savefig(buffer, format="png", facecolor=figure.get_facecolor())
        finally:
            plt.close(figure)
        buffer.seek(0)
        return discord.File(buffer, filename=filename)


def build_kill_map(
    match: dict[str, Any], timeline: MatchTimeline, participant: dict[str, Any]
) -> discord.File | None:
    """Build kill map."""
    LOGGER.debug("Building kill map")
    with _chart_lock:
        return _build_kill_map(match, timeline, participant)


def build_lp_chart(
    entries: Sequence[dict[str, Any]], *, days: int = 30
) -> discord.File | None:
    """Render a clean annotated line plot of persisted rank values.

    The underlying continuous values are retained so promotions remain plotted
    correctly. Each point is annotated with a compact rank label and its LP,
    such as ``G 1`` over ``29LP``.
    """
    if not MATPLOTLIB_AVAILABLE:
        return None
    cutoff = time.time() - max(days, 1) * 86400
    points = [
        entry
        for entry in entries
        if entry.get("v") is not None and entry.get("t", 0) >= cutoff
    ]
    if not points:
        LOGGER.debug("Skipping LP chart: no points within %d days", days)
        return None
    LOGGER.debug("Building LP chart: points=%d days=%d", len(points), days)
    with _chart_lock:
        figure, axes = plt.subplots(figsize=(10, 3.2), dpi=_FIGURE_DPI)
        try:
            figure.patch.set_facecolor(_BACKGROUND)
            axes.set_facecolor(_BACKGROUND)
            x_values = [datetime.fromtimestamp(entry["t"]) for entry in points]
            y_values = [entry["v"] for entry in points]
            line_color = "#00b8ad"
            axes.plot(x_values, y_values, color=line_color, linewidth=2.2, zorder=2)
            axes.scatter(x_values, y_values, color=line_color, s=42, zorder=3)
            axes.margins(x=0.02, y=0.28)
            axes.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d"))
            axes.get_yaxis().set_visible(False)
            axes.tick_params(axis="x", colors=_MUTED, labelsize=10, length=0, pad=8)
            axes.grid(False)
            for side in ("top", "right", "left"):
                axes.spines[side].set_visible(False)
            axes.spines["bottom"].set_color("#17181d")
            axes.spines["bottom"].set_linewidth(1.2)
            for x_value, y_value in zip(x_values, y_values):
                rank_label, lp_label = _lp_point_labels(y_value)
                axes.annotate(
                    rank_label, (x_value, y_value), xytext=(0, 13),
                    textcoords="offset points", ha="center", va="bottom",
                    color=_MUTED, fontsize=10, fontweight="bold",
                )
                axes.annotate(
                    lp_label, (x_value, y_value), xytext=(0, 2),
                    textcoords="offset points", ha="center", va="bottom",
                    color=_MUTED, fontsize=10,
                )
            figure.tight_layout(pad=0.7)
            buffer = io.BytesIO()
            figure.savefig(buffer, format="png", facecolor=figure.get_facecolor())
        finally:
            plt.close(figure)
    buffer.seek(0)
    return discord.File(buffer, filename=LP_CHART_FILENAME)


_TIER_ABBREVIATIONS = {
    "Iron": "I",
    "Bronze": "B",
    "Silver": "S",
    "Gold": "G",
    "Plat": "P",
    "Emerald": "E",
    "Diamond": "D",
    "Master": "M",
    "Grandmaster": "GM",
    "Challenger": "C",
}
_DIVISION_NUMBERS = {"IV": "4", "III": "3", "II": "2", "I": "1"}


def _lp_point_labels(value: float) -> tuple[str, str]:
    """Return the compact rank and LP labels shown above an LP chart point."""
    rank = value_to_rank(value)
    if rank is None:
        return "", ""
    tier, division, lp = rank
    tier_label = TIER_LABELS.get(tier, tier.title())
    tier_abbreviation = _TIER_ABBREVIATIONS.get(tier_label, tier_label[:1].upper())
    division_number = _DIVISION_NUMBERS.get(division, "")
    return f"{tier_abbreviation} {division_number}".strip(), f"{lp}LP"

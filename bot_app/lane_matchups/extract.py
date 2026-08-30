"""Turn one completed match + its timeline into laning-phase counter rows.

Pure and I/O-free, per ``PLAN.md`` §2-3 and §9: :func:`lane_outcomes` takes
already-fetched payloads and returns plain data, so it can be unit tested
against fixtures without a network call and reused unchanged by both the
offline statistics writer (:mod:`bot_app.match_cache`) and any future
per-player "your worst matchups" feature that needs no global corpus at all.

Built on :func:`bot_app.timeline.opponent_participant_id` and
:meth:`bot_app.timeline.MatchTimeline.lane_diff_at`, exactly as the plan
specifies. Nothing here computes the composite z-scored lane score — that
needs a population of rows to standardise against (§2.3), which a single
match does not have. This module hands back the raw components; the caller
(the statistics writer) accumulates and standardises them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..queues import SOLO_QUEUE_ID
from ..timeline import MatchTimeline, max_level_for_position, opponent_participant_id

LANE_WINDOW_MS = 14 * 60_000

MIN_GAME_DURATION_S = 15 * 60

LANE_ROLES = ("TOP", "MIDDLE", "BOTTOM", "UTILITY")
"""Roles v1 rates. JUNGLE is deliberately excluded — see PLAN.md §2.5."""

_BOT_PAIR_ROLES = ("BOTTOM", "UTILITY")


@dataclass(frozen=True)
class LaneOutcome:
    """One (match, role) row: the raw components of the §2.3 lane score.

    ``gold_diff``/``xp_diff`` are this champion's advantage over its lane
    opponent at 14:00 for TOP/MIDDLE/BOTTOM. For UTILITY they are instead the
    **bot-lane pair's** combined advantage (ADC + support vs. the enemy ADC +
    support) — a lane-*pair* score, not a solo one, per PLAN.md §2.5. Callers
    must not mix UTILITY rows into a TOP/MIDDLE/BOTTOM population without
    accounting for that difference.

    ``cs_diff`` is None for UTILITY: CS is near-meaningless for a support's
    lane result. ``solo_kill_diff`` is the pair's solo-kill margin before
    14:00 for the direct roles; for UTILITY it is instead the pair's kill
    *involvement* margin (kills + assists, either side of the lane) in the
    bottom half of the map before 14:00, used as the kill-participation
    covariate PLAN.md §2.5 calls for.
    """

    patch: str
    platform: str
    position: str
    champion_id: int
    opponent_id: int
    team_id: int
    gold_diff: float
    xp_diff: float
    cs_diff: float | None
    solo_kill_diff: float
    is_pair_metric: bool = False


def patch_from_version(game_version: str | None) -> str | None:
    """``"14.16.567.1234"`` -> ``"14.16"``.

    Shared with :mod:`bot_app.rating`, which keys its population baselines
    on the same patch string so both corpora age out on one window.
    """
    if not game_version:
        return None
    parts = game_version.split(".")
    if len(parts) < 2:
        return None
    return f"{parts[0]}.{parts[1]}"


def _valid_role_assignment(participants: list[dict[str, Any]]) -> bool:
    """Both teams have five distinct, non-empty ``teamPosition`` values."""
    for team_id in (100, 200):
        positions = [
            p.get("teamPosition")
            for p in participants
            if p.get("teamId") == team_id
        ]
        if len(positions) != 5 or not all(positions) or len(set(positions)) != 5:
            return False
    return True


def _is_remake(participants: list[dict[str, Any]]) -> bool:
    """Handle remake."""
    return any(p.get("gameEndedInEarlySurrender") for p in participants)


def _solo_kill_diff(
    events, mine: int, opponent: int, window_ms: int
) -> float:
    """Solo-kill margin (my solo kills minus theirs) between exactly this pair.

    "Solo" here means the ``CHAMPION_KILL`` event carries no assists, matching
    PLAN.md §2.3's "solo kills inside the window, filtered to the pair" —
    :meth:`MatchTimeline.champion_kills` already exposes ``assist_count`` for
    this without needing the module's private full-match solo-kill
    classifier, which additionally checks for nearby third parties and is not
    pair-scoped. ``events`` is the match's pre-fetched
    :meth:`MatchTimeline.champion_kills` sequence, shared across every row
    this match produces so it is only computed once per match, not once per
    participant.
    """
    pair = {mine, opponent}
    my_kills = 0
    their_kills = 0
    for event in events:
        if event.timestamp >= window_ms or event.assist_count:
            continue
        if event.killer_id not in pair or event.victim_id not in pair:
            continue
        if event.killer_id == mine:
            my_kills += 1
        elif event.killer_id == opponent:
            their_kills += 1
    return float(my_kills - their_kills)


def _is_bottom_half(x: float, y: float) -> bool:
    """Whether a map coordinate sits in the bottom lane's half of the map.

    Summoner's Rift is roughly diagonally symmetric: top lane runs along the
    upper-left, bottom lane along the lower-right, so ``x > y`` is a cheap
    proxy for "on the bottom side of the map" without needing the full route
    geometry :mod:`bot_app.charts` uses for jungle proximity.
    """
    return x > y


def _bottom_half_kill_involvement(
    events,
    pair: frozenset[int],
    enemy_pair: frozenset[int],
    window_ms: int,
) -> float:
    """Kills/assists the pair was involved in, in the bottom half, before ``window_ms``.

    Used as the UTILITY role's kill-participation covariate (PLAN.md §2.5),
    since gold/CS diffs are near-meaningless for a support's own lane result.
    ``events`` is the match's pre-fetched champion-kill sequence, shared with
    :func:`_solo_kill_diff` so it is computed once per match.
    """
    mine = 0
    theirs = 0
    for event in events:
        if event.timestamp >= window_ms:
            continue
        if not _is_bottom_half(event.x, event.y):
            continue
        killer_in_pair = event.killer_id in pair
        killer_in_enemy = event.killer_id in enemy_pair
        victim_in_pair = event.victim_id in pair
        victim_in_enemy = event.victim_id in enemy_pair
        if killer_in_pair and victim_in_enemy:
            mine += 1
        elif killer_in_enemy and victim_in_pair:
            theirs += 1
    return float(mine - theirs)


def _direct_role_outcome(
    match: dict[str, Any],
    timeline: MatchTimeline,
    participant: dict[str, Any],
    patch: str,
    platform: str,
    kill_events,
    participants_by_id: dict[int, dict[str, Any]],
) -> LaneOutcome | None:
    """A TOP/MIDDLE/BOTTOM row for one participant."""
    position = participant.get("teamPosition")
    participant_id = participant.get("participantId")
    opponent_id = opponent_participant_id(match, participant)
    if opponent_id is None:
        return None

    diff = timeline.lane_diff_at(
        participant_id, opponent_id, LANE_WINDOW_MS, max_level_for_position(position)
    )
    if diff is None:
        return None

    opponent = participants_by_id.get(opponent_id)
    if opponent is None:
        return None

    return LaneOutcome(
        patch=patch,
        platform=platform,
        position=position,
        champion_id=participant.get("championId"),
        opponent_id=opponent.get("championId"),
        team_id=participant.get("teamId"),
        gold_diff=float(diff.gold),
        xp_diff=float(diff.xp),
        cs_diff=float(diff.cs),
        solo_kill_diff=_solo_kill_diff(
            kill_events, participant_id, opponent_id, LANE_WINDOW_MS
        ),
    )


def _bot_pair(
    participants: list[dict[str, Any]], team_id: int
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """This team's (ADC, support) participants, or (None, None) if incomplete."""
    adc = next(
        (
            p
            for p in participants
            if p.get("teamId") == team_id and p.get("teamPosition") == "BOTTOM"
        ),
        None,
    )
    support = next(
        (
            p
            for p in participants
            if p.get("teamId") == team_id and p.get("teamPosition") == "UTILITY"
        ),
        None,
    )
    return adc, support


def _utility_outcome(
    match: dict[str, Any],
    timeline: MatchTimeline,
    support: dict[str, Any],
    patch: str,
    platform: str,
    kill_events,
) -> LaneOutcome | None:
    """A UTILITY row using the bot-lane *pair's* gold/xp diff (PLAN.md §2.5)."""
    participants = match.get("info", {}).get("participants", []) or []
    team_id = support.get("teamId")
    enemy_team_id = 200 if team_id == 100 else 100

    adc, my_support = _bot_pair(participants, team_id)
    enemy_adc, enemy_support = _bot_pair(participants, enemy_team_id)
    if not all((adc, my_support, enemy_adc, enemy_support)):
        return None

    adc_diff = timeline.lane_diff_at(
        adc.get("participantId"), enemy_adc.get("participantId"), LANE_WINDOW_MS
    )
    support_diff = timeline.lane_diff_at(
        my_support.get("participantId"),
        enemy_support.get("participantId"),
        LANE_WINDOW_MS,
    )
    if adc_diff is None or support_diff is None:
        return None

    pair = frozenset({adc.get("participantId"), my_support.get("participantId")})
    enemy_pair = frozenset(
        {enemy_adc.get("participantId"), enemy_support.get("participantId")}
    )
    kill_participation_diff = _bottom_half_kill_involvement(
        kill_events, pair, enemy_pair, LANE_WINDOW_MS
    )

    return LaneOutcome(
        patch=patch,
        platform=platform,
        position="UTILITY",
        champion_id=my_support.get("championId"),
        opponent_id=enemy_support.get("championId"),
        team_id=team_id,
        gold_diff=float(adc_diff.gold + support_diff.gold),
        xp_diff=float(adc_diff.xp + support_diff.xp),
        cs_diff=None,
        solo_kill_diff=kill_participation_diff,
        is_pair_metric=True,
    )


def lane_outcomes(
    match: dict[str, Any], timeline_payload: dict[str, Any] | None
) -> list[LaneOutcome]:
    """One row per (match, role) for TOP/MIDDLE/BOTTOM/UTILITY, per PLAN.md §2-3.

    Returns an empty list unless the match passes every §3 sample-construction
    filter: ranked solo queue, no early-surrender remake, a game long enough
    for the 14-minute frame to exist and not be the final frame of a collapse,
    valid five-distinct-role assignment on both teams, and frame data at the
    14-minute mark for both sides of every row it produces.
    """
    info = match.get("info", {}) or {}
    if info.get("queueId") != SOLO_QUEUE_ID:
        return []
    if (info.get("gameDuration") or 0) < MIN_GAME_DURATION_S:
        return []

    participants = info.get("participants", []) or []
    if len(participants) != 10:
        return []
    if _is_remake(participants):
        return []
    if not _valid_role_assignment(participants):
        return []
    if timeline_payload is None:
        return []

    patch = patch_from_version(info.get("gameVersion"))
    platform = info.get("platformId") or ""
    if not patch:
        return []

    timeline = MatchTimeline(dict(timeline_payload))
    # Computed once and shared across every row this match produces, since
    # each of the direct-role and utility outcome builders would otherwise
    # re-scan the full champion-kill event list per participant.
    kill_events = list(timeline.champion_kills())
    participants_by_id = {
        p.get("participantId"): p
        for p in participants
        if p.get("participantId") is not None
    }

    outcomes: list[LaneOutcome] = []
    for participant in participants:
        position = participant.get("teamPosition")
        if position not in ("TOP", "MIDDLE"):
            continue
        outcome = _direct_role_outcome(
            match, timeline, participant, patch, platform, kill_events, participants_by_id
        )
        if outcome is not None:
            outcomes.append(outcome)

    # BOTTOM uses the direct metric too (PLAN.md §2.5: accept the extra noise
    # from support pairing in v1 rather than modelling it as a covariate).
    for participant in participants:
        if participant.get("teamPosition") != "BOTTOM":
            continue
        outcome = _direct_role_outcome(
            match, timeline, participant, patch, platform, kill_events, participants_by_id
        )
        if outcome is not None:
            outcomes.append(outcome)

    for participant in participants:
        if participant.get("teamPosition") != "UTILITY":
            continue
        outcome = _utility_outcome(match, timeline, participant, patch, platform, kill_events)
        if outcome is not None:
            outcomes.append(outcome)

    return outcomes

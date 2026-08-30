"""Per-player performance rating for a finished match.

Implements the model in ``rating.md``: version one, standard five-player
Summoner's Rift only, built only from fields the Match-V5 match and timeline
payloads actually carry — see that document's §2 for what is and is not
reliably measurable from them. Nothing here claims to know ward quality,
support-quest completion time, jungle *camps* (as opposed to jungle
monsters), true peel or engage quality, exact teamfight presence, whether a
death caused a later objective loss, or split-push pressure. Where the old
design's first draft invented proxies for those (an isolated/traded/
objective-enabling death classifier, a kill-credit 55/45 split, a "pick your
best utility route" scorer), this implementation deliberately does not.

Normalization is corpus-relative, per §7:

1. Gold/XP/CS checkpoint metrics are first expressed as a *signed difference*
   against the direct role opponent (the same position on the other team),
   and the fifteen-minute figure is kept as the *swing* since ten rather
   than a second, near-duplicate level.
2. Every metric — including those signed diffs — is then standardized
   against a stored population mean and standard deviation **for that
   player's own role**, supplied by :mod:`bot_app.rating_baselines`.

Version one standardized within the lobby instead, which had three
consequences it was never meant to have: a lobby's mean score was pinned to
5.0 by construction, scores meant nothing across matches, and any metric
applying to only two players (jungle CS diff, every bot-lane duo diff)
collapsed to exactly ``±1`` regardless of magnitude. Lobby z-scoring remains
as the per-metric fallback for anything the corpus has not yet seen enough
of, so a cold database still produces ratings.

Because a corpus pools matches of every length, every accumulating total
(healing, crowd control, bounties, steals) is expressed per minute; a raw
total would make game duration read as performance.

Win/loss is never an input. Almost every statistic already correlates with
winning; feeding the result back in would turn this into a win detector
wearing a rating's clothes. Win/loss is used only afterward, to label the
best score on the winning side ``MVP`` and the best score on the losing side
``ACE``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .lane_matchups.extract import patch_from_version
from .queues import ARENA_QUEUE_IDS
from .rating_baselines import BaselineTable, default_baselines
from .timeline import (
    MatchTimeline,
    max_level_for_position,
    opponent_participant_id,
)

LOGGER = logging.getLogger(__name__)


ARAM_QUEUE_IDS = frozenset({450, 720, 930})


SR_POSITIONS = frozenset({"TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY"})


MIN_RATED_DURATION_S = 300


MARK_10_MIN_MS = 10 * 60_000
MARK_15_MIN_MS = 15 * 60_000


Z_CLIP = 2.5


SCORE_CENTER = 5.0

SCORE_SPREAD = 3.4
"""Points of score per unit of composite, chosen so scores actually spread.

The composite is a weighted mean of clipped z-scores, and averaging several
imperfectly correlated z-scores shrinks the result: measured over the cached
corpus its standard deviation is about 0.44, not 1.0. The old spread of 2.0
therefore produced scores with a standard deviation of only 0.9 — 82% of
every rating landed on C or D and S+ was mathematically unreachable. 3.4
puts the score's standard deviation at roughly 1.5 points, which is what
:data:`GRADE_THRESHOLDS` is cut against.

Re-derive this (and the thresholds) whenever the metric set changes: rate
the cached corpus, take the population standard deviation of
:attr:`PlayerRating.composite`, and set this to ``1.5 / that``.
"""


PENALTY_BUCKETS = frozenset({"discipline"})


# ---------------------------------------------------------------------------
# Bucket 1: Combat
# ---------------------------------------------------------------------------

COMBAT_METRICS: dict[str, float] = {
    "damage_efficiency": 0.30,
    "dpm": 0.20,
    "kill_participation": 0.20,
    "bounty_earned_pm": 0.15,
    "solo_kills_pm": 0.09,
    "multikills_pm": 0.06,
}


# ---------------------------------------------------------------------------
# Bucket 2: Economy — one table per role family, per rating.md §6
# ---------------------------------------------------------------------------

ECONOMY_METRICS: dict[str, dict[str, float]] = {
    "lane": {
        "gold_diff_10": 0.20,
        "gold_swing_15": 0.20,
        "xp_diff_10": 0.10,
        "xp_swing_15": 0.10,
        "cs_diff_10": 0.10,
        "cs_swing_15": 0.10,
        "gpm": 0.12,
        "cs_per_min": 0.08,
    },
    "jungle": {
        "gold_diff_10": 0.20,
        "gold_swing_15": 0.20,
        "xp_diff_10": 0.10,
        "xp_swing_15": 0.10,
        "jungle_cs_diff_10": 0.12,
        "jungle_cs_swing_15": 0.12,
        "gpm": 0.10,
        "enemy_jungle_monsters_pm": 0.06,
    },
    "utility": {
        "duo_gold_diff_10": 0.25,
        "duo_gold_swing_15": 0.25,
        "duo_xp_diff_10": 0.15,
        "duo_xp_swing_15": 0.15,
        "support_gold_diff_15": 0.10,
        "support_gpm": 0.10,
    },
}


def _economy_family(role: str) -> str:
    """Handle family."""
    if role == "JUNGLE":
        return "jungle"
    if role == "UTILITY":
        return "utility"
    return "lane"


# ---------------------------------------------------------------------------
# Bucket 3: Objectives
# ---------------------------------------------------------------------------

OBJECTIVE_METRICS: dict[str, float] = {
    "epic_participation": 0.30,
    "building_participation": 0.25,
    "obj_damage_pm": 0.20,
    "turret_damage_pm": 0.15,
    "steals_pm": 0.10,
}


# ---------------------------------------------------------------------------
# Bucket 4: Vision
# ---------------------------------------------------------------------------

VISION_METRICS: dict[str, float] = {
    "vspm": 0.45,
    "control_wards_pm": 0.25,
    "ward_takedowns_pm": 0.20,
    "wards_placed_pm": 0.10,
}


# ---------------------------------------------------------------------------
# Bucket 5: Utility
# ---------------------------------------------------------------------------

UTILITY_METRICS: dict[str, float] = {
    "heal_shield_pm": 0.35,
    "cc_time_pm": 0.25,
    "immobilizations_pm": 0.15,
    "self_mitigated_pm": 0.15,
    "damage_taken_share": 0.10,
}


# ---------------------------------------------------------------------------
# Bucket 6: Discipline (a penalty bucket: higher raw value is worse)
# ---------------------------------------------------------------------------

DISCIPLINE_METRICS: dict[str, float] = {
    "bounty_given_pm": 0.45,
    "time_dead_pct": 0.30,
    "death_share": 0.25,
}


BUCKET_METRICS: dict[str, dict[str, float]] = {
    "combat": COMBAT_METRICS,
    "economy": ECONOMY_METRICS["lane"],
    "objectives": OBJECTIVE_METRICS,
    "vision": VISION_METRICS,
    "utility": UTILITY_METRICS,
    "discipline": DISCIPLINE_METRICS,
}


BUCKET_NAMES: tuple[str, ...] = (
    "combat",
    "economy",
    "objectives",
    "vision",
    "utility",
    "discipline",
)


# ---------------------------------------------------------------------------
# Bucket weights per role, per rating.md §5
# ---------------------------------------------------------------------------

ROLE_WEIGHTS: dict[str, dict[str, float]] = {
    "TOP": {
        "combat": 0.27,
        "economy": 0.20,
        "objectives": 0.18,
        "vision": 0.05,
        "utility": 0.15,
        "discipline": 0.15,
    },
    "JUNGLE": {
        "combat": 0.22,
        "economy": 0.13,
        "objectives": 0.23,
        "vision": 0.10,
        "utility": 0.17,
        "discipline": 0.15,
    },
    "MIDDLE": {
        "combat": 0.29,
        "economy": 0.20,
        "objectives": 0.14,
        "vision": 0.07,
        "utility": 0.15,
        "discipline": 0.15,
    },
    "BOTTOM": {
        "combat": 0.34,
        "economy": 0.22,
        "objectives": 0.14,
        "vision": 0.08,
        "utility": 0.07,
        "discipline": 0.15,
    },
    "UTILITY": {
        "combat": 0.14,
        "economy": 0.08,
        "objectives": 0.14,
        "vision": 0.24,
        "utility": 0.25,
        "discipline": 0.15,
    },
}


GRADE_THRESHOLDS: tuple[tuple[float, str], ...] = (
    (8.5, "S+"),
    (7.5, "S"),
    (6.5, "A"),
    (5.5, "B"),
    (4.0, "C"),
    (2.75, "D"),
    (0.0, "F"),
)
"""Score cut points, set from the observed distribution rather than guessed.

Over the cached corpus these yield roughly S+ 2%, S 4%, A 9%, B 20%, C 39%,
D 21%, F 6% — a curve where every grade means something and the top ones are
rare without being unreachable. The previous thresholds were written as if
the composite were a full-width z-score and produced S+ 0%, S 0.1%, C+D 82%.
"""


@dataclass(frozen=True)
class RatingBuckets:
    """The six bucket scores behind one rating, in normalised units."""

    combat: float
    economy: float
    objectives: float
    vision: float
    utility: float
    discipline: float

    def items(self) -> list[tuple[str, float]]:
        """Handle items."""
        return [
            ("Combat", self.combat),
            ("Economy", self.economy),
            ("Objectives", self.objectives),
            ("Vision", self.vision),
            ("Utility", self.utility),
            ("Discipline", self.discipline),
        ]


@dataclass(frozen=True)
class PlayerRating:
    """One player's score, and enough of the arithmetic to explain it."""

    puuid: str
    participant_id: int
    champion: str
    role: str
    team_id: int
    score: float
    grade: str
    composite: float
    buckets: RatingBuckets
    confidence: str = "Full"
    label: str | None = None
    notes: tuple[str, ...] = ()

    def display_buckets(self) -> list[tuple[str, float]]:
        """Bucket z-scores, in the fixed presentation order."""
        return self.buckets.items()


def grade_for(score: float) -> str:
    """Letter grade for a 0-10 score."""
    for threshold, grade in GRADE_THRESHOLDS:
        if score >= threshold:
            return grade
    return "F"


def _clip(value: float, limit: float = Z_CLIP) -> float:
    """Handle clip."""
    return max(-limit, min(limit, value))


def _ratio(numerator: float, denominator: float) -> float:
    """Handle ratio."""
    return numerator / denominator if denominator else 0.0


def queue_profile(queue_id: int | None) -> str:
    """Which weight family a queue belongs to: ``sr``, ``aram`` or ``arena``.

    Only ``sr`` is scored — see the module docstring. ARAM and Arena are
    still classified explicitly rather than falling through to "sr" and
    being scored on a role table they cannot satisfy.
    """
    if queue_id in ARENA_QUEUE_IDS:
        return "arena"
    if queue_id in ARAM_QUEUE_IDS:
        return "aram"
    return "sr"


def _mirrored_roles(participants: Sequence[Mapping[str, Any]]) -> bool:
    """Whether both teams field exactly one of each Rift position.

    Version one requires this — see rating.md §3 — because the economy
    bucket's checkpoint metrics compare a player against their positional
    mirror; a jungler with no jungler to compare against has nothing to be
    role-relative to.
    """
    by_team: dict[int, set[str]] = {}
    for participant in participants:
        team_id = participant.get("teamId")
        position = participant.get("teamPosition")
        if team_id is None or position not in SR_POSITIONS:
            return False
        by_team.setdefault(team_id, set()).add(position)
    return len(by_team) == 2 and all(
        positions == SR_POSITIONS for positions in by_team.values()
    )


def participant_at_id(
    match: Mapping[str, Any], participant_id: int | None
) -> dict[str, Any] | None:
    """The participant dict for one ``participantId``, or None."""
    if participant_id is None:
        return None
    for participant in match.get("info", {}).get("participants", []) or []:
        if participant.get("participantId") == participant_id:
            return participant
    return None


def _teammate_by_position(
    match: Mapping[str, Any], participant: Mapping[str, Any], position: str
) -> dict[str, Any] | None:
    """This player's teammate at ``position``, or None."""
    team_id = participant.get("teamId")
    for other in match.get("info", {}).get("participants", []) or []:
        if other.get("teamId") == team_id and other.get("teamPosition") == position:
            return other
    return None


def _multikills(participant: Mapping[str, Any]) -> float:
    """How many multikills this player got.

    Falls back to the box score's own tier counters rather than
    ``doubleKills`` alone, which is a different quantity: a player whose only
    multikill was a quadra would otherwise be recorded as having none.
    """
    challenges = participant.get("challenges") or {}
    if "multikills" in challenges:
        return float(challenges["multikills"])
    return float(
        participant.get("doubleKills", 0)
        + participant.get("tripleKills", 0)
        + participant.get("quadraKills", 0)
        + participant.get("pentaKills", 0)
    )


def _epic_takedowns(participant: Mapping[str, Any]) -> float:
    """Dragons, Barons, Heralds and void grubs this player got takedown credit for."""
    challenges = participant.get("challenges") or {}
    if challenges:
        return float(
            challenges.get("dragonTakedowns", 0)
            + challenges.get("baronTakedowns", 0)
            + challenges.get("riftHeraldTakedowns", 0)
            + challenges.get("voidMonsterKill", 0)
        )
    return float(participant.get("dragonKills", 0) + participant.get("baronKills", 0))


class _Lobby:
    """Per-match team totals every share metric is measured against."""

    def __init__(self, participants: Sequence[Mapping[str, Any]]) -> None:
        """Initialize the instance."""
        self.damage: dict[int, float] = {}
        self.damage_taken: dict[int, float] = {}
        self.gold: dict[int, float] = {}
        self.kills: dict[int, float] = {}
        self.deaths: dict[int, float] = {}
        for participant in participants:
            key = participant.get("teamId") or 0
            self.damage[key] = self.damage.get(key, 0.0) + participant.get(
                "totalDamageDealtToChampions", 0
            )
            self.damage_taken[key] = self.damage_taken.get(
                key, 0.0
            ) + participant.get("totalDamageTaken", 0)
            self.gold[key] = self.gold.get(key, 0.0) + participant.get("goldEarned", 0)
            self.kills[key] = self.kills.get(key, 0.0) + participant.get("kills", 0)
            self.deaths[key] = self.deaths.get(key, 0.0) + participant.get("deaths", 0)


def _lane_diff_component(
    timeline: MatchTimeline | None,
    participant_id: int | None,
    opponent_id: int | None,
    timestamp: int,
    max_level: int,
) -> tuple[float | None, float | None, float | None]:
    """``(gold, xp, cs)`` diffs at one mark, or all-``None`` if unavailable."""
    if timeline is None:
        return None, None, None
    diff = timeline.lane_diff_at(participant_id, opponent_id, timestamp, max_level)
    if diff is None:
        return None, None, None
    return float(diff.gold), float(diff.xp), float(diff.cs)


def _duo_diff(
    match: Mapping[str, Any],
    timeline: MatchTimeline | None,
    support: Mapping[str, Any],
    timestamp: int,
) -> tuple[float | None, float | None]:
    """Combined bot-lane ``(gold, xp)`` advantage: support's diff plus the ADC's."""
    if timeline is None:
        return None, None
    adc = _teammate_by_position(match, support, "BOTTOM")
    if adc is None:
        return None, None
    support_diff = timeline.lane_diff_at(
        support.get("participantId"), opponent_participant_id(match, support), timestamp
    )
    adc_diff = timeline.lane_diff_at(
        adc.get("participantId"), opponent_participant_id(match, adc), timestamp
    )
    if support_diff is None or adc_diff is None:
        return None, None
    return (
        float(support_diff.gold + adc_diff.gold),
        float(support_diff.xp + adc_diff.xp),
    )


def _metrics(
    participant: Mapping[str, Any],
    match: Mapping[str, Any],
    lobby: _Lobby,
    timeline: MatchTimeline | None,
    minutes: float,
) -> dict[str, float | None]:
    """Every raw metric for one player.

    ``None`` means *unavailable*, not zero — a match with no timeline has no
    bounty ledger, and scoring that as zero would rank the player last on it
    instead of leaving it out. :func:`_bucket_score` renormalises around the
    metrics that are present.
    """
    key = participant.get("teamId") or 0
    duration_s = max(match.get("info", {}).get("gameDuration", 0), 1)
    participant_id = participant.get("participantId")
    role = (participant.get("teamPosition") or "").upper()
    max_level = max_level_for_position(role)

    challenges = participant.get("challenges") or {}
    damage = float(participant.get("totalDamageDealtToChampions", 0))
    deaths = float(participant.get("deaths", 0))

    ledger = timeline.bounty_ledger(participant_id) if timeline else None
    opponent_id = opponent_participant_id(match, participant)

    team_damage_share = challenges.get(
        "teamDamagePercentage", _ratio(damage, lobby.damage.get(key, 0.0))
    )
    team_gold_share = _ratio(
        float(participant.get("goldEarned", 0)), lobby.gold.get(key, 0.0)
    )

    metrics: dict[str, float | None] = {
        # combat
        # damage efficiency = team damage share − team gold share, per §6:
        # rewards converting resources into output rather than just being fed.
        "damage_efficiency": team_damage_share - team_gold_share,
        "dpm": challenges.get("damagePerMinute", _ratio(damage, minutes)),
        "kill_participation": challenges.get(
            "killParticipation",
            _ratio(
                participant.get("kills", 0) + participant.get("assists", 0),
                lobby.kills.get(key, 0.0),
            ),
        ),
        "bounty_earned_pm": (
            _ratio(ledger.earned, minutes) if ledger is not None else None
        ),
        # Kept as two metrics rather than one sum: a single outnumbered solo
        # double kill satisfies all three of Riot's counters at once, so
        # adding them counted one takedown up to three times. Solo kills and
        # multikills describe genuinely different play, so they are weighted
        # separately instead.
        "solo_kills_pm": _ratio(
            challenges.get("soloKills", 0) + challenges.get("outnumberedKills", 0),
            minutes,
        ),
        "multikills_pm": _ratio(_multikills(participant), minutes),
        # objectives
        "epic_participation": (
            float(timeline.epic_event_participation(participant_id))
            if timeline is not None
            else None
        ),
        "building_participation": (
            float(timeline.building_event_participation(participant_id))
            if timeline is not None
            else None
        ),
        "obj_damage_pm": _ratio(
            participant.get("damageDealtToObjectives", 0), minutes
        ),
        "turret_damage_pm": _ratio(participant.get("damageDealtToTurrets", 0), minutes),
        "steals_pm": _ratio(
            challenges.get("epicMonsterSteals", participant.get("objectivesStolen", 0)),
            minutes,
        ),
        # vision
        "vspm": challenges.get(
            "visionScorePerMinute", _ratio(participant.get("visionScore", 0), minutes)
        ),
        "control_wards_pm": _ratio(
            challenges.get(
                "controlWardsPlaced", participant.get("detectorWardsPlaced", 0)
            ),
            minutes,
        ),
        "ward_takedowns_pm": _ratio(
            challenges.get("wardTakedowns", participant.get("wardsKilled", 0)), minutes
        ),
        "wards_placed_pm": _ratio(participant.get("wardsPlaced", 0), minutes),
        # utility
        "heal_shield_pm": _ratio(
            challenges.get(
                "effectiveHealAndShielding",
                participant.get("totalHealsOnTeammates", 0)
                + participant.get("totalDamageShieldedOnTeammates", 0),
            ),
            minutes,
        ),
        "cc_time_pm": _ratio(participant.get("timeCCingOthers", 0), minutes),
        "immobilizations_pm": _ratio(
            challenges.get("enemyChampionImmobilizations", 0), minutes
        ),
        "self_mitigated_pm": _ratio(
            participant.get("damageSelfMitigated", 0), minutes
        ),
        "damage_taken_share": challenges.get(
            "damageTakenOnTeamPercentage",
            _ratio(
                participant.get("totalDamageTaken", 0),
                lobby.damage_taken.get(key, 0.0),
            ),
        ),
        # discipline (higher raw value is worse; sign-flipped after z-scoring)
        "bounty_given_pm": (
            _ratio(ledger.given, minutes) if ledger is not None else None
        ),
        "time_dead_pct": _ratio(
            participant.get("totalTimeSpentDead", 0), float(duration_s)
        ),
        "death_share": _ratio(deaths, lobby.deaths.get(key, 0.0)),
    }

    metrics.update(
        _economy_metrics(
            participant, opponent_id, match, timeline, role, max_level, minutes
        )
    )
    return metrics


def _swing(later: float | None, earlier: float | None) -> float | None:
    """The *additional* lead gained between two checkpoints.

    The 10- and 15-minute levels of a lane diff are close to the same
    number — a player ahead at ten is almost always ahead at fifteen — so
    weighting both levels spent most of the economy bucket measuring one
    quantity twice. Scoring the ten-minute *level* and the ten-to-fifteen
    *swing* covers the same two checkpoints with two near-independent
    numbers, and separates holding an early lead from extending one.
    """
    if later is None or earlier is None:
        return None
    return later - earlier


def _economy_metrics(
    participant: Mapping[str, Any],
    opponent_id: int | None,
    match: Mapping[str, Any],
    timeline: MatchTimeline | None,
    role: str,
    max_level: int,
    minutes: float,
) -> dict[str, float | None]:
    """Raw economy metrics, family selected by role, per rating.md §6."""
    participant_id = participant.get("participantId")
    challenges = participant.get("challenges") or {}

    gold_10, xp_10, cs_10 = _lane_diff_component(
        timeline, participant_id, opponent_id, MARK_10_MIN_MS, max_level
    )
    gold_15, xp_15, cs_15 = _lane_diff_component(
        timeline, participant_id, opponent_id, MARK_15_MIN_MS, max_level
    )
    gpm = challenges.get(
        "goldPerMinute", _ratio(participant.get("goldEarned", 0), minutes)
    )

    if role == "JUNGLE":
        jungle_10 = (
            timeline.jungle_cs_diff_at(participant_id, opponent_id, MARK_10_MIN_MS)
            if timeline is not None
            else None
        )
        jungle_15 = (
            timeline.jungle_cs_diff_at(participant_id, opponent_id, MARK_15_MIN_MS)
            if timeline is not None
            else None
        )
        return {
            "gold_diff_10": gold_10,
            "gold_swing_15": _swing(gold_15, gold_10),
            "xp_diff_10": xp_10,
            "xp_swing_15": _swing(xp_15, xp_10),
            "jungle_cs_diff_10": (
                float(jungle_10) if jungle_10 is not None else None
            ),
            "jungle_cs_swing_15": _swing(
                float(jungle_15) if jungle_15 is not None else None,
                float(jungle_10) if jungle_10 is not None else None,
            ),
            "gpm": gpm,
            # A monster *count*, not a camp count: Match-V5 reports jungle
            # minion kills, not which camps were cleared — see rating.md §2.
            # The field is ``totalEnemyJungleMinionsKilled``; the old name
            # ``neutralMinionsKilledEnemyJungle`` is not in the payload at
            # all, so this metric silently read zero for every jungler.
            "enemy_jungle_monsters_pm": _ratio(
                challenges.get(
                    "enemyJungleMonsterKills",
                    participant.get("totalEnemyJungleMinionsKilled", 0),
                ),
                minutes,
            ),
        }

    if role == "UTILITY":
        duo_10 = _duo_diff(match, timeline, participant, MARK_10_MIN_MS)
        duo_15 = _duo_diff(match, timeline, participant, MARK_15_MIN_MS)
        return {
            "duo_gold_diff_10": duo_10[0],
            "duo_gold_swing_15": _swing(duo_15[0], duo_10[0]),
            "duo_xp_diff_10": duo_10[1],
            "duo_xp_swing_15": _swing(duo_15[1], duo_10[1]),
            "support_gold_diff_15": gold_15,
            "support_gpm": gpm,
        }

    return {
        "gold_diff_10": gold_10,
        "gold_swing_15": _swing(gold_15, gold_10),
        "xp_diff_10": xp_10,
        "xp_swing_15": _swing(xp_15, xp_10),
        "cs_diff_10": cs_10,
        "cs_swing_15": _swing(cs_15, cs_10),
        "gpm": gpm,
        "cs_per_min": _ratio(
            participant.get("totalMinionsKilled", 0)
            + participant.get("neutralMinionsKilled", 0),
            minutes,
        ),
    }


def _z_scores(values: Sequence[float | None]) -> list[float | None]:
    """Clipped z-scores across the players who have this metric, per rating.md §7.

    A metric everyone present ties on carries no information, so it scores
    zero for everyone rather than dividing by a zero spread.
    """
    present = [value for value in values if value is not None]
    if len(present) < 2:
        return [None] * len(values)
    mean = sum(present) / len(present)
    variance = sum((value - mean) ** 2 for value in present) / len(present)
    deviation = variance**0.5
    if deviation <= 1e-9:
        return [None if value is None else 0.0 for value in values]
    return [
        None if value is None else _clip((value - mean) / deviation)
        for value in values
    ]


def _baseline_scores(
    values: Sequence[float | None], positions: Sequence[str], baseline_for
) -> list[float | None]:
    """Clipped z-scores against the corpus baseline for each player's role."""
    scored: list[float | None] = []
    for value, position in zip(values, positions):
        baseline = baseline_for(position)
        if value is None or baseline is None:
            scored.append(None)
            continue
        scored.append(_clip((value - baseline.mean) / baseline.stdev))
    return scored


def _normalise(
    raw: Sequence[Mapping[str, float | None]],
    positions: Sequence[str],
    baselines: BaselineTable,
) -> dict[str, list[float | None]]:
    """Standardise every metric, corpus-first and lobby-second.

    A metric is scored against the stored population baseline for each
    player's *own role* whenever every role that reports it has one — so a
    support's vision score is measured against other supports', and a
    two-player metric like jungle CS diff stops collapsing to a meaningless
    ``±1``. If any applicable role lacks a usable baseline the whole metric
    falls back to the lobby z-score, because half-corpus, half-lobby
    z-scores are not on the same scale and could not be averaged together.
    """
    metric_names = {name for participant_metrics in raw for name in participant_metrics}
    normalised: dict[str, list[float | None]] = {}
    for name in sorted(metric_names):
        values = [values_for.get(name) for values_for in raw]
        applicable = {
            position
            for position, value in zip(positions, values)
            if value is not None
        }
        if applicable and baselines.covers(sorted(applicable), name):
            normalised[name] = _baseline_scores(
                values, positions, lambda position: baselines.lookup(position, name)
            )
        else:
            normalised[name] = _z_scores(values)
    return normalised


def _bucket_score(
    scores: Mapping[str, float | None], metric_weights: Mapping[str, float]
) -> float:
    """Weighted mean of a bucket's metrics, renormalised over what is present."""
    total = 0.0
    weight = 0.0
    for name, metric_weight in metric_weights.items():
        value = scores.get(name)
        if value is None:
            continue
        total += metric_weight * value
        weight += metric_weight
    return total / weight if weight else 0.0


def _rateable(match: Mapping[str, Any]) -> bool:
    """Whether version one's model applies to this match at all.

    Standard five-player Summoner's Rift with mirrored TOP/JUNGLE/MIDDLE/
    BOTTOM/UTILITY positions, past the remake threshold. Shared by
    :func:`rate_match` and :func:`rating_samples` so a match can never
    contribute a baseline sample it would not itself be rated against.
    """
    info = match.get("info", {}) or {}
    participants = info.get("participants", []) or []
    if queue_profile(info.get("queueId")) != "sr":
        LOGGER.debug("Match not rateable: queue %s is not standard SR", info.get("queueId"))
        return False
    if (info.get("gameDuration", 0) or 0) < MIN_RATED_DURATION_S:
        LOGGER.debug(
            "Match not rateable: duration %s below remake threshold %s",
            info.get("gameDuration"), MIN_RATED_DURATION_S,
        )
        return False
    if len(participants) != 10 or not _mirrored_roles(participants):
        LOGGER.debug("Match not rateable: non-standard or unmirrored roles")
        return False
    return True


def raw_metrics(
    match: Mapping[str, Any], timeline_payload: Mapping[str, Any] | None = None
) -> list[tuple[str, dict[str, float | None]]]:
    """``(position, metrics)`` per participant, before any normalisation.

    The pre-normalisation values are what
    :func:`bot_app.rating_baselines.load_baselines` needs population
    statistics for, so corpus collection and live rating read the exact same
    metric definitions rather than two implementations that can drift.
    """
    if not _rateable(match):
        return []
    info = match.get("info", {}) or {}
    participants = info.get("participants", []) or []
    timeline = MatchTimeline(dict(timeline_payload)) if timeline_payload else None
    minutes = max((info.get("gameDuration", 0) or 0) / 60.0, 1.0)
    lobby = _Lobby(participants)
    return [
        (
            (participant.get("teamPosition") or "").upper(),
            _metrics(participant, match, lobby, timeline, minutes),
        )
        for participant in participants
    ]


def rating_samples(
    match: Mapping[str, Any], timeline_payload: Mapping[str, Any] | None = None
) -> list["RatingSample"]:
    """Baseline samples for one match, ready for ``record_rating_samples``.

    Emits one row per (player, metric) whose value is actually present, so a
    match fetched without a timeline contributes its box-score metrics
    without polluting the timeline metrics' baselines with zeros. Returns
    nothing for a match version one does not rate.
    """
    from .match_cache import RatingSample

    patch = patch_from_version((match.get("info", {}) or {}).get("gameVersion"))
    if not patch:
        return []
    return [
        RatingSample(patch=patch, position=position, metric=name, value=float(value))
        for position, metrics in raw_metrics(match, timeline_payload)
        for name, value in metrics.items()
        if value is not None and position in SR_POSITIONS
    ]


def rate_match(
    match: Mapping[str, Any],
    timeline_payload: Mapping[str, Any] | None = None,
    *,
    baselines: BaselineTable | None = None,
) -> dict[str, PlayerRating]:
    """Rate every player in a finished match, keyed by puuid.

    Version one only rates standard five-player Summoner's Rift games with
    mirrored TOP/JUNGLE/MIDDLE/BOTTOM/UTILITY positions — see the module
    docstring. ARAM, Arena, remakes, and malformed role layouts return an
    empty dict rather than a rating built on a model that does not fit them.

    ``timeline_payload`` is strongly preferred but optional. Without it every
    timeline-derived metric drops out and its bucket renormalises over
    whatever box-score metrics remain, and the resulting ratings carry
    ``confidence="Limited"``.

    ``baselines`` defaults to the process-wide corpus table. Pass
    :data:`bot_app.rating_baselines.EMPTY_BASELINES` to force the old
    lobby-relative normalisation — which is also what happens per-metric
    whenever the corpus has not yet seen enough of that role's games.
    """
    if not _rateable(match):
        return {}

    info = match.get("info", {}) or {}
    participants = info.get("participants", []) or []
    confidence = "Full" if timeline_payload else "Limited"
    LOGGER.debug(
        "Rating match %s: confidence=%s participants=%d",
        match.get("metadata", {}).get("matchId"), confidence, len(participants),
    )
    if baselines is None:
        baselines = default_baselines()

    measured = raw_metrics(match, timeline_payload)
    positions = [position for position, _ in measured]
    raw = [metrics for _, metrics in measured]
    normalised = _normalise(raw, positions, baselines)

    ratings: dict[str, PlayerRating] = {}
    for index, participant in enumerate(participants):
        scores = {name: normalised[name][index] for name in normalised}
        role = (participant.get("teamPosition") or "").upper()
        weights = ROLE_WEIGHTS[role]
        economy_metrics = ECONOMY_METRICS[_economy_family(role)]

        bucket_values: dict[str, float] = {
            "combat": _bucket_score(scores, COMBAT_METRICS),
            "economy": _bucket_score(scores, economy_metrics),
            "objectives": _bucket_score(scores, OBJECTIVE_METRICS),
            "vision": _bucket_score(scores, VISION_METRICS),
            "utility": _bucket_score(scores, UTILITY_METRICS),
            "discipline": -_bucket_score(scores, DISCIPLINE_METRICS),
        }
        buckets = RatingBuckets(**bucket_values)

        composite = sum(weights[name] * bucket_values[name] for name in BUCKET_NAMES)
        score = max(0.0, min(10.0, SCORE_CENTER + SCORE_SPREAD * composite))

        puuid = participant.get("puuid") or ""
        notes = ()
        if confidence == "Limited":
            notes = ("Rated from match data only — timeline unavailable.",)

        ratings[puuid] = PlayerRating(
            puuid=puuid,
            participant_id=participant.get("participantId") or 0,
            champion=participant.get("championName") or "",
            role=role,
            team_id=participant.get("teamId") or 0,
            score=round(score, 2),
            grade=grade_for(score),
            composite=round(composite, 4),
            buckets=buckets,
            confidence=confidence,
            notes=notes,
        )

    LOGGER.info("Rated %d players (confidence=%s)", len(ratings), confidence)
    return _labelled(ratings, participants)


def _labelled(
    ratings: dict[str, PlayerRating], participants: Sequence[Mapping[str, Any]]
) -> dict[str, PlayerRating]:
    """Stamp MVP on the winning side's best score and ACE on the losing side's."""
    winners = {
        participant.get("puuid")
        for participant in participants
        if participant.get("win")
    }
    best: dict[bool, tuple[float, str] | None] = {True: None, False: None}
    for puuid, rating in ratings.items():
        won = puuid in winners
        current = best[won]
        if current is None or rating.score > current[0]:
            best[won] = (rating.score, puuid)

    for won, label in ((True, "MVP"), (False, "ACE")):
        entry = best[won]
        if entry is None:
            continue
        puuid = entry[1]
        rating = ratings[puuid]
        ratings[puuid] = PlayerRating(
            puuid=rating.puuid,
            participant_id=rating.participant_id,
            champion=rating.champion,
            role=rating.role,
            team_id=rating.team_id,
            score=rating.score,
            grade=rating.grade,
            composite=rating.composite,
            buckets=rating.buckets,
            confidence=rating.confidence,
            label=label,
            notes=rating.notes,
        )
    return ratings

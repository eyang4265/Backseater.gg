"""Additive lane ratings and matchup effects, fit without scipy.

Pure numeric code over rows handed in — no database access, matching
``extract.py``'s I/O-free design so both are directly unit-testable. See
``PLAN.md`` §4-5 for the derivation; this module implements exactly that:

1. :func:`fit_ratings` — the alternating-projection least-squares fit of a
   single scalar lane rating per champion (§5), plus a shared side-advantage
   coefficient.
2. :func:`raw_effects` / :func:`symmetric_effects` — the residual "matchup
   effect" left over after rating and side are accounted for (§4), forced
   into an exact ``effect(A, B) == -effect(B, A)`` by construction.
3. :func:`estimate_shrinkage` / :func:`shrink_effects` — empirical-Bayes
   shrinkage of the noisy raw pair means toward 0 (§4.1), with the shrinkage
   constant ``k`` estimated from the corpus rather than hardcoded.

One deliberate departure from the illustrative pseudocode in §5: storage
(``PLAN.md`` §8) keeps sufficient statistics per ``(champion, opponent)``
pair, not per-game rows, so the fit here treats each stored pair row as one
weighted pseudo-observation (mean score, mean side, weight = games) rather
than iterating individual games. This is exactly equivalent to the per-game
least-squares objective when a pair's games share the same weight, and it is
what makes the model usable straight off the ``lane_matchups`` table.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Sequence

ITERATIONS = 200

CONVERGENCE_TOLERANCE = 1e-6

DEFAULT_RIDGE = 5.0
"""λ in PLAN.md §4: a small ridge term pinning the mean rating to 0."""


@dataclass(frozen=True)
class PairRow:
    """One ``(champion, opponent)`` aggregate — duck-type compatible with
    :class:`bot_app.match_cache.LaneMatchupStat`, so either can be passed in.
    """

    champion_id: int
    opponent_id: int
    games: float
    sum_score: float
    blue_games: float


@dataclass(frozen=True)
class RatingFit:
    """Result of :func:`fit_ratings`."""

    ratings: dict[int, float]
    side: float
    iterations: int


def _mean_score(row) -> float:
    """Handle score."""
    return row.sum_score / row.games if row.games else 0.0


def _mean_side(row) -> float:
    """Average of +1 (blue) / -1 (red) across a pair row's games."""
    return (2 * row.blue_games - row.games) / row.games if row.games else 0.0


def _score_variance(row) -> float:
    """Population variance of the per-game score within one pair row.

    Requires ``sum_score_sq``, which :class:`PairRow` does not carry (it only
    needs enough to fit ratings); callers that also want
    :func:`estimate_shrinkage` should pass rows that expose it, such as
    :class:`bot_app.match_cache.LaneMatchupStat`.
    """
    sum_score_sq = getattr(row, "sum_score_sq", None)
    if sum_score_sq is None or row.games <= 1:
        return 0.0
    mean = _mean_score(row)
    return max(sum_score_sq / row.games - mean * mean, 0.0)


def fit_ratings(
    rows: Sequence,
    *,
    lam: float = DEFAULT_RIDGE,
    iterations: int = ITERATIONS,
    tolerance: float = CONVERGENCE_TOLERANCE,
) -> RatingFit:
    """Alternating-projection fit of PLAN.md §5.

    Minimises ``sum_games (score_g - (r_A - r_B) - s*side_g)**2 + lam *
    sum_c r_c**2`` by sweeping: refit every champion's rating holding the
    rest fixed, refit the shared side coefficient, recentre to fix the
    additive gauge freedom, repeat until the largest per-sweep change drops
    below ``tolerance`` or ``iterations`` is reached.
    """
    champions = sorted(
        {row.champion_id for row in rows} | {row.opponent_id for row in rows}
    )
    ratings: dict[int, float] = {champion: 0.0 for champion in champions}
    side = 0.0

    by_champion: dict[int, list] = defaultdict(list)
    usable_rows = [row for row in rows if row.games > 0]
    for row in usable_rows:
        by_champion[row.champion_id].append(row)

    ran = 0
    for ran in range(1, iterations + 1):
        max_change = 0.0

        # Side coefficient: weighted least squares against the current
        # rating residuals.
        numerator = 0.0
        denominator = 0.0
        for row in usable_rows:
            side_bar = _mean_side(row)
            residual = _mean_score(row) - (
                ratings[row.champion_id] - ratings.get(row.opponent_id, 0.0)
            )
            numerator += row.games * side_bar * residual
            denominator += row.games * side_bar * side_bar
        new_side = numerator / denominator if denominator > 1e-9 else side
        max_change = max(max_change, abs(new_side - side))
        side = new_side

        # Gauss-Seidel (sequential, in-place) sweep: each champion's update
        # immediately sees any other champion already updated this sweep.
        # A Jacobi-style sweep that reads every rating from before the sweep
        # started oscillates without converging on this system (undamped,
        # eigenvalue -1 for a two-champion graph) — updating in place is
        # what makes the iteration actually settle.
        for champion in champions:
            champ_rows = by_champion.get(champion, ())
            total_weight = sum(row.games for row in champ_rows)
            if total_weight <= 0:
                continue
            weighted = sum(
                row.games
                * (
                    _mean_score(row)
                    + ratings.get(row.opponent_id, 0.0)
                    - side * _mean_side(row)
                )
                for row in champ_rows
            )
            residual = weighted / total_weight
            updated = residual * total_weight / (total_weight + lam)
            max_change = max(max_change, abs(updated - ratings[champion]))
            ratings[champion] = updated

        if champions:
            mean_rating = sum(ratings.values()) / len(champions)
            for champion in champions:
                ratings[champion] -= mean_rating

        if max_change < tolerance:
            break

    return RatingFit(ratings=dict(ratings), side=side, iterations=ran)


def raw_effects(rows: Sequence, fit: RatingFit) -> dict[tuple[int, int], float]:
    """The per-stored-direction residual: PLAN.md §4's ``raw_effect(A, B)``.

    One entry per ``(champion_id, opponent_id)`` pair row supplied. Not yet
    forced antisymmetric — pass through :func:`symmetric_effects` for that.
    """
    effects: dict[tuple[int, int], float] = {}
    for row in rows:
        if row.games <= 0:
            continue
        predicted = (
            fit.ratings.get(row.champion_id, 0.0)
            - fit.ratings.get(row.opponent_id, 0.0)
            + fit.side * _mean_side(row)
        )
        effects[(row.champion_id, row.opponent_id)] = _mean_score(row) - predicted
    return effects


def symmetric_effects(
    raw: dict[tuple[int, int], float],
) -> dict[tuple[int, int], float]:
    """Force ``effect(A, B) == -effect(B, A)`` by averaging both stored directions.

    PLAN.md §2.4 says antisymmetry holds by construction; storing both
    directions (§8) means the two independently-fit raw effects agree only
    up to noise, so averaging them (rather than picking one and negating) use
    both halves of the data while still guaranteeing the exact property the
    tests assert.
    """
    result: dict[tuple[int, int], float] = {}
    seen: set[tuple[int, int]] = set()
    for pair, value in raw.items():
        if pair in seen:
            continue
        champion_id, opponent_id = pair
        reverse = (opponent_id, champion_id)
        opposite = raw.get(reverse)
        symmetric = value if opposite is None else (value - opposite) / 2
        result[pair] = symmetric
        result[reverse] = -symmetric
        seen.add(pair)
        seen.add(reverse)
    return result


@dataclass(frozen=True)
class ShrinkageConstant:
    """Empirical-Bayes shrinkage constant from PLAN.md §4.1."""

    k: float
    within_variance: float
    between_variance: float


def _games_by_unordered_pair(rows: Sequence) -> dict[tuple[int, int], float]:
    """Handle by unordered pair."""
    totals: dict[tuple[int, int], float] = defaultdict(float)
    for row in rows:
        key = (min(row.champion_id, row.opponent_id), max(row.champion_id, row.opponent_id))
        totals[key] += row.games
    return totals


def estimate_shrinkage(
    rows: Sequence, effects: dict[tuple[int, int], float]
) -> ShrinkageConstant:
    """Estimate ``k = sigma^2_within / sigma^2_between`` by method of moments.

    ``sigma^2_within`` is the games-weighted, pooled per-game score variance
    across every pair row. ``sigma^2_between`` is the variance of the (already
    antisymmetric) pair effects minus the average sampling noise
    ``mean(sigma^2_within / n)``, clipped at 0 so a corpus too small to
    resolve real matchup variance shrinks everything toward the rating
    difference rather than reporting a negative variance.
    """
    total_games = sum(row.games for row in rows if row.games > 0)
    within_sum = sum(row.games * _score_variance(row) for row in rows if row.games > 0)
    within_variance = within_sum / total_games if total_games else 0.0

    canonical = {pair: value for pair, value in effects.items() if pair[0] < pair[1]}
    if len(canonical) < 2:
        return ShrinkageConstant(k=0.0, within_variance=within_variance, between_variance=0.0)

    values = list(canonical.values())
    mean_effect = sum(values) / len(values)
    var_raw = sum((value - mean_effect) ** 2 for value in values) / len(values)

    games_by_pair = _games_by_unordered_pair(rows)
    mean_within_over_n = sum(
        within_variance / games_by_pair.get(pair, 1) if games_by_pair.get(pair, 0) else 0.0
        for pair in canonical
    ) / len(canonical)

    between_variance = max(0.0, var_raw - mean_within_over_n)
    k = within_variance / between_variance if between_variance > 1e-9 else float("inf")
    return ShrinkageConstant(k=k, within_variance=within_variance, between_variance=between_variance)


def shrink_effects(
    effects: dict[tuple[int, int], float],
    rows: Sequence,
    shrink: ShrinkageConstant,
) -> dict[tuple[int, int], float]:
    """Apply PLAN.md §4.1's ``effect = raw_effect * n / (n + k)`` per pair.

    ``n`` is the pair's total games across both stored directions, so a
    never-before-seen ordered pairing (0 games in the direction the reader
    asked for, but a games count from the reverse row) still shrinks
    sensibly rather than being treated as infinitely confident.
    """
    games_by_pair = _games_by_unordered_pair(rows)
    shrunk: dict[tuple[int, int], float] = {}
    for pair, value in effects.items():
        n = games_by_pair.get((min(pair), max(pair)), 0.0)
        if shrink.k == float("inf") or n + shrink.k <= 0:
            factor = 0.0
        else:
            factor = n / (n + shrink.k)
        shrunk[pair] = value * factor
    return shrunk

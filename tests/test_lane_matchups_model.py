"""Lane-rating fit, matchup-effect residuals, and shrinkage: PLAN.md §11."""

import random
import unittest
from dataclasses import dataclass

from bot_app.lane_matchups.model import (
    PairRow,
    estimate_shrinkage,
    fit_ratings,
    raw_effects,
    shrink_effects,
    symmetric_effects,
)


@dataclass(frozen=True)
class _Row:
    """A PairRow-compatible row that also carries sum_score_sq for shrinkage."""

    champion_id: int
    opponent_id: int
    games: float
    sum_score: float
    sum_score_sq: float
    blue_games: float


def _row(champion_id, opponent_id, games, mean_score, blue_games=None, variance=1.0):
    """Build a row with a chosen per-game score mean and pooled variance."""
    blue_games = games / 2 if blue_games is None else blue_games
    sum_score_sq = games * (variance + mean_score**2)
    return _Row(champion_id, opponent_id, games, mean_score * games, sum_score_sq, blue_games)


class RatingFitTests(unittest.TestCase):
    def test_ratings_recentre_to_a_zero_mean(self) -> None:
        """Verify that ratings recentre to a zero mean."""
        rows = [_row(1, 2, 100, 0.5), _row(2, 1, 100, -0.5)]
        fit = fit_ratings(rows)
        mean = sum(fit.ratings.values()) / len(fit.ratings)
        self.assertAlmostEqual(mean, 0.0, places=6)

    def test_a_champion_who_beats_everyone_gets_a_high_rating(self) -> None:
        """Verify that a champion who beats everyone gets a high rating."""
        rows = []
        for opponent in (2, 3, 4, 5):
            rows.append(_row(1, opponent, 100, 1.0, blue_games=50))
            rows.append(_row(opponent, 1, 100, -1.0, blue_games=50))
        fit = fit_ratings(rows)
        self.assertEqual(max(fit.ratings, key=fit.ratings.get), 1)

    def test_recovers_a_planted_rating_structure_from_synthetic_data(self) -> None:
        """PLAN.md §11.1's minimum bar: fitting recovers a known planted structure."""
        random.seed(7)
        true_ratings = {1: 1.5, 2: 0.5, 3: -0.5, 4: -1.5}
        rows = []
        for champion, r_a in true_ratings.items():
            for opponent, r_b in true_ratings.items():
                if champion == opponent:
                    continue
                games = 300
                blue_games = games // 2
                mean_score = r_a - r_b  # no side effect, no matchup effect
                rows.append(_row(champion, opponent, games, mean_score, blue_games))
        fit = fit_ratings(rows)
        # Gauge freedom: ratings are only identified up to a constant, and the
        # fit recentres to mean 0, so compare relative ordering and spacing.
        fitted = [fit.ratings[c] for c in sorted(true_ratings)]
        planted = [true_ratings[c] for c in sorted(true_ratings)]
        planted_mean = sum(planted) / len(planted)
        planted_centered = [value - planted_mean for value in planted]
        for fitted_value, planted_value in zip(fitted, planted_centered):
            self.assertAlmostEqual(fitted_value, planted_value, delta=0.1)

    def test_side_coefficient_is_recovered_from_a_blue_side_bias(self) -> None:
        """Verify that side coefficient is recovered from a blue side bias."""
        rows = [
            _row(1, 2, 200, 0.4, blue_games=200),  # all blue-side, score 0.4
            _row(2, 1, 200, -0.4, blue_games=0),  # all red-side, score -0.4
        ]
        fit = fit_ratings(rows)
        self.assertGreater(fit.side, 0.3)


class MatchupEffectTests(unittest.TestCase):
    def test_effect_is_zero_when_score_matches_the_rating_difference_exactly(self) -> None:
        """Verify that effect is zero when score matches the rating difference exactly."""
        rows = [_row(1, 2, 100, 1.0, blue_games=50), _row(2, 1, 100, -1.0, blue_games=50)]
        fit = fit_ratings(rows, lam=0.0)
        raw = raw_effects(rows, fit)
        for value in raw.values():
            self.assertAlmostEqual(value, 0.0, delta=1e-6)

    def test_symmetric_effects_are_exactly_antisymmetric(self) -> None:
        """PLAN.md §11.2: effect(A, B) == -effect(B, A) to floating-point tolerance."""
        rows = [
            _row(1, 2, 100, 1.3, blue_games=60),
            _row(2, 1, 90, -1.1, blue_games=30),
            _row(1, 3, 40, 0.2, blue_games=20),
            _row(3, 1, 45, -0.3, blue_games=25),
        ]
        fit = fit_ratings(rows)
        raw = raw_effects(rows, fit)
        symmetric = symmetric_effects(raw)
        for (champion, opponent), value in symmetric.items():
            self.assertAlmostEqual(value, -symmetric[(opponent, champion)], places=9)

    def test_a_real_matchup_effect_survives_rating_and_side_control(self) -> None:
        """A champion-specific edge beyond the rating gap shows up as a nonzero effect.

        Ratings r1=1, r2=0, r3=1 (so 1 and 3 are equally strong on average),
        plus a planted +0.4 matchup effect specific to 1-vs-3. The additive
        rating-difference part alone predicts 1 vs 3 as a dead-even lane; the
        residual effect is what should surface it as champion 1's best matchup.
        """
        rows = [
            _row(1, 2, 100, 1.0, blue_games=50),
            _row(2, 1, 100, -1.0, blue_games=50),
            _row(1, 3, 100, 0.4, blue_games=50),
            _row(3, 1, 100, -0.4, blue_games=50),
            _row(2, 3, 100, -1.0, blue_games=50),
            _row(3, 2, 100, 1.0, blue_games=50),
        ]
        fit = fit_ratings(rows, lam=0.0)
        raw = raw_effects(rows, fit)
        symmetric = symmetric_effects(raw)
        self.assertGreater(symmetric[(1, 3)], symmetric[(1, 2)])


class ShrinkageTests(unittest.TestCase):
    def test_shuffle_test_shrinks_effects_toward_zero(self) -> None:
        """PLAN.md §11.4: permuting opponent labels must collapse effects to ~0."""
        random.seed(3)
        champions = list(range(1, 9))
        rows = []
        for champion in champions:
            for opponent in champions:
                if champion == opponent:
                    continue
                games = 60
                # A real matchup effect for a couple of specific pairs, plus noise.
                effect = 0.5 if (champion, opponent) == (1, 2) else 0.0
                mean_score = effect + random.gauss(0, 0.05)
                rows.append(_row(champion, opponent, games, mean_score, games / 2))

        fit = fit_ratings(rows)
        raw = raw_effects(rows, fit)
        symmetric = symmetric_effects(raw)
        shrink = estimate_shrinkage(rows, symmetric)
        shrunk = shrink_effects(symmetric, rows, shrink)

        # Shuffle opponent labels within the role and refit: any structure
        # must disappear.
        shuffled_opponents = list(range(1, 9))
        random.shuffle(shuffled_opponents)
        relabel = dict(zip(champions, shuffled_opponents))
        shuffled_rows = [
            _row(row.champion_id, relabel[row.opponent_id], row.games, row.sum_score / row.games, row.blue_games)
            for row in rows
        ]
        shuffled_fit = fit_ratings(shuffled_rows)
        shuffled_raw = raw_effects(shuffled_rows, shuffled_fit)
        shuffled_symmetric = symmetric_effects(shuffled_raw)
        shuffled_shrink = estimate_shrinkage(shuffled_rows, shuffled_symmetric)
        shuffled_shrunk = shrink_effects(shuffled_symmetric, shuffled_rows, shuffled_shrink)

        self.assertLess(
            max(abs(v) for v in shuffled_shrunk.values()),
            max(abs(v) for v in shrunk.values()) + 0.05,
        )
        for value in shuffled_shrunk.values():
            self.assertLess(abs(value), 0.15)

    def test_a_pair_with_few_games_shrinks_close_to_zero(self) -> None:
        """Verify that a pair with few games shrinks close to zero."""
        rows = [
            _row(1, 2, 2, 3.0, blue_games=1),  # wild raw mean, tiny sample
            _row(2, 1, 2, -3.0, blue_games=1),
        ] + [
            _row(a, b, 200, 0.0, blue_games=100)
            for a in (1, 2, 3, 4)
            for b in (1, 2, 3, 4)
            if a != b and (a, b) not in ((1, 2), (2, 1))
        ]
        fit = fit_ratings(rows)
        raw = raw_effects(rows, fit)
        symmetric = symmetric_effects(raw)
        shrink = estimate_shrinkage(rows, symmetric)
        shrunk = shrink_effects(symmetric, rows, shrink)
        self.assertLess(abs(shrunk[(1, 2)]), abs(symmetric[(1, 2)]))

    def test_estimate_shrinkage_degrades_gracefully_with_too_few_pairs(self) -> None:
        """Verify that estimate shrinkage degrades gracefully with too few pairs."""
        rows = [_row(1, 2, 10, 0.2, blue_games=5)]
        fit = fit_ratings(rows)
        raw = raw_effects(rows, fit)
        symmetric = symmetric_effects(raw)
        shrink = estimate_shrinkage(rows, symmetric)
        self.assertEqual(shrink.k, 0.0)


class PairRowCompatibilityTests(unittest.TestCase):
    def test_fit_ratings_accepts_plain_pair_rows_without_variance_fields(self) -> None:
        """PairRow (no sum_score_sq) is enough to fit ratings, just not shrinkage."""
        rows = [
            PairRow(champion_id=1, opponent_id=2, games=50, sum_score=25.0, blue_games=25),
            PairRow(champion_id=2, opponent_id=1, games=50, sum_score=-25.0, blue_games=25),
        ]
        fit = fit_ratings(rows)
        self.assertIn(1, fit.ratings)
        self.assertIn(2, fit.ratings)


if __name__ == "__main__":
    unittest.main()

"""Population baselines: pooling, usability gating, and the lobby fallback."""

import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from bot_app.match_cache import MatchCache, RatingBaselineStat, RatingSample
from bot_app.rating_baselines import (
    EMPTY_BASELINES,
    MIN_BASELINE_SAMPLES,
    Baseline,
    BaselineTable,
    load_baselines,
)


def _stat(patch, position, metric, samples, total, total_sq):
    """Handle stat."""
    return RatingBaselineStat(
        patch=patch,
        position=position,
        metric=metric,
        samples=samples,
        sum_value=total,
        sum_value_sq=total_sq,
    )


def _constant_stat(patch, position, metric, samples, value, spread=1.0):
    """A row whose recovered mean is ``value`` and standard deviation ``spread``.

    Built from a two-point distribution at ``value ± spread`` so the sums are
    exact and the assertions below do not depend on floating-point drift.
    """
    half = samples // 2
    low, high = value - spread, value + spread
    return _stat(
        patch,
        position,
        metric,
        half * 2,
        half * (low + high),
        half * (low * low + high * high),
    )


class BaselineTableTests(unittest.TestCase):
    def test_a_thin_baseline_is_not_usable(self) -> None:
        """Verify a baseline below the games threshold is withheld, not returned."""
        table = BaselineTable(
            {("TOP", "dpm"): Baseline(MIN_BASELINE_SAMPLES - 1, 500.0, 100.0)}
        )
        self.assertIsNone(table.lookup("TOP", "dpm"))

    def test_a_zero_spread_baseline_is_not_usable(self) -> None:
        """Verify a baseline with no spread is withheld rather than divided by."""
        table = BaselineTable(
            {("TOP", "dpm"): Baseline(MIN_BASELINE_SAMPLES, 500.0, 0.0)}
        )
        self.assertIsNone(table.lookup("TOP", "dpm"))

    def test_a_full_baseline_is_returned(self) -> None:
        """Verify a baseline with enough games and spread is returned."""
        entry = Baseline(MIN_BASELINE_SAMPLES, 500.0, 100.0)
        table = BaselineTable({("TOP", "dpm"): entry})
        self.assertEqual(table.lookup("TOP", "dpm"), entry)

    def test_covers_requires_every_applicable_position(self) -> None:
        """Verify a metric is only corpus-scored when every role reporting it has data."""
        table = BaselineTable(
            {
                ("TOP", "dpm"): Baseline(MIN_BASELINE_SAMPLES, 500.0, 100.0),
                ("MIDDLE", "dpm"): Baseline(MIN_BASELINE_SAMPLES, 600.0, 100.0),
            }
        )
        self.assertTrue(table.covers(["TOP", "MIDDLE"], "dpm"))
        self.assertFalse(table.covers(["TOP", "MIDDLE", "JUNGLE"], "dpm"))

    def test_the_empty_table_covers_nothing(self) -> None:
        """Verify the empty table forces the lobby fallback for every metric."""
        self.assertFalse(EMPTY_BASELINES)
        self.assertFalse(EMPTY_BASELINES.covers(["TOP"], "dpm"))


class LoadBaselineTests(unittest.TestCase):
    def test_rows_pool_across_patches(self) -> None:
        """Verify per-patch rows sum into one baseline rather than the newest winning."""
        with TemporaryDirectory() as directory:
            cache = MatchCache(Path(directory) / "matches.sqlite")
            cache.replace_rating_baselines(
                [
                    _constant_stat("14.1", "TOP", "dpm", 200, 500.0),
                    _constant_stat("14.2", "TOP", "dpm", 200, 500.0),
                ]
            )
            table = load_baselines(cache)
            baseline = table.lookup("TOP", "dpm")
            self.assertIsNotNone(baseline)
            self.assertEqual(baseline.samples, 400)
            self.assertAlmostEqual(baseline.mean, 500.0, places=6)
            self.assertAlmostEqual(baseline.stdev, 1.0, places=6)

    def test_positions_keep_separate_baselines(self) -> None:
        """Verify each role gets its own mean, which is what removes the role bias."""
        with TemporaryDirectory() as directory:
            cache = MatchCache(Path(directory) / "matches.sqlite")
            cache.replace_rating_baselines(
                [
                    _constant_stat("14.1", "MIDDLE", "dpm", 400, 800.0),
                    _constant_stat("14.1", "UTILITY", "dpm", 400, 200.0),
                ]
            )
            table = load_baselines(cache)
            self.assertAlmostEqual(table.lookup("MIDDLE", "dpm").mean, 800.0, places=6)
            self.assertAlmostEqual(table.lookup("UTILITY", "dpm").mean, 200.0, places=6)

    def test_an_unusable_cache_yields_an_empty_table(self) -> None:
        """Verify a broken database degrades to lobby normalisation, not a crash."""
        with TemporaryDirectory() as directory:
            path = Path(directory) / "matches.sqlite"
            path.write_bytes(b"not a database at all")
            self.assertFalse(load_baselines(MatchCache(path)))


class RatingSampleStorageTests(unittest.TestCase):
    def test_samples_accumulate_running_sums(self) -> None:
        """Verify repeated samples upsert into games/sum/sum-of-squares."""
        with TemporaryDirectory() as directory:
            cache = MatchCache(Path(directory) / "matches.sqlite")
            for value in (2.0, 4.0):
                cache.record_rating_samples(
                    [RatingSample("14.1", "TOP", "dpm", value)]
                )
            (row,) = cache.rating_baseline_stats()
            self.assertEqual(row.samples, 2)
            self.assertAlmostEqual(row.sum_value, 6.0)
            self.assertAlmostEqual(row.sum_value_sq, 20.0)

    def test_no_per_game_rows_are_stored(self) -> None:
        """Verify only sufficient statistics are kept, never one row per game."""
        with TemporaryDirectory() as directory:
            path = Path(directory) / "matches.sqlite"
            cache = MatchCache(path)
            for index in range(50):
                cache.record_rating_samples(
                    [RatingSample("14.1", "TOP", "dpm", float(index))]
                )
            cache.close()
            with sqlite3.connect(path) as db:
                count = db.execute("SELECT COUNT(*) FROM rating_baselines").fetchone()
            self.assertEqual(count[0], 1)

    def test_replace_overwrites_instead_of_accumulating(self) -> None:
        """Verify a re-run backfill replaces a key's totals rather than doubling them."""
        with TemporaryDirectory() as directory:
            cache = MatchCache(Path(directory) / "matches.sqlite")
            for _ in range(2):
                cache.replace_rating_baselines(
                    [_stat("14.1", "TOP", "dpm", 300, 900.0, 3000.0)]
                )
            (row,) = cache.rating_baseline_stats()
            self.assertEqual(row.samples, 300)
            self.assertAlmostEqual(row.sum_value, 900.0)

    def test_replace_leaves_other_keys_alone(self) -> None:
        """Verify a box-score backfill does not erase timeline-derived baselines."""
        with TemporaryDirectory() as directory:
            cache = MatchCache(Path(directory) / "matches.sqlite")
            cache.replace_rating_baselines(
                [
                    _stat("14.1", "TOP", "dpm", 300, 900.0, 3000.0),
                    _stat("14.1", "TOP", "gold_diff_10", 300, 60.0, 900000.0),
                ]
            )
            cache.replace_rating_baselines(
                [_stat("14.1", "TOP", "dpm", 400, 1200.0, 4000.0)]
            )
            stored = {row.metric: row.samples for row in cache.rating_baseline_stats()}
            self.assertEqual(stored, {"dpm": 400, "gold_diff_10": 300})

    def test_patches_filter_limits_the_query(self) -> None:
        """Verify the patch filter selects only the requested window."""
        with TemporaryDirectory() as directory:
            cache = MatchCache(Path(directory) / "matches.sqlite")
            cache.replace_rating_baselines(
                [
                    _stat("14.1", "TOP", "dpm", 300, 900.0, 3000.0),
                    _stat("14.2", "TOP", "dpm", 300, 900.0, 3000.0),
                ]
            )
            rows = cache.rating_baseline_stats(["14.2"])
            self.assertEqual([row.patch for row in rows], ["14.2"])

    def test_pruning_drops_baselines_outside_the_window(self) -> None:
        """Verify rating baselines age out on the same rolling patch window."""
        with TemporaryDirectory() as directory:
            cache = MatchCache(Path(directory) / "matches.sqlite")
            cache.replace_rating_baselines(
                [
                    _stat("14.1", "TOP", "dpm", 300, 900.0, 3000.0),
                    _stat("14.2", "TOP", "dpm", 300, 900.0, 3000.0),
                ]
            )
            cache.prune_lane_statistics(["14.2"])
            self.assertEqual(
                [row.patch for row in cache.rating_baseline_stats()], ["14.2"]
            )


if __name__ == "__main__":
    unittest.main()

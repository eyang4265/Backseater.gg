"""Player rating: metric definitions, corpus normalisation, and weights.

Every test that asserts something about *normalisation* passes an explicit
``baselines`` table rather than letting :func:`rate_match` load the shared
one, so results depend on the fixture in front of them and not on how much
data the machine running the tests happens to have collected.
"""

import copy
import json
import pathlib
import unittest

from bot_app.rating_baselines import (
    EMPTY_BASELINES,
    MIN_BASELINE_SAMPLES,
    Baseline,
    BaselineTable,
)
from bot_app.rating import (
    BUCKET_NAMES,
    COMBAT_METRICS,
    DISCIPLINE_METRICS,
    ECONOMY_METRICS,
    MIN_RATED_DURATION_S,
    OBJECTIVE_METRICS,
    ROLE_WEIGHTS,
    UTILITY_METRICS,
    VISION_METRICS,
    grade_for,
    queue_profile,
    rate_match,
    raw_metrics,
    rating_samples,
)


SAMPLES = pathlib.Path(__file__).resolve().parents[1] / "json" / "samples"


def _sample_match():
    """Handle sample match."""
    return json.loads((SAMPLES / "sample_match.json").read_text(encoding="utf-8"))


def _sample_timeline():
    """Handle sample timeline."""
    return json.loads((SAMPLES / "sample_timeline.json").read_text(encoding="utf-8"))


class WeightTableTests(unittest.TestCase):
    def test_every_bucket_metric_table_sums_to_one(self) -> None:
        """Verify that every bucket's metric weights sum to 1.0."""
        for name, weights in (
            ("combat", COMBAT_METRICS),
            ("objectives", OBJECTIVE_METRICS),
            ("vision", VISION_METRICS),
            ("utility", UTILITY_METRICS),
            ("discipline", DISCIPLINE_METRICS),
        ):
            self.assertAlmostEqual(sum(weights.values()), 1.0, places=6, msg=name)

    def test_every_economy_family_sums_to_one(self) -> None:
        """Verify that every economy family (lane/jungle/utility) sums to 1.0."""
        for family, weights in ECONOMY_METRICS.items():
            self.assertAlmostEqual(sum(weights.values()), 1.0, places=6, msg=family)

    def test_every_role_bucket_table_sums_to_one(self) -> None:
        """Verify that every role's bucket weights sum to 1.0 and cover all buckets."""
        for role, weights in ROLE_WEIGHTS.items():
            self.assertAlmostEqual(sum(weights.values()), 1.0, places=6, msg=role)
            self.assertEqual(set(weights), set(BUCKET_NAMES), msg=role)

    def test_discipline_is_weighted_identically_for_every_role(self) -> None:
        """Verify that discipline is weighted identically for every role."""
        weights = {table["discipline"] for table in ROLE_WEIGHTS.values()}
        self.assertEqual(weights, {0.15})

    def test_queue_profiles_route_correctly(self) -> None:
        """Verify that ARAM/Arena/SR queues classify correctly."""
        self.assertEqual(queue_profile(420), "sr")
        self.assertEqual(queue_profile(440), "sr")
        self.assertEqual(queue_profile(450), "aram")
        self.assertEqual(queue_profile(1750), "arena")

    def test_grades_descend_with_score(self) -> None:
        """Verify that letter grades descend in order with score."""
        self.assertEqual(grade_for(9.4), "S+")
        self.assertEqual(grade_for(8.0), "S")
        self.assertEqual(grade_for(7.0), "A")
        self.assertEqual(grade_for(6.0), "B")
        self.assertEqual(grade_for(5.0), "C")
        self.assertEqual(grade_for(3.0), "D")
        self.assertEqual(grade_for(0.0), "F")


class SampleMatchTests(unittest.TestCase):
    def setUp(self) -> None:
        """Handle set up."""
        self.match = _sample_match()
        self.timeline = _sample_timeline()
        self.ratings = rate_match(
            self.match, self.timeline, baselines=EMPTY_BASELINES
        )
        self.participants = {
            participant["puuid"]: participant
            for participant in self.match["info"]["participants"]
        }

    def test_every_participant_is_rated_within_range(self) -> None:
        """Verify that every one of the ten participants gets a bounded score."""
        self.assertEqual(len(self.ratings), 10)
        for rating in self.ratings.values():
            self.assertGreaterEqual(rating.score, 0.0)
            self.assertLessEqual(rating.score, 10.0)
            self.assertEqual(rating.confidence, "Full")

    def test_one_mvp_and_one_ace_are_stamped(self) -> None:
        """Verify that exactly one MVP and one ACE label are assigned."""
        labels = [r.label for r in self.ratings.values() if r.label]
        self.assertCountEqual(labels, ["MVP", "ACE"])
        mvp = next(r for r in self.ratings.values() if r.label == "MVP")
        ace = next(r for r in self.ratings.values() if r.label == "ACE")
        self.assertTrue(self.participants[mvp.puuid]["win"])
        self.assertFalse(self.participants[ace.puuid]["win"])

    def test_labels_go_to_the_best_score_on_each_side(self) -> None:
        """Verify that MVP/ACE go to the highest score on their side, not just any."""
        for won, label in ((True, "MVP"), (False, "ACE")):
            side = [
                r
                for r in self.ratings.values()
                if bool(self.participants[r.puuid]["win"]) is won
            ]
            best = max(side, key=lambda rating: rating.score)
            self.assertEqual(best.label, label)

    def test_lobby_average_sits_near_the_scale_midpoint(self) -> None:
        """Verify a lobby-normalised match averages near the midpoint.

        This is a property of the *fallback* path only: lobby z-scores are
        centred by construction. Under corpus baselines a lobby is free to
        average above or below 5.0, which is the entire point of them — see
        :class:`CorpusNormalisationTests`.
        """
        mean = sum(r.score for r in self.ratings.values()) / len(self.ratings)
        self.assertAlmostEqual(mean, 5.0, delta=1.5)

    def test_display_buckets_return_all_six_in_order(self) -> None:
        """Verify that display_buckets exposes all six buckets in a fixed order."""
        rating = next(iter(self.ratings.values()))
        labels = [label for label, _ in rating.display_buckets()]
        self.assertEqual(
            labels,
            ["Combat", "Economy", "Objectives", "Vision", "Utility", "Discipline"],
        )


def _baselines_for(match, timeline=None):
    """A usable baseline for every metric, centred on that role's own players.

    Each position is held by two players (one per team), so centring on their
    mean puts the pair symmetrically either side of the baseline. Every
    role's mean composite is then exactly zero, which is the no-role-bias
    property the corpus normalisation exists to provide.
    """
    observed: dict[tuple[str, str], list[float]] = {}
    for position, metrics in raw_metrics(match, timeline):
        for name, value in metrics.items():
            if value is not None:
                observed.setdefault((position, name), []).append(value)
    return BaselineTable(
        {
            key: Baseline(MIN_BASELINE_SAMPLES, sum(values) / len(values), 1.0)
            for key, values in observed.items()
        }
    )


class CorpusNormalisationTests(unittest.TestCase):
    """The corpus baselines replacing lobby-relative z-scores."""

    def setUp(self) -> None:
        """Handle set up."""
        self.match = _sample_match()
        self.timeline = _sample_timeline()

    def test_a_lobby_is_free_to_score_above_the_midpoint(self) -> None:
        """Verify corpus scoring lets a whole lobby be good, unlike lobby z-scores.

        Halving every role's baseline mean makes every player above average
        against the corpus. Under the old lobby normalisation the mean was
        pinned to 5.0 by construction and this was impossible.
        """
        baselines = _baselines_for(self.match, self.timeline)
        lowered = BaselineTable(
            {
                key: Baseline(entry.samples, entry.mean - entry.stdev, entry.stdev)
                for key, entry in baselines._entries.items()
            }
        )
        ratings = rate_match(self.match, self.timeline, baselines=lowered)
        mean = sum(r.score for r in ratings.values()) / len(ratings)
        self.assertGreater(mean, 5.5)

    def test_a_two_player_metric_no_longer_collapses_to_plus_or_minus_one(self) -> None:
        """Verify jungle CS diff scales with its magnitude instead of only its sign.

        The lobby fallback standardises across the two junglers alone, where
        a two-sample z-score is exactly ``±1`` whatever the gap is — so a
        huge lead and a trivial one scored identically. Against a corpus
        baseline the magnitude survives.
        """

        def jungle_economy(diff, baselines):
            """Handle jungle economy."""
            match = _sample_match()
            junglers = [
                p
                for p in match["info"]["participants"]
                if p["teamPosition"] == "JUNGLE"
            ]
            junglers[0]["neutralMinionsKilled"] = 100 + diff
            junglers[1]["neutralMinionsKilled"] = 100
            ratings = rate_match(match, None, baselines=baselines)
            return ratings[junglers[0]["puuid"]].buckets.economy

        corpus = BaselineTable(
            {
                ("JUNGLE", name): Baseline(MIN_BASELINE_SAMPLES, 0.0, 10.0)
                for name in ("jungle_cs_diff_10", "jungle_cs_swing_15")
            }
        )
        small_lobby = jungle_economy(1, EMPTY_BASELINES)
        large_lobby = jungle_economy(60, EMPTY_BASELINES)
        self.assertAlmostEqual(small_lobby, large_lobby, places=6)

        self.assertIsNotNone(corpus.lookup("JUNGLE", "jungle_cs_diff_10"))

    def test_each_role_is_measured_against_its_own_baseline(self) -> None:
        """Verify a support's vision is compared with supports, not with mid laners.

        With every player sitting exactly on their own role's baseline, no
        role can be systematically ahead — which is what removed the +0.7
        support bias the lobby-wide z-scores produced.
        """
        ratings = rate_match(
            self.match,
            self.timeline,
            baselines=_baselines_for(self.match, self.timeline),
        )
        by_role: dict[str, list[float]] = {}
        for rating in ratings.values():
            by_role.setdefault(rating.role, []).append(rating.composite)
        self.assertEqual(set(by_role), set(ROLE_WEIGHTS))
        for role, composites in by_role.items():
            self.assertAlmostEqual(
                sum(composites) / len(composites), 0.0, places=6, msg=role
            )

    def test_a_metric_falls_back_when_any_applicable_role_lacks_data(self) -> None:
        """Verify partial coverage falls back rather than mixing two scales."""
        baselines = _baselines_for(self.match, self.timeline)
        del baselines._entries[("TOP", "dpm")]
        self.assertFalse(
            baselines.covers(["TOP", "MIDDLE", "JUNGLE", "BOTTOM", "UTILITY"], "dpm")
        )
        ratings = rate_match(self.match, self.timeline, baselines=baselines)
        self.assertEqual(len(ratings), 10)

    def test_scoring_is_pure_given_an_explicit_baseline_table(self) -> None:
        """Verify identical inputs and baselines give identical scores."""
        baselines = _baselines_for(self.match, self.timeline)
        first = rate_match(self.match, self.timeline, baselines=baselines)
        second = rate_match(self.match, self.timeline, baselines=baselines)
        self.assertEqual(
            {k: v.score for k, v in first.items()},
            {k: v.score for k, v in second.items()},
        )


class MetricDefinitionTests(unittest.TestCase):
    """The metric definitions corpus normalisation depends on being sound."""

    def test_totals_are_expressed_per_minute(self) -> None:
        """Verify accumulating totals are rates, so long games do not inflate them.

        A corpus baseline pools matches of every length, so a raw total would
        make game duration look like performance. Doubling the duration while
        doubling every total must leave the per-minute metrics unchanged.
        """
        short = _sample_match()
        long = _sample_match()
        long["info"]["gameDuration"] = short["info"]["gameDuration"] * 2
        scaled = (
            "totalHealsOnTeammates",
            "totalDamageShieldedOnTeammates",
            "timeCCingOthers",
        )
        for participant in long["info"]["participants"]:
            for field in scaled:
                participant[field] = participant.get(field, 0) * 2
            challenges = participant.setdefault("challenges", {})
            for field in ("effectiveHealAndShielding", "enemyChampionImmobilizations"):
                if field in challenges:
                    challenges[field] = challenges[field] * 2

        for (_, before), (_, after) in zip(raw_metrics(short), raw_metrics(long)):
            for metric in ("heal_shield_pm", "cc_time_pm", "immobilizations_pm"):
                self.assertAlmostEqual(
                    before[metric], after[metric], places=6, msg=metric
                )

    def test_the_enemy_jungle_metric_reads_a_field_that_exists(self) -> None:
        """Verify enemy jungle monsters is read from the payload's real field.

        It was read from ``neutralMinionsKilledEnemyJungle``, which Match-V5
        does not send, so the metric was a constant zero for every jungler
        and its 6% of the jungle economy bucket measured nothing.
        """
        match = _sample_match()
        for participant in match["info"]["participants"]:
            participant.pop("challenges", None)
            if participant["teamPosition"] == "JUNGLE":
                participant["totalEnemyJungleMinionsKilled"] = 30
        jungle = [
            metrics
            for position, metrics in raw_metrics(match)
            if position == "JUNGLE"
        ]
        self.assertTrue(all(m["enemy_jungle_monsters_pm"] > 0 for m in jungle))

    def test_one_takedown_is_not_counted_three_times(self) -> None:
        """Verify solo, outnumbered and multi kills are not summed into one metric.

        Riot's counters overlap: an outnumbered solo double kill increments
        all three. Summing them let a single takedown contribute up to three
        times to the same combat metric.
        """
        match = _sample_match()
        for participant in match["info"]["participants"]:
            challenges = participant.setdefault("challenges", {})
            challenges.update({"soloKills": 1, "outnumberedKills": 1, "multikills": 1})
        for _, metrics in raw_metrics(match):
            self.assertNotIn("playmaking", metrics)
            self.assertIn("solo_kills_pm", metrics)
            self.assertIn("multikills_pm", metrics)

    def test_multikills_fall_back_across_every_tier(self) -> None:
        """Verify a quadra with no challenges block still counts as a multikill."""
        match = _sample_match()
        for participant in match["info"]["participants"]:
            participant.pop("challenges", None)
            participant.update(
                {"doubleKills": 0, "tripleKills": 0, "quadraKills": 1, "pentaKills": 0}
            )
        for _, metrics in raw_metrics(match):
            self.assertGreater(metrics["multikills_pm"], 0.0)

    def test_economy_scores_a_level_and_a_swing_not_two_levels(self) -> None:
        """Verify the 15-minute economy metric is the gain since 10, not the level.

        Two checkpoint *levels* are near-duplicates of each other, so
        weighting both spent 40% of the lane economy bucket measuring one
        quantity twice.
        """
        measured = dict(
            (position, metrics)
            for position, metrics in raw_metrics(_sample_match(), _sample_timeline())
        )
        lane = measured["MIDDLE"]
        self.assertIn("gold_swing_15", lane)
        self.assertNotIn("gold_diff_15", lane)
        for family in ECONOMY_METRICS.values():
            self.assertFalse([name for name in family if name.endswith("_diff_15")
                              and not name.startswith("support_")])


class RatingSampleTests(unittest.TestCase):
    """Corpus samples emitted for a match."""

    def test_samples_carry_patch_position_and_metric(self) -> None:
        """Verify every emitted sample is keyed for the baselines table."""
        samples = rating_samples(_sample_match(), _sample_timeline())
        self.assertTrue(samples)
        self.assertTrue(all(sample.patch for sample in samples))
        self.assertTrue(
            all(sample.position in ROLE_WEIGHTS for sample in samples)
        )

    def test_absent_metrics_are_omitted_rather_than_sent_as_zero(self) -> None:
        """Verify a timeline-less match funds box-score baselines only.

        Recording an unavailable metric as zero would drag its population
        mean toward zero and make every later match look above average on it.
        """
        names = {sample.metric for sample in rating_samples(_sample_match())}
        self.assertIn("dpm", names)
        self.assertNotIn("gold_diff_10", names)
        self.assertNotIn("bounty_earned_pm", names)

    def test_an_unrateable_match_contributes_nothing(self) -> None:
        """Verify a match version one will not rate cannot pollute the baselines."""
        match = _sample_match()
        match["info"]["gameDuration"] = MIN_RATED_DURATION_S - 1
        self.assertEqual(rating_samples(match, _sample_timeline()), [])
        self.assertEqual(rating_samples({}), [])

    def test_a_match_without_a_version_is_skipped(self) -> None:
        """Verify a payload with no game version yields no patch-keyed samples."""
        match = _sample_match()
        match["info"].pop("gameVersion", None)
        self.assertEqual(rating_samples(match, _sample_timeline()), [])


class NormalisationTests(unittest.TestCase):
    def test_scores_are_stable_when_the_participant_order_changes(self) -> None:
        """Verify that scores are independent of participant serialization order."""
        match = _sample_match()
        timeline = _sample_timeline()
        first = rate_match(match, timeline, baselines=EMPTY_BASELINES)

        shuffled = copy.deepcopy(match)
        shuffled["info"]["participants"].reverse()
        second = rate_match(shuffled, timeline, baselines=EMPTY_BASELINES)

        self.assertEqual(
            {puuid: rating.score for puuid, rating in first.items()},
            {puuid: rating.score for puuid, rating in second.items()},
        )

    def test_win_flag_does_not_change_any_score(self) -> None:
        """Verify win/loss is excluded from the model: flipping it moves only labels."""
        match = _sample_match()
        timeline = _sample_timeline()
        before = rate_match(match, timeline, baselines=EMPTY_BASELINES)

        flipped = copy.deepcopy(match)
        for participant in flipped["info"]["participants"]:
            participant["win"] = not participant["win"]
        after = rate_match(flipped, timeline, baselines=EMPTY_BASELINES)

        self.assertEqual(
            {puuid: rating.score for puuid, rating in before.items()},
            {puuid: rating.score for puuid, rating in after.items()},
        )

    def test_a_metric_the_whole_lobby_ties_on_carries_no_signal(self) -> None:
        """Verify that a metric everyone ties on does not crash or skew scores."""
        match = _sample_match()
        for participant in match["info"]["participants"]:
            participant["visionScore"] = 30
            participant.setdefault("challenges", {})["visionScorePerMinute"] = 1.0
        ratings = rate_match(match, _sample_timeline(), baselines=EMPTY_BASELINES)
        self.assertEqual(len(ratings), 10)


class DegradedInputTests(unittest.TestCase):
    def test_rating_without_a_timeline_still_ranks_the_lobby(self) -> None:
        """Verify that a rating without a timeline still produces ten ratings."""
        match = _sample_match()
        ratings = rate_match(match, baselines=EMPTY_BASELINES)
        self.assertEqual(len(ratings), 10)
        self.assertTrue(all(r.confidence == "Limited" for r in ratings.values()))
        self.assertTrue(
            all("timeline unavailable" in r.notes[0] for r in ratings.values())
        )

    def test_rating_without_challenges_falls_back_to_base_stats(self) -> None:
        """Verify that missing challenges blocks still produce bounded scores."""
        match = _sample_match()
        for participant in match["info"]["participants"]:
            participant.pop("challenges", None)
        ratings = rate_match(match, _sample_timeline(), baselines=EMPTY_BASELINES)
        self.assertEqual(len(ratings), 10)
        self.assertTrue(all(0.0 <= r.score <= 10.0 for r in ratings.values()))

    def test_remakes_are_not_rated(self) -> None:
        """Verify that games shorter than the minimum duration are unrated."""
        match = _sample_match()
        match["info"]["gameDuration"] = MIN_RATED_DURATION_S - 1
        self.assertEqual(rate_match(match, _sample_timeline()), {})

    def test_empty_match_is_not_rated(self) -> None:
        """Verify that an empty or malformed match payload is unrated."""
        self.assertEqual(rate_match({}), {})
        self.assertEqual(rate_match({"info": {"participants": []}}), {})

    def test_aram_is_not_rated(self) -> None:
        """Verify ARAM matches are left unrated in version one."""
        match = _sample_match()
        match["info"]["queueId"] = 450
        self.assertEqual(rate_match(match, _sample_timeline()), {})

    def test_arena_is_not_rated(self) -> None:
        """Verify Arena matches are left unrated in version one."""
        match = _sample_match()
        match["info"]["queueId"] = 1750
        for index, participant in enumerate(match["info"]["participants"]):
            participant["playerSubteamId"] = index // 2 + 1
            participant["teamPosition"] = ""
        self.assertEqual(rate_match(match, _sample_timeline()), {})

    def test_malformed_role_layout_is_not_rated(self) -> None:
        """Verify a match missing a mirrored position is left unrated."""
        match = _sample_match()
        match["info"]["participants"][0]["teamPosition"] = "TOP"
        match["info"]["participants"][1]["teamPosition"] = "TOP"
        self.assertEqual(rate_match(match, _sample_timeline()), {})


if __name__ == "__main__":
    unittest.main()

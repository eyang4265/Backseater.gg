"""Solo-kill classification and lane diffs read off a match timeline."""

import json
import pathlib
import unittest

from bot_app.timeline import (
    LANE_DIFF_TOLERANCE_MS,
    MAX_LEVEL,
    TOP_LANE_MAX_LEVEL,
    LaneDiff,
    MatchTimeline,
    format_diff,
    format_lane_lines,
    format_levels,
    level_at_xp,
    max_level_for_position,
    participant_at_slot,
)


def _frame(timestamp, positions, stats=None, events=()):
    """Handle frame."""
    participant_frames = {}
    for participant_id, (x, y) in positions.items():
        entry = {"position": {"x": x, "y": y}}
        entry.update((stats or {}).get(participant_id, {}))
        participant_frames[str(participant_id)] = entry
    return {
        "timestamp": timestamp,
        "participantFrames": participant_frames,
        "events": list(events),
    }


def _kill(timestamp, killer, victim, x, y, assists=()):
    """Handle kill."""
    return {
        "type": "CHAMPION_KILL",
        "timestamp": timestamp,
        "killerId": killer,
        "victimId": victim,
        "position": {"x": x, "y": y},
        "assistingParticipantIds": list(assists),
    }


class SoloKillTests(unittest.TestCase):
    def _timeline(self, events):
        """Handle timeline."""
        far_away = {1: (0, 0), 2: (100, 100), 3: (50_000, 50_000)}
        return MatchTimeline(
            {
                "info": {
                    "frames": [
                        _frame(0, far_away),
                        _frame(60_000, far_away, events=events),
                    ]
                }
            }
        )

    def test_isolated_kill_counts_for_both_players(self) -> None:
        """Verify that isolated kill counts for both players."""
        timeline = self._timeline([_kill(61_000, killer=1, victim=2, x=0, y=0)])
        self.assertEqual(timeline.solo_kill_stats(1).solo_kills, 1)
        self.assertEqual(timeline.solo_kill_stats(2).solo_deaths, 1)
        self.assertEqual(timeline.solo_kill_stats(1).solo_deaths, 0)

    def test_assisted_kill_is_not_solo(self) -> None:
        """Verify that assisted kill is not solo."""
        timeline = self._timeline([_kill(61_000, 1, 2, 0, 0, assists=[3])])
        self.assertEqual(timeline.solo_kill_stats(1).solo_kills, 0)

    def test_nearby_third_champion_disqualifies_the_kill(self) -> None:
        """Verify that nearby third champion disqualifies the kill."""
        nearby = {1: (0, 0), 2: (100, 100), 3: (200, 200)}
        timeline = MatchTimeline(
            {
                "info": {
                    "frames": [
                        _frame(0, nearby),
                        _frame(60_000, nearby, events=[_kill(61_000, 1, 2, 0, 0)]),
                    ]
                }
            }
        )
        self.assertEqual(timeline.solo_kill_stats(1).solo_kills, 0)

    def test_non_champion_kills_are_ignored(self) -> None:
        """Verify that non champion kills are ignored."""
        event = _kill(61_000, killer=0, victim=2, x=0, y=0)
        self.assertEqual(self._timeline([event]).solo_kill_stats(2).solo_deaths, 0)

    def test_empty_timeline_is_safe(self) -> None:
        """Verify that empty timeline is safe."""
        empty = MatchTimeline({})
        self.assertEqual(empty.solo_kill_stats(1).solo_kills, 0)
        self.assertIsNone(empty.lane_diff_at(1, 2, 600_000))

    def test_no_participant_id_yields_zeroes(self) -> None:
        """Verify that no participant id yields zeroes."""
        self.assertEqual(self._timeline([]).solo_kill_stats(None).solo_kills, 0)


class KillLocationTests(unittest.TestCase):
    def _timeline(self, events):
        """Handle timeline."""
        positions = {1: (0, 0), 2: (100, 100)}
        return MatchTimeline(
            {
                "info": {
                    "frames": [
                        _frame(0, positions),
                        _frame(60_000, positions, events=events),
                    ]
                }
            }
        )

    def test_kills_and_deaths_are_split_by_participant(self) -> None:
        """Verify that kills and deaths are split by participant."""
        timeline = self._timeline(
            [
                _kill(61_000, killer=1, victim=2, x=1_000, y=2_000),
                _kill(62_000, killer=3, victim=1, x=9_000, y=8_000, assists=[4, 5]),
            ]
        )
        kills, deaths = timeline.kills_and_deaths(1)

        self.assertEqual([(event.x, event.y) for event in kills], [(1_000, 2_000)])
        self.assertEqual([(event.x, event.y) for event in deaths], [(9_000, 8_000)])
        self.assertEqual(deaths[0].assist_count, 2)
        self.assertEqual(deaths[0].minute, 1)

    def test_events_are_ordered_oldest_first(self) -> None:
        """Verify that events are ordered oldest first."""
        timeline = self._timeline(
            [
                _kill(300_000, killer=1, victim=2, x=1, y=1),
                _kill(90_000, killer=1, victim=3, x=2, y=2),
            ]
        )
        kills, _ = timeline.kills_and_deaths(1)
        self.assertEqual([event.timestamp for event in kills], [90_000, 300_000])

    def test_kills_without_a_position_are_dropped(self) -> None:
        """Verify that kills without a position are dropped."""
        placeless = {
            "type": "CHAMPION_KILL",
            "timestamp": 61_000,
            "killerId": 1,
            "victimId": 2,
        }
        self.assertEqual(self._timeline([placeless]).champion_kills(), [])

    def test_other_event_types_are_ignored(self) -> None:
        """Verify that other event types are ignored."""
        turret = {
            "type": "BUILDING_KILL",
            "timestamp": 61_000,
            "position": {"x": 5, "y": 5},
        }
        self.assertEqual(self._timeline([turret]).champion_kills(), [])

    def test_no_participant_id_yields_no_events(self) -> None:
        """Verify that no participant id yields no events."""
        timeline = self._timeline([_kill(61_000, killer=1, victim=2, x=1, y=1)])
        self.assertEqual(timeline.kills_and_deaths(None), ([], []))


class LaneDiffTests(unittest.TestCase):
    def _timeline(self, last_timestamp):
        """Handle timeline."""
        stats = {
            1: {
                "totalGold": 5_000,
                "xp": 6_000,
                "minionsKilled": 80,
                "jungleMinionsKilled": 4,
            },
            2: {
                "totalGold": 4_000,
                "xp": 5_500,
                "minionsKilled": 70,
                "jungleMinionsKilled": 0,
            },
        }
        positions = {1: (0, 0), 2: (10, 10)}
        return MatchTimeline(
            {
                "info": {
                    "frames": [
                        _frame(0, positions, stats),
                        _frame(last_timestamp, positions, stats),
                    ]
                }
            }
        )

    def test_diff_is_mine_minus_theirs(self) -> None:
        """Verify that diff is mine minus theirs."""
        diff = self._timeline(600_000).lane_diff_at(1, 2, 600_000)
        self.assertEqual((diff.gold, diff.xp, diff.cs), (1_000, 500, 14))

    def test_level_diff_comes_from_each_side_of_the_curve(self) -> None:
        """Verify that level diff comes from each side of the curve."""
        diff = self._timeline(600_000).lane_diff_at(1, 2, 600_000)
        self.assertAlmostEqual(diff.levels, level_at_xp(6_000) - level_at_xp(5_500))
        self.assertAlmostEqual(diff.levels, 0.463, places=3)

    def test_frame_outside_the_tolerance_is_rejected(self) -> None:
        """Verify that frame outside the tolerance is rejected."""
        timeline = self._timeline(600_000 - LANE_DIFF_TOLERANCE_MS - 1)
        self.assertIsNone(timeline.lane_diff_at(1, 2, 600_000))

    def test_missing_opponent_yields_no_diff(self) -> None:
        """Verify that missing opponent yields no diff."""
        self.assertIsNone(self._timeline(600_000).lane_diff_at(1, None, 600_000))


class LaneDiffSeriesTests(unittest.TestCase):
    def _timeline(self, frame_count, stats=None):
        """Handle timeline."""
        stats = stats or {1: {"totalGold": 5_000}, 2: {"totalGold": 4_000}}
        positions = {1: (0, 0), 2: (10, 10)}
        return MatchTimeline(
            {
                "info": {
                    "frames": [
                        _frame(minute * 60_000, positions, stats)
                        for minute in range(frame_count)
                    ]
                }
            }
        )

    def test_marks_step_by_five_minutes_to_the_end_of_the_game(self) -> None:
        """Verify that marks step by five minutes to the end of the game."""
        series = self._timeline(22).lane_diff_series(1, 2)
        self.assertEqual([minute for minute, _ in series], [5, 10, 15, 20])
        self.assertEqual([diff.gold for _, diff in series], [1_000] * 4)

    def test_short_game_stops_early(self) -> None:
        """Verify that short game stops early."""
        self.assertEqual(
            [minute for minute, _ in self._timeline(8).lane_diff_series(1, 2)], [5]
        )

    def test_game_shorter_than_one_mark_yields_nothing(self) -> None:
        """Verify that game shorter than one mark yields nothing."""
        self.assertEqual(self._timeline(3).lane_diff_series(1, 2), [])

    def test_interval_is_configurable(self) -> None:
        """Verify that interval is configurable."""
        series = self._timeline(16).lane_diff_series(1, 2, interval_ms=600_000)
        self.assertEqual([minute for minute, _ in series], [10])

    def test_a_mark_missing_frame_data_does_not_end_the_series(self) -> None:
        """Verify that a mark missing frame data does not end the series."""
        timeline = self._timeline(22)

        del timeline._frames[10]["participantFrames"]["2"]
        self.assertEqual(
            [minute for minute, _ in timeline.lane_diff_series(1, 2)], [5, 15, 20]
        )

    def test_missing_opponent_yields_an_empty_series(self) -> None:
        """Verify that missing opponent yields an empty series."""
        self.assertEqual(self._timeline(22).lane_diff_series(1, None), [])

    def test_empty_timeline_is_safe(self) -> None:
        """Verify that empty timeline is safe."""
        self.assertEqual(MatchTimeline({}).lane_diff_series(1, 2), [])


_CUMULATIVE_XP = [
    0,
    280,
    660,
    1140,
    1720,
    2400,
    3180,
    4060,
    5040,
    6120,
    7300,
    8580,
    9960,
    11440,
    13020,
    14700,
    16480,
    18360,
    20340,
    22420,
]


class LevelCurveTests(unittest.TestCase):
    def test_the_generated_curve_matches_the_published_table(self) -> None:
        """Verify that the generated curve matches the published table."""
        for level, cumulative in enumerate(_CUMULATIVE_XP, start=1):
            with self.subTest(level=level):
                self.assertEqual(
                    level_at_xp(cumulative, TOP_LANE_MAX_LEVEL), float(level)
                )

    def test_each_level_costs_100_more_than_the_last(self) -> None:
        """Verify that each level costs 100 more than the last."""
        steps = [
            _CUMULATIVE_XP[i] - _CUMULATIVE_XP[i - 1]
            for i in range(1, len(_CUMULATIVE_XP))
        ]
        self.assertEqual(steps, list(range(280, 280 + 100 * len(steps), 100)))

    def test_thresholds_land_on_whole_levels(self) -> None:
        """Verify that thresholds land on whole levels."""
        self.assertEqual(level_at_xp(0), 1.0)
        self.assertEqual(level_at_xp(280), 2.0)
        self.assertEqual(level_at_xp(660), 3.0)
        self.assertEqual(level_at_xp(18_360), float(MAX_LEVEL))

    def test_progress_between_levels_is_fractional(self) -> None:
        """Verify that progress between levels is fractional."""
        self.assertEqual(level_at_xp(470), 2.5)

    def test_the_curve_stretches_as_levels_get_expensive(self) -> None:
        """Verify that the curve stretches as levels get expensive."""
        self.assertLess(level_at_xp(16_290 + 190) - 17.0, 0.15)

    def test_xp_past_the_cap_does_not_add_levels(self) -> None:
        """Verify that xp past the cap does not add levels."""
        self.assertEqual(level_at_xp(30_000), float(MAX_LEVEL))

    def test_missing_or_negative_xp_is_level_one(self) -> None:
        """Verify that missing or negative xp is level one."""
        self.assertEqual(level_at_xp(None), 1.0)
        self.assertEqual(level_at_xp(-5), 1.0)

    def test_top_lane_keeps_levelling_past_the_usual_cap(self) -> None:
        """Verify that top lane keeps levelling past the usual cap."""
        self.assertEqual(level_at_xp(20_665), 18.0)
        self.assertEqual(int(level_at_xp(20_665, TOP_LANE_MAX_LEVEL)), 19)
        self.assertEqual(level_at_xp(22_420, TOP_LANE_MAX_LEVEL), 20.0)
        self.assertEqual(
            level_at_xp(30_000, TOP_LANE_MAX_LEVEL), float(TOP_LANE_MAX_LEVEL)
        )

    def test_the_cap_comes_from_the_role(self) -> None:
        """Verify that the cap comes from the role."""
        self.assertEqual(max_level_for_position("TOP"), TOP_LANE_MAX_LEVEL)
        for position in ("JUNGLE", "MIDDLE", "BOTTOM", "UTILITY", "", None):
            self.assertEqual(max_level_for_position(position), MAX_LEVEL)


_SAMPLES = pathlib.Path(__file__).resolve().parent.parent / "json" / "samples"


@unittest.skipUnless(
    (_SAMPLES / "sample_timeline.json").exists(), "sample match data is missing"
)
class LevelCurveAgainstRiotTests(unittest.TestCase):
    """The curve has to agree with the ``level`` Riot puts in every frame.

    This is the check that pins the top-lane cap down: a flat cap of 18
    disagrees with the sample in 15 places, all of them top laners.
    """

    def test_every_frame_matches_riots_own_level(self) -> None:
        """Verify that every frame matches riots own level."""
        match = json.loads((_SAMPLES / "sample_match.json").read_text())
        timeline = json.loads((_SAMPLES / "sample_timeline.json").read_text())
        caps = {
            str(p["participantId"]): max_level_for_position(p.get("teamPosition"))
            for p in match["info"]["participants"]
        }

        checked = 0
        for frame in timeline["info"]["frames"]:
            for participant_id, participant_frame in frame["participantFrames"].items():
                expected = participant_frame.get("level")
                if expected is None:
                    continue
                derived = int(
                    level_at_xp(participant_frame.get("xp", 0), caps[participant_id])
                )
                self.assertEqual(
                    derived,
                    expected,
                    f"participant {participant_id} at {participant_frame.get('xp')} XP",
                )
                checked += 1
        self.assertGreater(checked, 100)

    def test_only_top_laners_pass_eighteen(self) -> None:
        """Verify that only top laners pass eighteen."""
        match = json.loads((_SAMPLES / "sample_match.json").read_text())
        timeline = json.loads((_SAMPLES / "sample_timeline.json").read_text())
        positions = {
            str(p["participantId"]): p.get("teamPosition")
            for p in match["info"]["participants"]
        }

        past_cap = {
            positions[participant_id]
            for frame in timeline["info"]["frames"]
            for participant_id, participant_frame in frame["participantFrames"].items()
            if (participant_frame.get("level") or 0) > MAX_LEVEL
        }
        # Shorter sample matches may not reach level 19; if any participant
        # exceeds the normal cap, only the top lane may do so.
        self.assertTrue(past_cap.issubset({"TOP"}))


class FormatDiffTests(unittest.TestCase):
    def test_sign_and_grouping(self) -> None:
        """Verify that sign and grouping."""
        self.assertEqual(format_diff(1234), "+1,234")
        self.assertEqual(format_diff(-58), "-58")
        self.assertEqual(format_diff(0), "+0")

    def test_levels_show_one_decimal_with_a_sign(self) -> None:
        """Verify that levels show one decimal with a sign."""
        self.assertEqual(format_levels(2.5), "+2.5")
        self.assertEqual(format_levels(-1.24), "-1.2")
        self.assertEqual(format_levels(0.0), "+0.0")

    def test_a_hair_below_zero_is_not_negative_zero(self) -> None:
        """Verify that a hair below zero is not negative zero."""
        self.assertEqual(format_levels(-0.01), "+0.0")


class SlotLookupTests(unittest.TestCase):
    @staticmethod
    def _match():
        """Handle match."""
        roles = ["TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY"]
        return {
            "info": {
                "participants": [
                    {
                        "participantId": slot,
                        "teamId": 100 if slot <= 5 else 200,
                        "teamPosition": roles[(slot - 1) % 5],
                    }
                    for slot in range(1, 11)
                ]
            }
        }

    def test_slot_one_is_blue_top_and_six_is_red_top(self) -> None:
        """Verify that slot one is blue top and six is red top."""
        blue_top = participant_at_slot(self._match(), 1)
        red_top = participant_at_slot(self._match(), 6)
        self.assertEqual((blue_top["teamId"], blue_top["teamPosition"]), (100, "TOP"))
        self.assertEqual((red_top["teamId"], red_top["teamPosition"]), (200, "TOP"))

    def test_slots_run_top_to_support_down_each_side(self) -> None:
        """Verify that slots run top to support down each side."""
        match = self._match()
        self.assertEqual(
            [participant_at_slot(match, slot)["teamPosition"] for slot in range(1, 11)],
            ["TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY"] * 2,
        )
        self.assertEqual(
            [participant_at_slot(match, slot)["teamId"] for slot in range(1, 11)],
            [100] * 5 + [200] * 5,
        )

    def test_a_slot_the_match_does_not_have_is_none(self) -> None:
        """Verify that a slot the match does not have is none."""
        self.assertIsNone(participant_at_slot(self._match(), 11))
        self.assertIsNone(participant_at_slot(self._match(), 0))

    def test_no_slot_asked_for_is_none(self) -> None:
        """Verify that no slot asked for is none."""
        self.assertIsNone(participant_at_slot(self._match(), None))

    def test_an_empty_match_is_safe(self) -> None:
        """Verify that an empty match is safe."""
        self.assertIsNone(participant_at_slot({}, 1))


class LaneLineTests(unittest.TestCase):
    def test_one_line_per_mark(self) -> None:
        """Verify that one line per mark."""
        lines = format_lane_lines(
            [
                (10, LaneDiff(gold=-321, xp=0, levels=-0.5, cs=-7)),
                (15, LaneDiff(gold=-592, xp=0, levels=-0.4, cs=-7)),
            ]
        )
        self.assertEqual(
            lines.split("\n"),
            [
                "@10 min — Gold -321 | Levels -0.5 | CS -7",
                "@15 min — Gold -592 | Levels -0.4 | CS -7",
            ],
        )

    def test_large_numbers_keep_their_grouping(self) -> None:
        """Verify that large numbers keep their grouping."""
        lines = format_lane_lines([(30, LaneDiff(gold=6_780, xp=0, levels=3.2, cs=54))])
        self.assertEqual(lines, "@30 min — Gold +6,780 | Levels +3.2 | CS +54")

    def test_nothing_is_padded(self) -> None:
        """Verify that nothing is padded."""
        series = [
            (minute, LaneDiff(gold=1, xp=0, levels=0.1, cs=1)) for minute in (5, 40)
        ]
        first, second = format_lane_lines(series).split("\n")
        self.assertTrue(first.startswith("@5 min — Gold +1 |"))
        self.assertTrue(second.startswith("@40 min — Gold +1 |"))

    def test_an_empty_series_leaves_the_wording_to_the_caller(self) -> None:
        """Verify that an empty series leaves the wording to the caller."""
        self.assertEqual(format_lane_lines([]), "")


if __name__ == "__main__":
    unittest.main()

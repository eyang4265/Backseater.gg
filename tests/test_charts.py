"""Chart fallbacks that must work without matplotlib."""

import unittest
from unittest.mock import patch

from bot_app.charts import (
    _BOT_SIDE_BOUNDARY,
    _BOT_SCUTTLE_SPAWN,
    _TOP_SIDE_BOUNDARY,
    _TOP_SCUTTLE_SPAWN,
    _in_base,
    _in_mid_corridor,
    _lane_weights,
    _lp_point_labels,
    build_laning_comparison_chart,
    build_lp_chart,
    jungle_checkpoint_minutes,
    jungle_heatmap_density_points,
    jungle_heatmap_takedowns,
    jungle_kda_at,
    jungle_kill_positions,
    jungle_lane_involvement,
    jungle_path_points,
    jungle_proximity_breakdown,
    jungle_proximity_percentages,
    jungle_position_sample_timestamps,
    jungle_position_samples,
)


class LpChartTests(unittest.TestCase):
    def test_points_use_compact_rank_and_lp_labels(self) -> None:
        """Format points like the rank-history reference chart."""
        self.assertEqual(("G 1", "29LP"), _lp_point_labels(1529))
        self.assertEqual(("P 4", "0LP"), _lp_point_labels(1600))

    def test_missing_matplotlib_returns_none(self) -> None:
        """Verify that missing matplotlib returns none."""
        with patch("bot_app.charts.MATPLOTLIB_AVAILABLE", False):
            self.assertIsNone(build_lp_chart([{"t": 1, "v": 100}], days=30))


class LaningComparisonChartTests(unittest.TestCase):
    def test_missing_matplotlib_returns_none(self) -> None:
        """Verify that missing matplotlib returns none."""
        checkpoints = [(5, {"you": {"Gold": 2000, "XP": 1800}, "opponent": {"Gold": 1700, "XP": 1600}})]
        with patch("bot_app.charts.MATPLOTLIB_AVAILABLE", False):
            self.assertIsNone(build_laning_comparison_chart(checkpoints))

    def test_empty_checkpoints_returns_none(self) -> None:
        """Verify that an empty checkpoint list returns none rather than an empty chart."""
        self.assertIsNone(build_laning_comparison_chart([]))

    def test_renders_a_chart_with_missing_and_present_checkpoints(self) -> None:
        """Verify a mix of missing and present sides renders without error."""
        checkpoints = [
            (5, {"you": {"Gold": 2000, "XP": 1800}, "opponent": {"Gold": 1700, "XP": 1600}}),
            (10, {"you": None, "opponent": None}),
            (15, {"you": {"Gold": 6600, "XP": 6100}, "opponent": {"Gold": 5900, "XP": 5700}}),
        ]
        chart = build_laning_comparison_chart(checkpoints)
        self.assertIsNotNone(chart)
        self.assertEqual("laning.png", chart.filename)


class JungleInvolvementTests(unittest.TestCase):
    def test_mid_corridor_uses_requested_scuttle_order(self) -> None:
        """Keep the Mid corridor between the requested bot/top boundary routes."""
        # The two paths retain their forward blue-Nexus-to-red-Nexus order.
        # The initial shrines are at (10,000, 5,000) and (5,000, 10,000),
        # rather than the river's central patrol locations.
        self.assertEqual((10_000, 5_000), _BOT_SCUTTLE_SPAWN)
        self.assertEqual((5_000, 10_000), _TOP_SCUTTLE_SPAWN)
        self.assertEqual(
            ((0, 0), (7050, 4000), _BOT_SCUTTLE_SPAWN, (10600, 6400), (14500, 14500)),
            _BOT_SIDE_BOUNDARY,
        )
        self.assertEqual(
            ((0, 0), (3870, 7900), _TOP_SCUTTLE_SPAWN, (7450, 10500), (14500, 14500)),
            _TOP_SIDE_BOUNDARY,
        )
        self.assertTrue(_in_mid_corridor(7500, 7000))
        self.assertTrue(_in_mid_corridor(8_000, 6_000))
        self.assertTrue(_in_mid_corridor(6_000, 8_000))
        self.assertFalse(_in_mid_corridor(3000, 11000))

    def test_fountain_samples_are_excluded_and_marker_timestamps_stay_aligned(self) -> None:
        """Drop base time without desynchronizing heatmap marker timestamps."""
        timeline = {"info": {"mapId": 11, "frames": [
            {"timestamp": 0, "participantFrames": {
                "1": {"position": {"x": 400, "y": 400}}
            }},
            {"timestamp": 60_000, "participantFrames": {
                "1": {"position": {"x": 1_000, "y": 13_000}}
            }},
            {"timestamp": 120_000, "participantFrames": {
                "1": {"position": {"x": 14_400, "y": 14_400}}
            }},
            {"timestamp": 180_000, "participantFrames": {
                "1": {"position": {"x": 13_000, "y": 1_000}}
            }},
        ]}}

        samples = jungle_position_samples(timeline, 1)
        timestamps = jungle_position_sample_timestamps(timeline, 1)

        self.assertTrue(_in_base(400, 400))
        self.assertFalse(_in_base(1_000, 13_000))
        self.assertEqual([1, 2], [number for number, *_ in samples])
        self.assertEqual([(1, 60_000), (2, 180_000)], timestamps)
        self.assertEqual(set(dict(timestamps)), {number for number, *_ in samples})

    def test_boundary_lane_weights_blend_adjacent_lanes(self) -> None:
        """Split proximity evenly across the two lanes on a route boundary."""
        weights = _lane_weights(2_100, 8_400)

        self.assertEqual({"Top": 1.0, "Mid": 0.0, "Bottom": 0.0}, weights)
        self.assertEqual(
            {"Top": 0.0, "Mid": 1.0, "Bottom": 0.0},
            _lane_weights(7_500, 7_000),
        )
        self.assertEqual(
            {"Top": 0.0, "Mid": 0.0, "Bottom": 1.0},
            _lane_weights(13_000, 1_000),
        )
        self.assertEqual(
            {"Top": 0.5, "Mid": 0.5, "Bottom": 0.0},
            _lane_weights(*_TOP_SCUTTLE_SPAWN),
        )
        self.assertEqual(
            {"Top": 0.0, "Mid": 0.5, "Bottom": 0.5},
            _lane_weights(*_BOT_SCUTTLE_SPAWN),
        )

    def test_proximity_is_invariant_to_position_sampling_rate(self) -> None:
        """Represent the same route at one-minute and one-second resolution."""
        def make_timeline(interval: int) -> dict:
            frames = []
            for timestamp in range(0, 120_001, interval):
                position = (
                    {"x": 1_000, "y": 13_000}
                    if timestamp < 60_000
                    else {"x": 13_000, "y": 1_000}
                )
                frames.append({
                    "timestamp": timestamp,
                    "participantFrames": {"1": {"position": position}},
                })
            return {"info": {"frameInterval": interval, "frames": frames}}

        coarse = jungle_proximity_percentages(make_timeline(60_000), 1, max_minutes=2)
        fine = jungle_proximity_percentages(make_timeline(1_000), 1, max_minutes=2)

        for lane in coarse:
            self.assertAlmostEqual(coarse[lane], fine[lane], delta=1.0)

    def test_empty_involvement_renormalizes_scores_to_one_hundred(self) -> None:
        """Use the full presence channel when no fights occurred."""
        timeline = {"info": {"frameInterval": 60_000, "frames": [{
            "timestamp": 0,
            "participantFrames": {"1": {"position": {"x": 1_000, "y": 13_000}}},
        }]}}

        scores = jungle_proximity_percentages(timeline, 1, max_minutes=1)

        self.assertAlmostEqual(100.0, sum(scores.values()))

    def test_empty_presence_renormalizes_scores_to_one_hundred(self) -> None:
        """Use the full involvement channel when every sample is in base."""
        timeline = {"info": {"frameInterval": 60_000, "frames": [{
            "timestamp": 0,
            "participantFrames": {"1": {"position": {"x": 400, "y": 400}}},
            "events": [{
                "type": "CHAMPION_KILL", "timestamp": 30_000,
                "killerId": 1, "victimId": 2,
                "position": {"x": 1_000, "y": 13_000},
            }],
        }]}}

        breakdown = jungle_proximity_breakdown(timeline, 1, max_minutes=1)

        self.assertEqual(0.0, sum(lane["presence"] for lane in breakdown.values()))
        self.assertAlmostEqual(100.0, sum(lane["score"] for lane in breakdown.values()))

    def test_death_contributes_to_lane_involvement(self) -> None:
        """Treat a jungler death as fight involvement where it occurred."""
        timeline = {"info": {"frames": [{
            "timestamp": 60_000,
            "events": [{
                "type": "CHAMPION_KILL", "killerId": 2, "victimId": 1,
                "position": {"x": 1_000, "y": 13_000},
            }],
        }]}}

        breakdown = jungle_proximity_breakdown(timeline, 1, max_minutes=5)
        counts = jungle_lane_involvement(timeline, 1, [], max_minutes=5)

        self.assertGreater(breakdown["Top"]["involvement"], 99.0)
        self.assertEqual(1, counts["Top"]["deaths"])

    def test_camp_only_activity_reports_hover_as_presence(self) -> None:
        """Expose camp-derived proximity without inventing fight involvement."""
        timeline = {"info": {"frames": [{
            "timestamp": 60_000,
            "events": [{
                "type": "MONSTER_KILL", "killerId": 1,
                "position": {"x": 2_100, "y": 8_400},
            }],
        }]}}

        breakdown = jungle_proximity_breakdown(timeline, 1, max_minutes=5)

        for lane in breakdown.values():
            self.assertAlmostEqual(lane["presence"], lane["hover"])
            self.assertEqual(0.0, lane["involvement"])

    def test_position_samples_skip_first_then_number_by_timestamp(self) -> None:
        """Ignore the first sample and number remaining hexagons chronologically."""
        timeline = {
            "info": {
                "frames": [
                    {"timestamp": 120_000, "participantFrames": {
                        "1": {"position": {"x": 14_000, "y": 1_000}}
                    }},
                    {"timestamp": 0, "participantFrames": {
                        "1": {"position": {"x": 500, "y": 500}}
                    }},
                    {"timestamp": 60_000, "participantFrames": {
                        "1": {"position": {"x": 6_000, "y": 6_000}}
                    }},
                ]
            }
        }

        self.assertEqual(
            [(1, 6_000, 6_000, "Mid"), (2, 14_000, 1_000, "Bottom")],
            jungle_position_samples(timeline, 1),
        )

    def test_heatmap_numbers_every_point_after_the_ignored_start(self) -> None:
        """Every post-spawn movement sample is numbered by default (no cap)."""
        timeline = {"info": {"frames": [
            {"timestamp": minute * 60_000, "participantFrames": {
                "1": {"position": {"x": 6_000 + minute * 100, "y": 6_000 + minute * 100}}
            }}
            for minute in range(8)
        ]}}

        samples = jungle_position_samples(timeline, 1)

        self.assertEqual([1, 2, 3, 4, 5, 6, 7], [number for number, *_ in samples])
        self.assertEqual((6_700, 6_700), samples[-1][1:3])

    def test_heatmap_limit_still_truncates_when_explicitly_passed(self) -> None:
        """An explicit ``limit`` still caps the numbered markers."""
        timeline = {"info": {"frames": [
            {"timestamp": minute * 60_000, "participantFrames": {
                "1": {"position": {"x": 6_000 + minute * 100, "y": 6_000 + minute * 100}}
            }}
            for minute in range(8)
        ]}}

        samples = jungle_position_samples(timeline, 1, limit=5)

        self.assertEqual([1, 2, 3, 4, 5], [number for number, *_ in samples])
        self.assertEqual((6_500, 6_500), samples[-1][1:3])

    def test_sample_timeline_hexagon_timestamps(self) -> None:
        """Read every post-spawn timestamp from the sample timeline by default."""
        import json
        from pathlib import Path

        timeline = json.loads(
            (Path(__file__).parents[1] / "json/samples/sample_timeline.json").read_text()
        )

        timestamps = jungle_position_sample_timestamps(timeline, 2)

        self.assertEqual(
            [(1, 60_026), (2, 120_046), (3, 180_109), (4, 240_125), (5, 300_145)],
            timestamps[:5],
        )
        self.assertGreater(len(timestamps), 5)

    def test_kill_positions_are_cut_off_and_numbered_chronologically(self) -> None:
        """Return only valid jungler kill positions in chronological order."""
        timeline = {
            "info": {
                "frames": [{
                    "timestamp": 1_200_100,
                    "events": [
                        {"type": "CHAMPION_KILL", "timestamp": 900_000, "killerId": 1,
                         "position": {"x": 900, "y": 901}},
                        {"type": "CHAMPION_KILL", "timestamp": 600_000, "killerId": 1,
                         "position": {"x": 600, "y": 601}},
                        {"type": "CHAMPION_KILL", "timestamp": 1_200_001, "killerId": 1,
                         "position": {"x": 1200, "y": 1201}},
                        {"type": "CHAMPION_KILL", "timestamp": 500_000, "killerId": 2,
                         "position": {"x": 500, "y": 501}},
                    ],
                }]
            }
        }

        self.assertEqual([(600, 601), (900, 901)], jungle_kill_positions(timeline, 1, 20))

    def test_kills_contribute_to_heatmap_density(self) -> None:
        """Weight kill locations as part of the heatmap density."""
        timeline = {
            "info": {
                "frames": [
                    {"timestamp": 0, "participantFrames": {
                        "1": {"position": {"x": 6_000, "y": 6_000}}
                    }},
                    {"timestamp": 60_000, "participantFrames": {
                        "1": {"position": {"x": 6_100, "y": 6_100}}
                    }, "events": [{
                        "type": "CHAMPION_KILL", "timestamp": 60_000,
                        "killerId": 1, "position": {"x": 6_200, "y": 6_200},
                    }]},
                ]
            }
        }

        self.assertEqual(
            [(6_100, 6_100), *((6_200, 6_200),) * 5],
            jungle_heatmap_density_points(timeline, 1),
        )

    def test_heatmap_marks_kills_and_assists_through_fifteen_minutes(self) -> None:
        """Include positioned kills and assists through the 15-minute window."""
        timeline = {"info": {"frames": [
            {"timestamp": minute * 60_000, "participantFrames": {
                "1": {"position": {"x": minute * 100, "y": minute * 100}}
            }, "events": ([{
                "type": "CHAMPION_KILL", "timestamp": 90_000,
                "killerId": 1, "position": {"x": 900, "y": 901},
            }] if minute == 2 else [{
                "type": "CHAMPION_KILL", "timestamp": 210_000,
                "killerId": 2, "assistingParticipantIds": [1],
                "position": {"x": 2_100, "y": 2_101},
            }] if minute == 4 else [{
                "type": "CHAMPION_KILL", "timestamp": 300_000,
                "killerId": 1, "position": {"x": 3_000, "y": 3_001},
            }] if minute == 5 else [])}
            for minute in range(6)
        ]}}

        self.assertEqual(
            [
                ("kill", "Mid", 90_000, 900, 901),
                ("assist", "Mid", 210_000, 2_100, 2_101),
                ("kill", "Mid", 300_000, 3_000, 3_001),
            ],
            jungle_heatmap_takedowns(timeline, 1),
        )

    def test_heatmap_includes_jungler_deaths_through_fifteen_minutes(self) -> None:
        """Include a jungler death and retain events through the 15-minute window."""
        timeline = {"info": {"frames": [
            {"timestamp": minute * 60_000, "participantFrames": {
                "1": {"position": {"x": minute * 100, "y": minute * 100}}
            }, "events": ([{
                "type": "CHAMPION_KILL", "timestamp": 14 * 60_000,
                "killerId": 2, "victimId": 1,
                "position": {"x": 1_400, "y": 1_401},
            }] if minute == 14 else [])}
            for minute in range(16)
        ]}}

        self.assertEqual(
            [("death", "Mid", 14 * 60_000, 1_400, 1_401)],
            jungle_heatmap_takedowns(timeline, 1, max_minutes=15),
        )

    def test_path_interleaves_kills_between_position_samples(self) -> None:
        """Route position two through a kill before continuing to position three."""
        # Coordinates are offset well outside the fountain radius around
        # (400, 400) so this test isn't incidentally exercising base filtering.
        timeline = {
            "info": {
                "frames": [
                    {"timestamp": 0, "participantFrames": {
                        "1": {"position": {"x": 5100, "y": 5100}}
                    }},
                    {"timestamp": 60_000, "participantFrames": {
                        "1": {"position": {"x": 5200, "y": 5200}}
                    }},
                    {"timestamp": 120_000, "participantFrames": {
                        "1": {"position": {"x": 5400, "y": 5400}}
                    }, "events": [{
                        "type": "CHAMPION_KILL", "timestamp": 90_000,
                        "killerId": 1, "position": {"x": 5300, "y": 5300},
                    }]},
                ]
            }
        }

        self.assertEqual(
            [(5200, 5200), (5300, 5300), (5400, 5400)],
            jungle_path_points(timeline, 1),
        )

    def test_path_excludes_base_positions_like_position_samples_does(self) -> None:
        """A recall back to base shouldn't route the path through the fountain."""
        timeline = {
            "info": {
                "frames": [
                    {"timestamp": 0, "participantFrames": {
                        "1": {"position": {"x": 5100, "y": 5100}}
                    }},
                    {"timestamp": 60_000, "participantFrames": {
                        "1": {"position": {"x": 400, "y": 400}}
                    }},
                    {"timestamp": 120_000, "participantFrames": {
                        "1": {"position": {"x": 5400, "y": 5400}}
                    }},
                ]
            }
        }

        self.assertTrue(_in_base(400, 400))
        self.assertEqual(
            [(5400, 5400)],
            jungle_path_points(timeline, 1),
        )

    def test_checkpoints_continue_through_match(self) -> None:
        """Generate every completed five-minute checkpoint through match end."""
        self.assertEqual(
            (5, 10, 15, 20, 25, 30, 35),
            jungle_checkpoint_minutes(37 * 60 + 42),
        )

    def test_kda_uses_event_timestamp_inside_later_frame(self) -> None:
        """Include pre-cutoff events stored in a frame whose timestamp is later."""
        timeline = {
            "info": {
                "frames": [{
                    "timestamp": 900_296,
                    "events": [
                        {"type": "CHAMPION_KILL", "timestamp": 899_000, "killerId": 1, "victimId": 6},
                        {"type": "CHAMPION_KILL", "timestamp": 901_000, "killerId": 1, "victimId": 6},
                    ],
                }]
            }
        }

        self.assertEqual((1, 0, 0), jungle_kda_at(timeline, 1, max_minutes=15))

    def test_assigns_enemy_jungler_fights_to_joining_lane(self) -> None:
        """Assign enemy-jungle takedowns to the allied lane that joined them."""
        timeline = {
            "info": {
                "frames": [{
                    "timestamp": 300_000,
                    "events": [{
                        "type": "CHAMPION_KILL", "killerId": 1, "victimId": 6,
                        "assistingParticipantIds": [2],
                    }, {
                        "type": "CHAMPION_KILL", "killerId": 2, "victimId": 6,
                        "assistingParticipantIds": [1],
                    }],
                }]
            }
        }
        participants = [
            {"participantId": 1, "teamPosition": "JUNGLE"},
            {"participantId": 2, "teamPosition": "TOP"},
            {"participantId": 6, "teamPosition": "JUNGLE"},
        ]

        involvement = jungle_lane_involvement(timeline, 1, participants, max_minutes=5)

        self.assertEqual(
            {"kills": 1, "assists": 1, "deaths": 0}, involvement["Top"]
        )
        self.assertEqual(1, sum(lane["kills"] for lane in involvement.values()))
        self.assertEqual(1, sum(lane["assists"] for lane in involvement.values()))

    def test_prefers_fight_location_over_participant_role(self) -> None:
        """Use the map location when it disagrees with the victim's lane role."""
        timeline = {"info": {"frames": [{
            "timestamp": 300_000,
            "events": [{
                "type": "CHAMPION_KILL", "killerId": 1, "victimId": 6,
                "position": {"x": 1_000, "y": 13_000},
            }],
        }]}}
        participants = [
            {"participantId": 1, "teamPosition": "JUNGLE"},
            {"participantId": 6, "teamPosition": "BOTTOM"},
        ]

        involvement = jungle_lane_involvement(timeline, 1, participants, max_minutes=5)

        self.assertEqual(1, involvement["Top"]["kills"])
        self.assertEqual(0, involvement["Bottom"]["kills"])

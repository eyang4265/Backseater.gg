"""Laning-phase counter-pick row extraction: filters, window, roles."""

import copy
import unittest

from bot_app.lane_matchups.extract import (
    LANE_WINDOW_MS,
    LaneOutcome,
    lane_outcomes,
)


def _participant(pid, team_id, position, champion_id, **overrides):
    """Handle participant."""
    base = {
        "participantId": pid,
        "teamId": team_id,
        "teamPosition": position,
        "championId": champion_id,
        "championName": f"Champ{champion_id}",
        "gameEndedInEarlySurrender": False,
    }
    base.update(overrides)
    return base


def _match(participants, *, queue_id=420, duration=1200, version="14.16.567.1234"):
    """Handle match."""
    return {
        "info": {
            "queueId": queue_id,
            "gameDuration": duration,
            "gameVersion": version,
            "platformId": "NA1",
            "participants": participants,
        }
    }


def _frame(timestamp, golds, xps=None, cs=None, events=()):
    """One timeline frame: golds is {participantId: totalGold}."""
    xps = xps or {}
    cs = cs or {}
    return {
        "timestamp": timestamp,
        "participantFrames": {
            str(pid): {
                "totalGold": gold,
                "xp": xps.get(pid, 1000),
                "minionsKilled": cs.get(pid, 50),
                "jungleMinionsKilled": 0,
                "position": {"x": 5000, "y": 5000},
            }
            for pid, gold in golds.items()
        },
        "events": list(events),
    }


def _base_participants():
    """Ten participants with valid, distinct roles on both sides."""
    positions = ["TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY"]
    participants = []
    champ_id = 1
    for team_id in (100, 200):
        for pid_offset, position in enumerate(positions):
            pid = (0 if team_id == 100 else 5) + pid_offset + 1
            participants.append(_participant(pid, team_id, position, champ_id))
            champ_id += 1
    return participants


def _base_timeline(gold_by_pid=None):
    """A minimal two-frame timeline covering the 14-minute mark."""
    default_gold = {pid: 5000 for pid in range(1, 11)}
    if gold_by_pid:
        default_gold.update(gold_by_pid)
    return {
        "info": {
            "frames": [
                _frame(0, {pid: 500 for pid in range(1, 11)}),
                _frame(LANE_WINDOW_MS, default_gold),
                _frame(LANE_WINDOW_MS + 5 * 60_000, default_gold),
            ]
        }
    }


class SampleConstructionFilterTests(unittest.TestCase):
    def test_wrong_queue_is_excluded(self) -> None:
        """Verify that wrong queue is excluded."""
        match = _match(_base_participants(), queue_id=440)
        self.assertEqual(lane_outcomes(match, _base_timeline()), [])

    def test_short_game_is_excluded(self) -> None:
        """Verify that short game is excluded."""
        match = _match(_base_participants(), duration=600)
        self.assertEqual(lane_outcomes(match, _base_timeline()), [])

    def test_remake_is_excluded(self) -> None:
        """Verify that remake is excluded."""
        participants = _base_participants()
        participants[0]["gameEndedInEarlySurrender"] = True
        match = _match(participants)
        self.assertEqual(lane_outcomes(match, _base_timeline()), [])

    def test_missing_role_assignment_is_excluded(self) -> None:
        """Verify that missing role assignment is excluded."""
        participants = _base_participants()
        participants[0]["teamPosition"] = ""
        match = _match(participants)
        self.assertEqual(lane_outcomes(match, _base_timeline()), [])

    def test_duplicate_role_on_one_side_is_excluded(self) -> None:
        """Verify that duplicate role on one side is excluded."""
        participants = _base_participants()
        participants[0]["teamPosition"] = "MIDDLE"
        match = _match(participants)
        self.assertEqual(lane_outcomes(match, _base_timeline()), [])

    def test_missing_timeline_is_excluded(self) -> None:
        """Verify that missing timeline is excluded."""
        match = _match(_base_participants())
        self.assertEqual(lane_outcomes(match, None), [])

    def test_valid_game_produces_one_row_per_role_except_jungle(self) -> None:
        """Verify that valid game produces one row per role except jungle."""
        match = _match(_base_participants())
        outcomes = lane_outcomes(match, _base_timeline())
        # 4 rows/side (TOP, MIDDLE, BOTTOM, UTILITY) * 2 sides = 8 rows.
        self.assertEqual(len(outcomes), 8)
        self.assertNotIn("JUNGLE", {o.position for o in outcomes})


class DirectRoleOutcomeTests(unittest.TestCase):
    def test_gold_diff_is_antisymmetric_between_lane_opponents(self) -> None:
        """Verify that gold diff is antisymmetric between lane opponents."""
        participants = _base_participants()
        match = _match(participants)
        timeline = _base_timeline({1: 6000, 6: 5000})  # TOP blue vs TOP red
        outcomes = lane_outcomes(match, timeline)
        top_blue = next(o for o in outcomes if o.position == "TOP" and o.team_id == 100)
        top_red = next(o for o in outcomes if o.position == "TOP" and o.team_id == 200)
        self.assertAlmostEqual(top_blue.gold_diff, -top_red.gold_diff)
        self.assertAlmostEqual(top_blue.gold_diff, 1000.0)

    def test_champion_and_opponent_ids_are_mirrored(self) -> None:
        """Verify that champion and opponent ids are mirrored across the pair."""
        match = _match(_base_participants())
        outcomes = lane_outcomes(match, _base_timeline())
        top_blue = next(o for o in outcomes if o.position == "TOP" and o.team_id == 100)
        top_red = next(o for o in outcomes if o.position == "TOP" and o.team_id == 200)
        self.assertEqual(top_blue.champion_id, top_red.opponent_id)
        self.assertEqual(top_red.champion_id, top_blue.opponent_id)

    def test_solo_kill_diff_counts_only_the_pair_before_the_window(self) -> None:
        """Verify that solo kill diff counts only the pair before the window."""
        participants = _base_participants()
        match = _match(participants)
        timeline_payload = _base_timeline()
        timeline_payload["info"]["frames"][0]["events"] = [
            {
                "type": "CHAMPION_KILL",
                "timestamp": 60_000,
                "killerId": 1,  # TOP blue
                "victimId": 6,  # TOP red
                "position": {"x": 1000, "y": 1000},
                "assistingParticipantIds": [],
            },
            {
                # Outside the window: must not count.
                "type": "CHAMPION_KILL",
                "timestamp": LANE_WINDOW_MS + 60_000,
                "killerId": 6,
                "victimId": 1,
                "position": {"x": 1000, "y": 1000},
                "assistingParticipantIds": [],
            },
            {
                # Assisted: must not count as solo.
                "type": "CHAMPION_KILL",
                "timestamp": 90_000,
                "killerId": 1,
                "victimId": 6,
                "position": {"x": 1000, "y": 1000},
                "assistingParticipantIds": [2],
            },
        ]
        outcomes = lane_outcomes(match, timeline_payload)
        top_blue = next(o for o in outcomes if o.position == "TOP" and o.team_id == 100)
        self.assertEqual(top_blue.solo_kill_diff, 1.0)

    def test_no_frame_at_the_window_excludes_that_row(self) -> None:
        """Verify that a game ending before 14 minutes drops the row entirely."""
        participants = _base_participants()
        match = _match(participants, duration=901)
        timeline = {
            "info": {
                "frames": [
                    _frame(0, {pid: 500 for pid in range(1, 11)}),
                    _frame(901_000, {pid: 5000 for pid in range(1, 11)}),
                ]
            }
        }
        self.assertEqual(lane_outcomes(match, timeline), [])


class UtilityPairOutcomeTests(unittest.TestCase):
    def test_utility_uses_pair_gold_and_omits_cs(self) -> None:
        """Verify that utility uses pair gold and omits cs."""
        match = _match(_base_participants())
        # BOTTOM=4/9, UTILITY=5/10 for blue/red respectively.
        timeline = _base_timeline({4: 5500, 5: 5200, 9: 5000, 10: 5000})
        outcomes = lane_outcomes(match, timeline)
        support_blue = next(
            o for o in outcomes if o.position == "UTILITY" and o.team_id == 100
        )
        self.assertTrue(support_blue.is_pair_metric)
        self.assertIsNone(support_blue.cs_diff)
        self.assertAlmostEqual(support_blue.gold_diff, 700.0)

    def test_utility_gold_diff_is_antisymmetric(self) -> None:
        """Verify that utility gold diff is antisymmetric between sides."""
        match = _match(_base_participants())
        timeline = _base_timeline({4: 5500, 5: 5200, 9: 5000, 10: 5000})
        outcomes = lane_outcomes(match, timeline)
        support_blue = next(
            o for o in outcomes if o.position == "UTILITY" and o.team_id == 100
        )
        support_red = next(
            o for o in outcomes if o.position == "UTILITY" and o.team_id == 200
        )
        self.assertAlmostEqual(support_blue.gold_diff, -support_red.gold_diff)

    def test_direct_role_rows_are_not_flagged_as_pair_metrics(self) -> None:
        """Verify that direct role rows are not flagged as pair metrics."""
        match = _match(_base_participants())
        outcomes = lane_outcomes(match, _base_timeline())
        for outcome in outcomes:
            if outcome.position != "UTILITY":
                self.assertFalse(outcome.is_pair_metric)


class PatchParsingTests(unittest.TestCase):
    def test_patch_is_major_minor_from_game_version(self) -> None:
        """Verify that patch is major minor from game version."""
        match = _match(_base_participants(), version="14.16.567.1234")
        outcomes = lane_outcomes(match, _base_timeline())
        self.assertTrue(all(o.patch == "14.16" for o in outcomes))

    def test_missing_version_excludes_the_match(self) -> None:
        """Verify that missing version excludes the match."""
        match = _match(_base_participants(), version=None)
        self.assertEqual(lane_outcomes(match, _base_timeline()), [])


if __name__ == "__main__":
    unittest.main()

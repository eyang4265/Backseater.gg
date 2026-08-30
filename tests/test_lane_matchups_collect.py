"""Snowball harvester: dedupe, seeding, and request-shape, fully mocked."""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock
from unittest.mock import MagicMock

from bot_app.lane_matchups import collect
from bot_app.match_cache import MatchCache
from bot_app.services.riot_api import RiotAPIError


def _participant(pid, puuid, team_id, position, champion_id):
    """Handle participant."""
    return {
        "participantId": pid,
        "puuid": puuid,
        "teamId": team_id,
        "teamPosition": position,
        "championId": champion_id,
        "gameEndedInEarlySurrender": False,
    }


def _match(match_id, puuids, *, queue_id=420, duration=1200):
    """A minimal ranked-solo match with distinct roles on both sides."""
    positions = ["TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY"]
    participants = []
    champ_id = 1
    for team_offset, team_id in enumerate((100, 200)):
        for slot, position in enumerate(positions):
            index = team_offset * 5 + slot
            participants.append(
                _participant(index + 1, puuids[index], team_id, position, champ_id)
            )
            champ_id += 1
    return {
        "info": {
            "queueId": queue_id,
            "gameDuration": duration,
            "gameVersion": "14.16.1.1",
            "platformId": "NA1",
            "participants": participants,
        }
    }


class SeedPuuidsTests(unittest.TestCase):
    def test_seed_puuids_collects_entries_across_tiers(self) -> None:
        """Verify that seed puuids collects entries across tiers."""
        client = MagicMock()
        client.apex_league.side_effect = [
            {"entries": [{"puuid": "a"}, {"puuid": "b"}]},
            {"entries": [{"puuid": "c"}]},
            RiotAPIError("boom"),
        ]
        with mock.patch.object(collect, "get_client", return_value=client):
            puuids = collect.seed_puuids("NA1", tiers=("challenger", "grandmaster", "master"))
        self.assertEqual(puuids, ["a", "b", "c"])


class HarvestTests(unittest.TestCase):
    def setUp(self) -> None:
        """Handle set up."""
        self.client = MagicMock()
        self.cache = MagicMock()
        self.cache.get.return_value = None

    def _patched(self, seed_puuids_return):
        """Handle patched."""
        return mock.patch.multiple(
            collect,
            get_client=MagicMock(return_value=self.client),
            seed_puuids=MagicMock(return_value=seed_puuids_return),
        )

    def test_harvest_dedupes_matches_already_in_the_cache(self) -> None:
        """Verify that harvest dedupes matches already in the cache."""
        puuids = [f"p{i}" for i in range(10)]
        self.client.match_ids.return_value = ["M1"]
        self.cache.get.return_value = {"cached": True}
        with self._patched([puuids[0]]):
            processed = collect.harvest("NA1", max_matches=5, cache=self.cache)
        self.assertEqual(processed, 0)
        self.client.match.assert_not_called()

    def test_harvest_fetches_new_matches_and_writes_lane_statistics(self) -> None:
        """Verify that harvest fetches new matches and writes lane statistics."""
        puuids = [f"p{i}" for i in range(10)]
        match = _match("M1", puuids)
        self.client.match_ids.return_value = ["M1"]
        self.client.match.return_value = match
        self.client.match_timeline.return_value = {
            "info": {
                "frames": [
                    {"timestamp": 0, "participantFrames": {}, "events": []},
                    {
                        "timestamp": 14 * 60_000,
                        "participantFrames": {
                            str(i + 1): {
                                "totalGold": 5000 + i * 10,
                                "xp": 4000,
                                "minionsKilled": 80,
                                "jungleMinionsKilled": 0,
                            }
                            for i in range(10)
                        },
                        "events": [],
                    },
                ]
            }
        }
        with self._patched([puuids[0]]):
            processed = collect.harvest("NA1", max_matches=5, cache=self.cache)
        self.assertEqual(processed, 1)
        self.cache.record_lane_outcomes.assert_called_once()
        outcomes = self.cache.record_lane_outcomes.call_args[0][0]
        self.assertTrue(len(outcomes) > 0)

    def test_harvest_stops_at_max_matches(self) -> None:
        """Verify that harvest stops at max matches."""
        puuids = [f"p{i}" for i in range(10)]
        self.client.match_ids.return_value = ["M1", "M2", "M3"]
        self.client.match.return_value = _match("M1", puuids, queue_id=440)
        with self._patched([puuids[0]]):
            processed = collect.harvest("NA1", max_matches=2, cache=self.cache)
        self.assertEqual(processed, 2)

    def test_harvest_expands_the_frontier_from_new_participants(self) -> None:
        """Verify that harvest expands the frontier from new participants."""
        puuids = [f"p{i}" for i in range(10)]
        match = _match("M1", puuids, queue_id=440)  # non-ranked, no lane stats
        self.client.match_ids.side_effect = lambda puuid, server, count: (
            ["M1"] if puuid == "p0" else []
        )
        self.client.match.return_value = match
        with self._patched([puuids[0]]):
            collect.harvest("NA1", max_matches=1, cache=self.cache)
        # Every other participant's puuid should have been requested for
        # match ids at least once (the frontier grew from M1's roster).
        called_puuids = {call.args[0] for call in self.client.match_ids.call_args_list}
        self.assertIn("p0", called_puuids)


def _cacheable_match(match_id="M1"):
    """A rateable match carrying the end timestamp ``MatchCache.put`` requires."""
    match = _match(match_id, [f"p{i}" for i in range(10)])
    match["info"]["gameEndTimestamp"] = 2_000_000_000_000
    return match


class RatingBaselineCollectionTests(unittest.TestCase):
    """Harvest and backfill funding the rating population baselines."""

    def test_harvest_records_rating_samples_from_the_same_fetch(self) -> None:
        """Verify a harvested match funds baselines without extra requests."""
        client = MagicMock()
        cache = MagicMock()
        cache.get.return_value = None
        puuids = [f"p{i}" for i in range(10)]
        client.match_ids.return_value = ["M1"]
        client.match.return_value = _match("M1", puuids)
        client.match_timeline.return_value = {"info": {"frames": []}}
        with mock.patch.multiple(
            collect,
            get_client=MagicMock(return_value=client),
            seed_puuids=MagicMock(return_value=[puuids[0]]),
            reset_default_baselines=MagicMock(),
        ):
            collect.harvest("NA1", max_matches=1, cache=cache)
        cache.record_rating_samples.assert_called_once()
        self.assertTrue(cache.record_rating_samples.call_args.args[0])
        self.assertEqual(client.match.call_count, 1)

    def test_backfill_is_idempotent(self) -> None:
        """Verify re-running the backfill does not double-count cached matches."""
        with TemporaryDirectory() as directory:
            cache = MatchCache(Path(directory) / "matches.sqlite")
            cache.put("NA1_1", "NA1", _cacheable_match())
            with mock.patch.object(collect, "reset_default_baselines"):
                first = collect.backfill_rating_baselines(cache)
                collect.backfill_rating_baselines(cache)
            self.assertGreater(first, 0)
            # Two samples per (position, metric) per match — one player
            # per team at each position — so idempotence means 2, not 4.
            samples = {row.samples for row in cache.rating_baseline_stats()}
            self.assertEqual(samples, {2})

    def test_backfill_spends_no_requests(self) -> None:
        """Verify the backfill never touches the Riot client."""
        client = MagicMock()
        with TemporaryDirectory() as directory:
            cache = MatchCache(Path(directory) / "matches.sqlite")
            cache.put("NA1_1", "NA1", _cacheable_match())
            with mock.patch.object(collect, "get_client", MagicMock(return_value=client)), \
                    mock.patch.object(collect, "reset_default_baselines"):
                collect.backfill_rating_baselines(cache)
        client.match.assert_not_called()
        client.match_timeline.assert_not_called()



if __name__ == "__main__":
    unittest.main()

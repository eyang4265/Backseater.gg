"""The live query must supplement the schedule page and fail visibly on errors."""

import json
import unittest
from unittest.mock import Mock, patch

from bot_app import lolesports
from bot_app.worlds import parse_series
from tests.test_worlds import event


class LiveFeedTests(unittest.TestCase):
    def test_live_series_absent_from_schedule_is_included(self):
        scheduled = event()
        scheduled["id"] = "later-series"
        page = Mock(text=json.dumps(scheduled))
        live = Mock()
        live.json.return_value = {"data": {"esports": {"events": [event(state="inProgress")]}}}
        with patch.object(lolesports.requests, "get", side_effect=[page, live]) as get, \
             patch.object(lolesports.time, "time", return_value=125):
            result = parse_series(lolesports.fetch_worlds_page())
        self.assertEqual(len(result), 2)
        self.assertTrue(any(s["state"] == "inProgress" for s in result))
        self.assertEqual(get.call_args.args[0], lolesports.LIVE_URL)
        variables = json.loads(get.call_args.kwargs["params"]["variables"])
        self.assertEqual(variables["eventState"], ["inProgress", "completed"])
        self.assertTrue(variables["eventDateStart"].endswith(".000Z"))
        self.assertTrue(variables["eventDateEnd"].endswith(".999Z"))
        self.assertEqual(get.call_args.kwargs["params"]["_refresh"], "2")

    def test_graphql_failure_does_not_silently_drop_live_games(self):
        live = Mock()
        live.json.return_value = {"errors": [{"message": "query changed"}]}
        with patch.object(lolesports.requests, "get", side_effect=[Mock(text="page"), live]):
            with self.assertRaises(lolesports.EsportsUnavailable):
                lolesports.fetch_worlds_page()

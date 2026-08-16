"""Bravery-pick logic for the /coachless command's build view."""
import unittest
from bot_app.coachless import CoachlessEntry
from bot_app.commands.coachless import _brave_pick, _brave_picks, _pick_share


class BraveryPickTests(unittest.TestCase):
    def test_excludes_only_the_single_most_picked_option(self):
        most_picked = CoachlessEntry("1", "MostPicked", wpa=5.0, buys=9900)
        runner_up = CoachlessEntry("2", "RunnerUp", wpa=1.0, buys=200)
        sleeper = CoachlessEntry("3", "Sleeper", wpa=3.0, buys=50)
        # MostPicked has the best WPA but is excluded as the obvious choice;
        # among what's left, the highest WPA wins even though it isn't the rarest.
        self.assertEqual(_brave_pick((most_picked, runner_up, sleeper)), sleeper)

    def test_a_decently_common_high_wpa_pick_can_still_win(self):
        most_picked = CoachlessEntry("1", "MostPicked", wpa=0.5, buys=9900)
        strong_but_common = CoachlessEntry("2", "StrongButCommon", wpa=4.0, buys=3000)
        rare_but_weak = CoachlessEntry("3", "RareButWeak", wpa=0.1, buys=10)
        self.assertEqual(_brave_pick((most_picked, strong_but_common, rare_but_weak)), strong_but_common)

    def test_two_entries_falls_back_to_the_higher_wpa_one(self):
        one = CoachlessEntry("1", "One", wpa=1.0, buys=500)
        two = CoachlessEntry("2", "Two", wpa=3.0, buys=500)
        self.assertEqual(_brave_pick((one, two)), two)

    def test_single_entry_returns_itself(self):
        only = CoachlessEntry("1", "Only", wpa=1.0, buys=500)
        self.assertEqual(_brave_pick((only,)), only)

    def test_empty_entries_yield_no_pick(self):
        self.assertIsNone(_brave_pick(()))

    def test_excluded_identifiers_are_skipped_for_deduping_across_stages(self):
        already_picked = CoachlessEntry("1", "AlreadyPicked", wpa=9.0, buys=500)
        next_best = CoachlessEntry("2", "NextBest", wpa=2.0, buys=100)
        self.assertEqual(
            _brave_pick((already_picked, next_best), exclude=frozenset({"1"})),
            next_best,
        )

    def test_excluding_everything_yields_no_pick(self):
        only = CoachlessEntry("1", "Only", wpa=1.0, buys=500)
        self.assertIsNone(_brave_pick((only,), exclude=frozenset({"1"})))

    def test_brave_picks_returns_top_n_by_wpa_excluding_most_picked(self):
        most_picked = CoachlessEntry("1", "MostPicked", wpa=9.0, buys=9900)
        first = CoachlessEntry("2", "First", wpa=4.0, buys=100)
        second = CoachlessEntry("3", "Second", wpa=3.0, buys=100)
        third = CoachlessEntry("4", "Third", wpa=2.0, buys=100)
        fourth = CoachlessEntry("5", "Fourth", wpa=1.0, buys=100)
        result = _brave_picks((most_picked, first, second, third, fourth), 3)
        self.assertEqual(result, (first, second, third))

    def test_brave_picks_stops_short_when_the_pool_runs_out(self):
        most_picked = CoachlessEntry("1", "MostPicked", wpa=9.0, buys=9900)
        only_other = CoachlessEntry("2", "OnlyOther", wpa=1.0, buys=100)
        self.assertEqual(_brave_picks((most_picked, only_other), 3), (only_other,))

    def test_brave_picks_respects_exclude(self):
        a = CoachlessEntry("1", "A", wpa=5.0, buys=100)
        b = CoachlessEntry("2", "B", wpa=4.0, buys=500)  # most-picked among what's left after exclude
        c = CoachlessEntry("3", "C", wpa=3.0, buys=100)
        d = CoachlessEntry("4", "D", wpa=2.0, buys=100)
        self.assertEqual(_brave_picks((a, b, c, d), 2, exclude=frozenset({"1"})), (c, d))

    def test_pick_share_is_relative_to_the_whole_category(self):
        a = CoachlessEntry("1", "A", wpa=1.0, buys=25)
        b = CoachlessEntry("2", "B", wpa=1.0, buys=75)
        self.assertAlmostEqual(_pick_share(a, (a, b)), 0.25)
        self.assertAlmostEqual(_pick_share(b, (a, b)), 0.75)


if __name__ == "__main__": unittest.main()

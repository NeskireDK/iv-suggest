"""Fatigue is wall-clock hours, so the shuffle interval is a free knob.

`top_hours` shipped as an int the reorder incremented by one per run. That only
equalled hours because the timer fired hourly: at a quarter-hour tick `fatigue`
0.95 would have become 0.81 an hour and `fatigue_cap` 8 would have been reached
in two hours instead of eight.

The property worth pinning is that the same elapsed time costs the same score
however many reorders it is split across.
"""

import unittest

from support import load

ME = "andre@example.com"
LANE = "suggested"


class Elapsed(unittest.TestCase):

    def setUp(self):
        self.mod = load(IV_SUGGEST_ACCOUNT=ME)

    def answering(self, rows):
        self.mod.query = lambda sql: rows

    def test_the_gap_since_the_last_reorder_is_what_ages_an_item(self):
        self.answering([[0.25]])
        self.assertAlmostEqual(0.25,
                               self.mod.hours_since_the_last_reorder(LANE))

    def test_a_lane_that_has_never_been_reordered_ages_nothing(self):
        """No row yet, so there is no elapsed time to charge anything for."""
        self.answering([[None]])
        self.assertEqual(0.0, self.mod.hours_since_the_last_reorder(LANE))
        self.answering([])
        self.assertEqual(0.0, self.mod.hours_since_the_last_reorder(LANE))

    def test_a_clock_that_went_backwards_cannot_unage_a_lane(self):
        self.answering([[-3.0]])
        self.assertEqual(0.0, self.mod.hours_since_the_last_reorder(LANE))


class WhatAgeFatigueWrites(unittest.TestCase):

    def setUp(self):
        self.mod = load(IV_SUGGEST_ACCOUNT=ME)
        self.mod.hours_since_the_last_reorder = lambda lane_id: 0.25
        self.sql = []
        self.mod.execute = lambda sql: self.sql.append(sql)
        self.cfg = dict(self.mod.SHUFFLE_DEFAULTS, visible=2, fatigue_cap=8)

    def statements(self):
        self.mod.age_fatigue(LANE, self.cfg, ["a", "b", "c"])
        return self.sql[0]

    def test_the_visible_gain_the_elapsed_hours_not_one(self):
        self.assertIn("+0.250000::double precision", self.statements())

    def test_the_rest_lose_the_same_hours_they_would_have_gained(self):
        self.assertIn("-0.250000::double precision", self.statements())

    def test_the_cap_is_written_as_hours_rather_than_a_run_count(self):
        self.assertIn("8.000000::double precision", self.statements())


class SplittingATickChangesNothing(unittest.TestCase):
    """The whole point: an hour of screen time costs the same whether the lane
    was reordered once or four times during it."""

    def setUp(self):
        self.mod = load(IV_SUGGEST_ACCOUNT=ME)
        self.cfg = dict(self.mod.SHUFFLE_DEFAULTS, jitter=0, fatigue=0.95,
                        fatigue_cap=8, recency_boost=0,
                        published_halflife_days=0, unwatched_halflife_days=0)

    def scored(self, top):
        return self.mod.display_score(
            {"score": 1.0, "top": top, "age": 0.0, "since_top": 0.0,
             "published_days": 0.0}, self.cfg)

    def test_four_quarter_hours_score_as_one_hour(self):
        self.assertAlmostEqual(self.scored(4 * 0.25), self.scored(1.0), 9)

    def test_an_hour_on_screen_still_costs_what_it_always_did(self):
        self.assertAlmostEqual(0.95, self.scored(1.0), 9)

    def test_a_fractional_hour_costs_a_fraction_rather_than_nothing(self):
        """An int column truncated this to 0 and a 15 minute lane never tired."""
        self.assertLess(self.scored(0.25), self.scored(0.0))
        self.assertGreater(self.scored(0.25), self.scored(1.0))

    def test_the_cap_still_bounds_an_interrupted_timer(self):
        """A reorder after two days off charges the real gap, and the cap holds it."""
        self.assertAlmostEqual(self.scored(48.0), self.scored(8.0), 9)


if __name__ == "__main__":
    unittest.main()

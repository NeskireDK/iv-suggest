"""Turnover as displacement rather than expiry.

A lane on a clock throws away a video for having been there a while, which says
nothing about whether it was any good. The alternative: let the lane grow into
`size`, and after that let each new video take the place of the lowest standing
one already in it. The decays are what put a video at the bottom, so "worst"
means ignored or old, not merely early.

`grow_per_day` is the rate. Without it a displacing lane would fill once and
freeze, which is why the config refuses that combination outright.
"""

import unittest

from support import load

ME = "andre@example.com"


class Standing(unittest.TestCase):
    """What an entry is worth now, as opposed to what it scored the night it arrived."""

    def setUp(self):
        self.mod = load(IV_SUGGEST_ACCOUNT=ME)
        self.cfg = dict(self.mod.SHUFFLE_DEFAULTS, published_halflife_days=21,
                        unwatched_halflife_days=3)

    def test_with_both_decays_off_it_is_the_stored_score(self):
        flat = dict(self.cfg, published_halflife_days=0, unwatched_halflife_days=0)
        self.assertEqual(2.5, self.mod.standing_score(2.5, 400.0, 9000.0, flat))

    def test_sitting_unwatched_lowers_it(self):
        self.assertLess(self.mod.standing_score(1.0, 30.0, 0.0, self.cfg),
                        self.mod.standing_score(1.0, 0.0, 0.0, self.cfg))

    def test_an_old_upload_lowers_it(self):
        self.assertLess(self.mod.standing_score(1.0, 0.0, 900.0, self.cfg),
                        self.mod.standing_score(1.0, 0.0, 1.0, self.cfg))

    def test_a_lane_with_upload_decay_off_does_not_punish_an_old_song(self):
        """music-discover: a song is not stale for being from 1998."""
        music = dict(self.cfg, published_halflife_days=0)
        self.assertEqual(self.mod.standing_score(1.0, 0.0, 9000.0, music),
                         self.mod.standing_score(1.0, 0.0, 1.0, music))

    def test_the_two_floors_compound_and_that_is_the_worst_a_score_can_be_cut(self):
        """0.15 x 0.25. Old AND ignored is a 26x discount, so on a lane running both
        decays the score has to be about 27 times better to survive it. That is the
        intended answer for "old and nobody watched it", and it is why
        music-discover runs only one of the two."""
        self.assertAlmostEqual(0.0375,
                               self.mod.standing_score(1.0, 9e9, 9e9, self.cfg), 6)
        self.assertLess(self.mod.standing_score(10.0, 60.0, 900.0, self.cfg),
                        self.mod.standing_score(0.5, 0.0, 0.0, self.cfg))

    def test_with_only_one_decay_a_strong_score_still_wins(self):
        """music-discover's shape: a great old song outranks a mediocre new one."""
        music = dict(self.cfg, published_halflife_days=0)
        self.assertGreater(self.mod.standing_score(10.0, 60.0, 9000.0, music),
                           self.mod.standing_score(0.5, 0.0, 0.0, music))


class WhoLeaves(unittest.TestCase):

    def setUp(self):
        self.mod = load(IV_SUGGEST_ACCOUNT=ME)
        self.now = self.mod.time.time()
        self.lane = dict(self.mod.DEFAULTS, id="l", size=4, grow_per_day=2,
                         displace_when_full=True,
                         shuffle=dict(self.mod.SHUFFLE_DEFAULTS,
                                      published_halflife_days=0,
                                      unwatched_halflife_days=0))

    def keep(self, *vids):
        return [({"videoId": v, "title": v}, None) for v in vids]

    def state(self, scores):
        added = dict((v, self.now) for v in scores)
        return added, dict(scores)

    def lowest(self, lane, keep, scores, uploads=None):
        added, item_score = self.state(scores)
        return self.mod.lowest_standing(keep, lane, added, item_score, uploads or {})

    def test_a_lane_below_its_size_gives_up_nothing(self):
        """It grows into `size` rather than churning while it is still short."""
        self.assertEqual(set(), self.lowest(self.lane, self.keep("a", "b"),
                                            {"a": 1.0, "b": 0.1}))

    def test_a_full_lane_gives_up_its_worst(self):
        got = self.lowest(self.lane, self.keep("a", "b", "c", "d"),
                          {"a": 9.0, "b": 0.2, "c": 8.0, "d": 0.1})
        self.assertEqual({"b", "d"}, got)

    def test_it_gives_up_exactly_the_growth_rate(self):
        lane = dict(self.lane, grow_per_day=1)
        got = self.lowest(lane, self.keep("a", "b", "c", "d"),
                          {"a": 9.0, "b": 0.2, "c": 8.0, "d": 0.1})
        self.assertEqual({"d"}, got)

    def test_the_decay_and_not_the_arrival_order_decides_the_worst(self):
        """The whole point: `oldest_survivors` would take the earliest two."""
        added = {"old_but_good": self.now - 40 * 86400,
                 "new_and_weak": self.now, "b": self.now, "c": self.now}
        scores = {"old_but_good": 9.0, "new_and_weak": 0.1, "b": 5.0, "c": 5.0}
        lane = dict(self.lane, grow_per_day=1)
        got = self.mod.lowest_standing(self.keep(*scores), lane, added, scores, {})
        self.assertEqual({"new_and_weak"}, got)

    def test_an_upload_with_no_date_is_ranked_as_old_not_as_missing(self):
        lane = dict(self.lane, grow_per_day=1,
                    shuffle=dict(self.mod.SHUFFLE_DEFAULTS,
                                 published_halflife_days=21))
        got = self.lowest(lane, self.keep("a", "b", "c", "d"),
                          {"a": 1.0, "b": 1.0, "c": 1.0, "d": 1.0},
                          uploads={"a": 0.0, "b": 0.0, "c": 0.0})
        self.assertEqual({"d"}, got)


class HowFast(unittest.TestCase):

    def setUp(self):
        self.mod = load(IV_SUGGEST_ACCOUNT=ME)

    def keep(self, n):
        return [({"videoId": str(i)}, None) for i in range(n)]

    def test_no_rate_fills_every_empty_slot_at_once(self):
        lane = dict(self.mod.DEFAULTS, size=100, grow_per_day=0)
        self.assertEqual(100, self.mod.room_for_new(lane, self.keep(0)))

    def test_a_rate_is_what_stops_a_size_100_lane_costing_100_adds_tonight(self):
        lane = dict(self.mod.DEFAULTS, size=100, grow_per_day=6)
        self.assertEqual(6, self.mod.room_for_new(lane, self.keep(30)))

    def test_the_rate_never_overfills_a_nearly_full_lane(self):
        lane = dict(self.mod.DEFAULTS, size=100, grow_per_day=6)
        self.assertEqual(2, self.mod.room_for_new(lane, self.keep(98)))

    def test_a_full_lane_has_room_for_exactly_what_it_gave_up(self):
        lane = dict(self.mod.DEFAULTS, size=100, grow_per_day=6)
        self.assertEqual(6, self.mod.room_for_new(lane, self.keep(94)))


class WhichModel(unittest.TestCase):

    def setUp(self):
        self.mod = load(IV_SUGGEST_ACCOUNT=ME)
        self.mod.upload_age_in = lambda plid: {}

    def keep(self, *vids):
        return [({"videoId": v, "title": v}, None) for v in vids]

    def test_a_clock_lane_still_rotates_by_age_and_says_so(self):
        lane = dict(self.mod.DEFAULTS, id="l", size=4, refresh_per_day=1)
        now = self.mod.time.time()
        got, why = self.mod.what_this_run_gives_up(
            self.keep("early", "late"), lane, "PL1",
            {"early": now - 86400, "late": now}, {"early": 9.0, "late": 0.1})
        self.assertEqual(({"early"}, "rotated"), (got, why))

    def test_a_displacing_lane_takes_the_worst_and_says_so(self):
        lane = dict(self.mod.DEFAULTS, id="l", size=2, grow_per_day=1,
                    displace_when_full=True,
                    shuffle=dict(self.mod.SHUFFLE_DEFAULTS))
        now = self.mod.time.time()
        got, why = self.mod.what_this_run_gives_up(
            self.keep("early", "late"), lane, "PL1",
            {"early": now - 86400, "late": now}, {"early": 9.0, "late": 0.1})
        self.assertEqual(({"late"}, "displaced"), (got, why))

    def test_being_displaced_costs_the_same_cooldown_as_being_rotated(self):
        lane = dict(self.mod.DEFAULTS)
        self.assertEqual(self.mod.cooldown_days_for(lane, "rotated"),
                         self.mod.cooldown_days_for(lane, "displaced"))


class Refusals(unittest.TestCase):

    def setUp(self):
        self.mod = load(IV_SUGGEST_ACCOUNT=ME)

    def test_displacing_with_no_rate_is_refused_rather_than_frozen(self):
        lane = dict(self.mod.DEFAULTS, id="frozen", displace_when_full=True,
                    grow_per_day=0, shuffle=dict(self.mod.SHUFFLE_DEFAULTS))
        with self.assertRaises(self.mod.Aborted) as caught:
            self.mod.refuse_a_lane_that_can_never_turn_over(lane)
        self.assertIn("frozen", str(caught.exception))
        self.assertIn("grow_per_day", str(caught.exception))

    def test_a_rate_without_displacement_is_fine(self):
        lane = dict(self.mod.DEFAULTS, id="ok", grow_per_day=6,
                    shuffle=dict(self.mod.SHUFFLE_DEFAULTS))
        self.assertIsNone(self.mod.refuse_a_lane_that_can_never_turn_over(lane))


if __name__ == "__main__":
    unittest.main()

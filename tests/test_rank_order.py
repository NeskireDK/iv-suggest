"""Holding a lane in one order instead of reordering it.

A subscription lane's whole point is a rank -- biggest first, or newest first.
The weighted reorder does not preserve one: the stored score falls 1% per rank
while fatigue alone takes 34% off what has been on screen, so within a day the
feed rank means nothing. Measured on the live `subs-live` before this: slot 1
held an 08-27 stream and slot 2 held 09-03, in a lane sorted newest first.

`rank_order` is a hold, not a switch-off. The lane is still swept for watched
videos, and it still hands a mix lane sourcing it a real order -- both of which
`shuffle.enabled: false` would have taken away, and `home-mix` sources
`subs-top48`.
"""

import unittest

from support import load

ME = "andre@example.com"


class Holding(unittest.TestCase):

    def setUp(self):
        self.mod = load(IV_SUGGEST_ACCOUNT=ME)

    def cfg(self, order):
        return dict(self.mod.SHUFFLE_DEFAULTS, rank_order=order)

    def items(self):
        return [{"vid": "middle", "score": 5.0, "published_days": 10.0,
                 "top": 8, "age": 0.0, "since_top": 0.0, "ucid": "A"},
                {"vid": "best", "score": 9.0, "published_days": 30.0,
                 "top": 8, "age": 0.0, "since_top": 0.0, "ucid": "B"},
                {"vid": "newest", "score": 1.0, "published_days": 1.0,
                 "top": 0, "age": 0.0, "since_top": 0.0, "ucid": "C"}]

    def order(self, cfg):
        return [it["vid"] for it in self.mod.rank_items(self.items(), cfg)]

    def test_score_holds_the_rank_the_nightly_run_stored(self):
        self.assertEqual(["best", "middle", "newest"], self.order(self.cfg("score")))

    def test_published_holds_newest_first(self):
        self.assertEqual(["newest", "middle", "best"],
                         self.order(self.cfg("published")))

    def test_a_held_lane_ignores_fatigue_jitter_and_the_rota(self):
        """Every one of those exists to move things, which is what a hold refuses."""
        noisy = dict(self.cfg("score"), fatigue=0.5, jitter=0.9,
                     round_robin_top=True, diversity=True, recency_boost=5.0)
        for _ in range(20):
            self.assertEqual(["best", "middle", "newest"], self.order(noisy))

    def test_the_hold_is_off_by_default_so_every_lane_reorders_as_before(self):
        self.assertEqual("", self.mod.SHUFFLE_DEFAULTS["rank_order"])

    def test_without_a_hold_the_weighted_ranking_still_runs(self):
        calm = dict(self.mod.SHUFFLE_DEFAULTS, jitter=0, diversity=False,
                    round_robin_top=False)
        self.assertNotEqual(["best", "middle", "newest"], self.order(calm))


class StillSwept(unittest.TestCase):
    """A hold must not cost the two things `shuffle.enabled: false` would."""

    def setUp(self):
        self.mod = load(IV_SUGGEST_ACCOUNT=ME)
        self.mod.lane_plid = lambda account, lane_id: "PL1"
        self.mod.permute_playlist_index = lambda plid, ordered: None
        self.mod.age_fatigue = lambda lane_id, cfg, after: None
        self.mod.execute = lambda sql: None
        self.mod.read_order = lambda plid, lane_id: [
            [n + 1, v, "UC%d" % n, float(3 - n), 0, 1.0, 1.0, v, float(n)]
            for n, v in enumerate(["a", "b", "c"])]
        self.swept = []
        self.mod.drop_the_watched = lambda lane, plid, dry: \
            self.swept.append(lane["id"]) or 0

    def lane(self):
        return dict(self.mod.DEFAULTS, id="subs-top48", policy="refill",
                    shuffle=dict(self.mod.SHUFFLE_DEFAULTS, rank_order="score"))

    def test_a_held_lane_is_still_swept_for_watched_videos(self):
        self.mod.shuffle_lane(self.lane(), {}, dry=False)
        self.assertEqual(["subs-top48"], self.swept)

    def test_a_held_lane_still_reports_an_order_for_a_mix_that_sources_it(self):
        """home-mix sources subs-top48; a lane returning None makes the mix skip."""
        self.assertEqual(["a", "b", "c"],
                         self.mod.shuffle_lane(self.lane(), {}, dry=False))


class Refusals(unittest.TestCase):

    def setUp(self):
        self.mod = load(IV_SUGGEST_ACCOUNT=ME)

    def lane(self, order):
        return dict(self.mod.DEFAULTS, id="typo",
                    shuffle=dict(self.mod.SHUFFLE_DEFAULTS, rank_order=order))

    def test_an_order_nothing_can_sort_by_is_refused_not_ignored(self):
        """Silently ignoring it would leave the lane reordering and looking fine."""
        with self.assertRaises(self.mod.Aborted) as caught:
            self.mod.refuse_a_lane_that_can_never_turn_over(self.lane("views"))
        self.assertIn("typo", str(caught.exception))
        self.assertIn("published", str(caught.exception))

    def test_the_two_real_orders_pass(self):
        for order in ("score", "published", ""):
            self.assertIsNone(
                self.mod.refuse_a_lane_that_can_never_turn_over(self.lane(order)))


if __name__ == "__main__":
    unittest.main()

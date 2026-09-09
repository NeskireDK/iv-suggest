"""Age, in the three senses this engine now has to keep apart.

Publish age is how long ago the video went up. Lane age is how long it has been
on offer here. `recency_halflife` has always meant the second one, which is why
a five year old upload added last night used to take the full freshness boost.

The third is the sweep: a video nobody watched leaves, counted in days the
account actually watched something rather than calendar days, so a fortnight
away does not empty every lane.
"""

import unittest

from support import load, source

ME = "andre@example.com"


class SoftDecay(unittest.TestCase):

    def setUp(self):
        self.mod = load(IV_SUGGEST_ACCOUNT=ME)

    def test_a_halflife_of_zero_is_off_rather_than_a_division(self):
        self.assertEqual(1.0, self.mod.soft_decay(500.0, 0, 0.15))

    def test_nothing_is_discounted_at_zero_age(self):
        self.assertEqual(1.0, self.mod.soft_decay(0.0, 21, 0.15))

    def test_one_halflife_lands_halfway_to_the_floor(self):
        self.assertAlmostEqual(0.575, self.mod.soft_decay(21.0, 21, 0.15), 3)

    def test_the_floor_is_a_floor_and_not_an_asymptote_to_zero(self):
        """An old video must still be able to surface on a strong score."""
        self.assertAlmostEqual(0.15, self.mod.soft_decay(10000.0, 21, 0.15), 6)
        self.assertGreater(self.mod.soft_decay(10000.0, 21, 0.15), 0.0)

    def test_a_negative_age_cannot_amplify(self):
        """A clock skew between the database and YouTube must not become a boost."""
        self.assertEqual(1.0, self.mod.soft_decay(-40.0, 21, 0.15))


class WhichAge(unittest.TestCase):
    """The two ages in `display_score`, held apart by a test rather than a comment."""

    def setUp(self):
        self.mod = load(IV_SUGGEST_ACCOUNT=ME)
        self.cfg = dict(self.mod.SHUFFLE_DEFAULTS, jitter=0)

    def item(self, **over):
        return dict({"score": 1.0, "top": 0, "age": 0.0, "since_top": 0.0,
                     "published_days": 0.0}, **over)

    def test_the_defaults_leave_every_existing_lane_scored_as_before(self):
        """Both new decays ship off, so a lane that sets neither cannot move."""
        cfg = dict(self.cfg, published_halflife_days=0, unwatched_halflife_days=0)
        old = self.item(published_days=4000.0, age=800.0)
        new = self.item(published_days=0.0, age=800.0)
        self.assertEqual(self.mod.display_score(old, cfg),
                         self.mod.display_score(new, cfg))

    def test_an_old_upload_added_tonight_scores_below_a_new_one(self):
        """The bug this fixes: `recency_halflife` reads lane age, so both used to tie."""
        cfg = dict(self.cfg, published_halflife_days=21)
        ancient = self.item(published_days=2000.0)
        fresh = self.item(published_days=1.0)
        self.assertLess(self.mod.display_score(ancient, cfg),
                        self.mod.display_score(fresh, cfg))

    def test_lane_age_still_drives_the_arrival_boost_on_its_own(self):
        cfg = dict(self.cfg, published_halflife_days=0, unwatched_halflife_days=0)
        just_added = self.item(age=0.0)
        settled = self.item(age=72.0)
        self.assertGreater(self.mod.display_score(just_added, cfg),
                           self.mod.display_score(settled, cfg))

    def test_sitting_unwatched_for_days_keeps_costing_after_the_boost_flattens(self):
        """`recency_halflife` is 12 hours, so past the first day it barely separates."""
        cfg = dict(self.cfg, unwatched_halflife_days=3)
        two_days = self.item(age=48.0)
        nine_days = self.item(age=216.0)
        flat = dict(cfg, unwatched_halflife_days=0)
        without = (self.mod.display_score(nine_days, flat)
                   / self.mod.display_score(two_days, flat))
        with_it = (self.mod.display_score(nine_days, cfg)
                   / self.mod.display_score(two_days, cfg))
        self.assertGreater(without, 0.9)
        self.assertLess(with_it, 0.5)


class RowsFromTheDatabase(unittest.TestCase):
    """`read_order` sends CSV through psql, so a NULL arrives as an empty string."""

    def setUp(self):
        self.mod = load(IV_SUGGEST_ACCOUNT=ME)

    def row(self, pub_days):
        return [7, "vid00000001", "UC", "1.5", "2", "3.0", "4.0", "T", pub_days]

    def test_a_published_date_is_read_as_days(self):
        self.assertEqual(12.5, self.mod.item_to_rank(self.row("12.5"))["published_days"])

    def test_a_null_published_becomes_the_no_date_stand_in(self):
        got = self.mod.item_to_rank(self.row(""))
        self.assertEqual(self.mod.AGE_OF_A_LISTING_WITH_NO_DATE,
                         got["published_days"])

    def test_read_order_asks_for_the_column_item_to_rank_unpacks(self):
        """The two are one wide tuple apart; a mismatch is a ValueError at 03:30."""
        self.assertIn("pv.published", source())


class CandidateAge(unittest.TestCase):
    """The other half: fewer old videos get in, not just lower once they are in."""

    def setUp(self):
        self.mod = load(IV_SUGGEST_ACCOUNT=ME)

    def lane(self, **over):
        return dict(self.mod.DEFAULTS, **over)

    def rec(self, vid, days):
        when = self.mod.time.time() - days * 86400.0
        return {"videoId": vid,
                "published": self.mod.datetime.fromtimestamp(
                    when, self.mod.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}

    def expand(self, lane, recs):
        class Fetcher:
            lane_used = cache_hits = 0

            def video(self, vid):
                return {"recommendedVideos": recs}

        run = self.mod.LaneRun([], set(), set(), set(), Fetcher(), True, None)
        scores, _ = self.mod.expand_by_recommendation(lane, ["seed0000001"], 10, run)
        return scores

    def test_off_by_default_so_the_existing_lanes_keep_the_whole_graph(self):
        scores = self.expand(self.lane(),
                             [self.rec("new00000001", 1), self.rec("old00000001", 900)])
        self.assertAlmostEqual(scores["new00000001"], scores["old00000001"], 6)

    def test_an_old_recommendation_is_discounted_rather_than_dropped(self):
        lane = self.lane(recommend_age_halflife_days=21)
        scores = self.expand(lane, [self.rec("new00000001", 1),
                                    self.rec("old00000001", 900)])
        self.assertIn("old00000001", scores)
        self.assertLess(scores["old00000001"], scores["new00000001"] * 0.3)

    def test_a_hard_cap_still_drops_rather_than_discounts(self):
        """`recommend_max_age_days` and the halflife are separate decisions."""
        lane = self.lane(recommend_max_age_days=14, recommend_age_halflife_days=21)
        scores = self.expand(lane, [self.rec("new00000001", 1),
                                    self.rec("old00000001", 900)])
        self.assertNotIn("old00000001", scores)

    def test_a_listing_with_no_date_is_treated_as_old_not_as_new(self):
        lane = self.lane(recommend_age_halflife_days=21)
        scores = self.expand(lane, [{"videoId": "nodate00001"},
                                    self.rec("new00000001", 0)])
        self.assertLess(scores["nodate00001"], scores["new00000001"])


if __name__ == "__main__":
    unittest.main()

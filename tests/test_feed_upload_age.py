"""The public feed ranks on agreement and depth, and now on upload age too.

A source mix may rank an old video highly on purpose: `music-discover` turns its
own upload-age decay off, because a song is not stale for being from 1998. That
reasoning holds inside a music playlist and stops holding in a feed a
logged-out visitor reads as what is happening now.

Measured on the live `popular` on 2026-09-24, before this: 9 of its 54 videos
were over a year old, the first of them stood at median position 5 over 4,000
simulated draws, and 1.48 of the first ten slots were years old. The whole
effect is in where they sit -- one account holds a `home-mix`, so the draw takes
54 of 54 and every video is in the feed whatever its weight.
"""

import unittest

from support import load, source
from test_sessions import callers_of

ME = "andre@example.com"
A_YEAR = 365.0
LAST_WEEK = 7.0


class TheDiscount(unittest.TestCase):

    def setUp(self):
        self.mod = load(IV_SUGGEST_ACCOUNT=ME)
        self.ages = {}
        self.asked = []
        self.mod.query = lambda sql: self.asked.append(sql) or [
            [vid, days] for vid, days in self.ages.items() if "'%s'" % vid in sql]

    def cfg(self, **over):
        return dict(self.mod.consensus_config({}), **over)

    def weigh(self, *lists, **over):
        mixes = [{"key": "mix%d" % n, "vids": list(vids)}
                 for n, vids in enumerate(lists)]
        return self.mod.consensus_weights(mixes, self.cfg(**over))

    def test_it_ships_off_and_asks_the_database_nothing(self):
        """The default is the arithmetic the feed already ran."""
        self.assertEqual(0, self.mod.CONSENSUS_DEFAULTS["published_halflife_days"])
        weights = self.weigh(["a", "b"])
        self.assertEqual([], self.asked)
        self.assertEqual({"a": 1 / 4.0, "b": 1 / 5.0}, weights)

    def test_an_old_upload_weighs_less_than_a_recent_one_at_the_same_depth(self):
        self.ages = {"old": 2000.0, "new": LAST_WEEK}
        weights = self.weigh(["old"], ["new"], published_halflife_days=21)
        self.assertLess(weights["old"], weights["new"])

    def test_a_recent_video_deep_in_a_mix_can_beat_an_old_one_near_its_top(self):
        """The whole point: a 1998 song at rank 8 stops leading a public feed."""
        recent = ["v%d" % n for n in range(12)]
        self.ages = dict({"old": 5580.0}, **{vid: LAST_WEEK for vid in recent})
        weights = self.weigh(recent[:8] + ["old"] + recent[8:],
                             published_halflife_days=21)
        self.assertLess(weights["old"], weights["v11"])

    def test_the_floor_is_what_the_oldest_takes_rather_than_zero(self):
        self.ages = {"ancient": 40000.0}
        weights = self.weigh(["ancient"], published_halflife_days=21,
                             published_floor=0.15)
        self.assertAlmostEqual(0.15 / 4.0, weights["ancient"])

    def test_a_higher_floor_is_a_gentler_discount(self):
        self.ages = {"old": 2000.0}
        hard = self.weigh(["old"], published_halflife_days=21, published_floor=0.15)
        soft = self.weigh(["old"], published_halflife_days=21, published_floor=0.50)
        self.assertLess(hard["old"], soft["old"])

    def test_agreement_still_lifts_an_old_video_over_a_recent_one_nobody_shares(self):
        """Age is a discount on the weight, not a veto over what the feed agrees on."""
        self.ages = {"agreed": 2000.0, "solo": LAST_WEEK}
        weights = self.weigh(["agreed", "solo"], ["agreed"], ["agreed"],
                             published_halflife_days=90, published_floor=0.5)
        self.assertGreater(weights["agreed"], weights["solo"])

    def test_a_video_with_no_date_takes_the_stand_in_age_rather_than_dropping_out(self):
        self.ages = {}
        weights = self.weigh(["nodate"], published_halflife_days=21)
        expected = self.mod.soft_decay(self.mod.AGE_OF_A_LISTING_WITH_NO_DATE,
                                       21, 0.15) / 4.0
        self.assertAlmostEqual(expected, weights["nodate"])

    def test_every_date_is_read_in_one_query_however_many_videos(self):
        self.ages = {"a": 1.0, "b": 2.0, "c": 3.0}
        self.weigh(["a", "b"], ["c"], published_halflife_days=21)
        self.assertEqual(1, len(self.asked))
        self.assertIn("playlist_videos", self.asked[0])

    def test_no_video_at_all_asks_nothing(self):
        self.assertEqual({}, self.mod.upload_age_weights(
            set(), self.cfg(published_halflife_days=21)))
        self.assertEqual([], self.asked)


class WhereItApplies(unittest.TestCase):

    def setUp(self):
        self.mod = load(IV_SUGGEST_ACCOUNT=ME)
        self.source = source()

    def test_the_hourly_redraw_weighs_the_same_way_the_nightly_build_does(self):
        """Both go through consensus_weights, so one setting governs both."""
        self.assertIn("consensus_order", callers_of(self.source, "consensus_weights"))
        self.assertIn("run_lane_consensus",
                      callers_of(self.source, "consensus_weights"))

    def test_the_dates_come_over_sql_and_not_from_a_fetch(self):
        self.assertNotIn("upload_ages_in_days", callers_of(self.source, "get_video"))
        self.assertIn("upload_ages_in_days", callers_of(self.source, "query"))

    def test_a_mix_lane_is_left_alone(self):
        """home-mix reads each source's stored order; this is the feed's own ranking."""
        self.assertNotIn("run_lane_mix", callers_of(self.source, "upload_age_weights"))
        self.assertNotIn("mix_order", callers_of(self.source, "upload_age_weights"))

    def test_both_keys_are_readable_out_of_a_lane_s_consensus_block(self):
        cfg = self.mod.consensus_config(
            {"consensus": {"published_halflife_days": 30, "published_floor": 0.25}})
        self.assertEqual(30, cfg["published_halflife_days"])
        self.assertEqual(0.25, cfg["published_floor"])
        self.assertEqual([{"users": "all", "lane": "home-mix"}], cfg["sources"])


if __name__ == "__main__":
    unittest.main()

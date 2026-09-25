"""A `published` ahead of now is a bad date, not a very new upload.

Found live on 2026-09-25: `YVqW8dX4kcQ` ("Interview with 0.1x Engineer [v2
Patch]", Kai Lentit) sat in two of this account's playlists dated **2027-02-02**,
about 130 days ahead. Every reader of upload age treated that as maximally
fresh -- `soft_decay` clamps a negative age to zero, `days_since_upload` clamped
it to zero, and `rank_order: published` sorts ascending, so it would have led
`subs-live` for as long as the lane held it. Nothing was going to age it out
either: it gets newer relative to nothing.

The rule is the one the codebase already had for a missing date: it is not a
date. `parse_published` refuses it at the edge, so the intake filters reject it
exactly as they reject a listing with no date at all, and the two readers that
come off SQL rather than a listing go through `upload_age_in_days`.
"""

import time
import unittest

from support import load

ME = "andre@example.com"
A_YEAR_AHEAD = 365 * 86400.0


class AtTheEdge(unittest.TestCase):
    """parse_published, which is where upstream's answer becomes something we believe."""

    def setUp(self):
        self.mod = load(IV_SUGGEST_ACCOUNT=ME)

    def parse(self, published):
        return self.mod.parse_published({"published": published})

    def test_a_unix_timestamp_in_the_future_is_no_date(self):
        self.assertEqual(0.0, self.parse(time.time() + A_YEAR_AHEAD))

    def test_an_rfc3339_string_in_the_future_is_no_date(self):
        self.assertEqual(0.0, self.parse("2027-02-02T00:00:00Z"))

    def test_an_upload_from_a_moment_ago_is_still_a_date(self):
        just_now = time.time() - 5
        self.assertAlmostEqual(just_now, self.parse(just_now), places=3)

    def test_clock_skew_inside_the_grace_is_not_treated_as_bogus(self):
        """A few seconds of drift upstream must not discard a genuinely new upload."""
        skewed = time.time() + self.mod.CLOCK_SKEW_GRACE_SECONDS / 2
        self.assertAlmostEqual(skewed, self.parse(skewed), places=3)

    def test_a_real_past_date_is_untouched(self):
        self.assertEqual(1600000000.0, self.parse(1600000000))


class TheAgeReaders(unittest.TestCase):

    def setUp(self):
        self.mod = load(IV_SUGGEST_ACCOUNT=ME)
        self.stand_in = self.mod.AGE_OF_A_LISTING_WITH_NO_DATE

    def test_a_negative_age_off_sql_becomes_the_stand_in(self):
        self.assertEqual(self.stand_in, self.mod.upload_age_in_days(-130.0))

    def test_a_missing_age_off_sql_becomes_the_stand_in(self):
        for missing in (None, ""):
            self.assertEqual(self.stand_in, self.mod.upload_age_in_days(missing))

    def test_a_real_age_passes_through(self):
        self.assertEqual(12.5, self.mod.upload_age_in_days(12.5))

    def test_a_future_dated_listing_reads_as_the_stand_in_age(self):
        age = self.mod.days_since_upload({"published": time.time() + A_YEAR_AHEAD})
        self.assertEqual(self.stand_in, age)

    def test_the_shuffle_never_sees_a_negative_published_days(self):
        row = [1, "vid", "UC", 1.0, 0, 1.0, 1.0, "t", -130.0]
        self.assertEqual(self.stand_in, self.mod.item_to_rank(row)["published_days"])

    def test_the_feed_s_own_age_query_reads_it_the_same_way(self):
        self.mod.query = lambda sql: [["bogus", -130.0], ["fine", 9.0]]
        ages = self.mod.upload_ages_in_days({"bogus", "fine"})
        self.assertEqual({"bogus": self.stand_in, "fine": 9.0}, ages)


class WhatItStopsHappening(unittest.TestCase):

    def setUp(self):
        self.mod = load(IV_SUGGEST_ACCOUNT=ME)

    def test_an_intake_filter_rejects_it_exactly_as_it_rejects_no_date(self):
        cutoff = time.time() - 30 * 86400.0
        future = {"published": time.time() + A_YEAR_AHEAD}
        self.assertTrue(self.mod.is_too_old(future, cutoff))
        self.assertTrue(self.mod.is_too_old({}, cutoff))

    def test_it_no_longer_leads_a_lane_ranked_by_upload_date(self):
        """subs-live holds `rank_order: published`, which sorts ascending."""
        items = [self.mod.item_to_rank([1, "bogus", "UC", 1.0, 0, 1.0, 1.0, "t", -130.0]),
                 self.mod.item_to_rank([2, "today", "UC", 1.0, 0, 1.0, 1.0, "t", 0.5])]
        cfg = dict(self.mod.SHUFFLE_DEFAULTS, rank_order="published")
        self.assertEqual("today", self.mod.rank_items(items, cfg)[0]["vid"])

    def test_it_no_longer_takes_the_full_upload_recency_boost(self):
        cfg = dict(self.mod.SHUFFLE_DEFAULTS, published_halflife_days=21,
                   jitter=0, recency_boost=0)
        bogus = self.mod.item_to_rank([1, "bogus", "UC", 1.0, 0, 1.0, 1.0, "t", -130.0])
        today = self.mod.item_to_rank([2, "today", "UC", 1.0, 0, 1.0, 1.0, "t", 0.0])
        self.assertLess(self.mod.display_score(bogus, cfg),
                        self.mod.display_score(today, cfg))


if __name__ == "__main__":
    unittest.main()

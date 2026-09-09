"""Retiring what nobody watched, counted in days somebody actually watched.

Calendar days would be wrong: a fortnight away would empty every lane and the
next run would refill it from scratch. So the clock is `suggest.plays`. A
holiday costs nothing; three days of use retire what three days of use ignored.

`ttl_days` stays underneath as the calendar backstop, which is also what covers
an account the harvest records no plays for.
"""

import unittest

from support import load

ME = "andre@example.com"


class TheCutoff(unittest.TestCase):

    def setUp(self):
        self.mod = load(IV_SUGGEST_ACCOUNT=ME)
        self.asked = []
        self.mod.one = lambda sql: self.asked.append(sql) or "2026-09-07"

    def test_zero_is_off_and_asks_the_database_nothing(self):
        self.assertEqual("", self.mod.date_this_many_active_days_ago(0))
        self.assertEqual([], self.asked)

    def test_it_counts_distinct_days_of_watching(self):
        self.mod.date_this_many_active_days_ago(3)
        sql = self.asked[0]
        self.assertIn("DISTINCT played::date", sql)
        self.assertIn("LIMIT 3", sql)
        self.assertIn("HAVING count(*) = 3", sql)

    def test_too_little_history_yields_no_cutoff_rather_than_a_recent_one(self):
        """Fewer than N days of use must retire nothing, not everything."""
        self.mod.one = lambda sql: ""
        self.assertEqual("", self.mod.date_this_many_active_days_ago(3))

    def test_only_this_account_counts_towards_its_own_cutoff(self):
        self.mod.date_this_many_active_days_ago(3)
        self.assertIn("account='%s'" % ME, self.asked[0])


class WhyItLeaves(unittest.TestCase):

    def setUp(self):
        self.mod = load(IV_SUGGEST_ACCOUNT=ME)
        self.run = self.mod.LaneRun([], {"seen0000001"}, set(), {"UCblocked"},
                                    None, True, None)
        self.lane = dict(self.mod.DEFAULTS)
        self.added = {"old00000001": "2026-09-01", "new00000001": "2026-09-08"}

    def why(self, vid, ttl="", stale=""):
        return self.mod.why_it_leaves({"videoId": vid, "authorId": "UCx"},
                                      self.lane, self.run, self.added, ttl, stale)

    def test_no_cutoff_keeps_everything_it_used_to_keep(self):
        self.assertIsNone(self.why("old00000001"))

    def test_an_entry_older_than_the_cutoff_leaves_as_unwatched(self):
        self.assertEqual("unwatched", self.why("old00000001", stale="2026-09-07"))

    def test_an_entry_added_since_stays(self):
        self.assertIsNone(self.why("new00000001", stale="2026-09-07"))

    def test_an_entry_added_on_the_cutoff_day_gets_the_whole_day(self):
        """Strictly before, so the day it arrived is never counted against it."""
        self.added["edge00000001"] = "2026-09-07"
        self.assertIsNone(self.why("edge00000001", stale="2026-09-07"))

    def test_watched_still_wins_the_reason_because_the_cooldowns_differ(self):
        self.added["seen0000001"] = "2026-09-01"
        self.assertEqual("watched", self.why("seen0000001", stale="2026-09-07"))

    def test_the_ttl_still_wins_over_the_active_day_rule(self):
        self.assertEqual("expired",
                         self.why("old00000001", ttl="2026-09-05",
                                  stale="2026-09-07"))


class Cooldown(unittest.TestCase):

    def setUp(self):
        self.mod = load(IV_SUGGEST_ACCOUNT=ME)
        self.lane = dict(self.mod.DEFAULTS)

    def test_ignored_is_held_out_as_long_as_rotated_not_as_long_as_watched(self):
        """60 days for not clicking something would starve the candidate pool."""
        self.assertEqual(self.lane["rotate_cooldown_days"],
                         self.mod.cooldown_days_for(self.lane, "unwatched"))
        self.assertLess(self.mod.cooldown_days_for(self.lane, "unwatched"),
                        self.mod.cooldown_days_for(self.lane, "watched"))


class Defaults(unittest.TestCase):

    def setUp(self):
        self.mod = load(IV_SUGGEST_ACCOUNT=ME)

    def test_every_new_key_ships_off(self):
        """A lane that asks for none of this must behave exactly as it did."""
        self.assertEqual(0, self.mod.DEFAULTS["stale_after_active_days"])
        self.assertEqual(0, self.mod.DEFAULTS["recommend_age_halflife_days"])
        self.assertEqual(0, self.mod.SHUFFLE_DEFAULTS["published_halflife_days"])
        self.assertEqual(0, self.mod.SHUFFLE_DEFAULTS["unwatched_halflife_days"])


if __name__ == "__main__":
    unittest.main()

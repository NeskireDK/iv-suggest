"""Dating `users.watched`, which is the only per-account trace of a play.

The other half of the harvest infers a play from Invidious refreshing its own
metadata cache. That refresh carries no identity, so it can only ever speak for
one account, and `get_video` skips it for anything fetched in the last ten
minutes — measured on the live instance, 113 of the last 500 watch-history
entries had no play row at all.

`users.watched` has every one of them and no clock. This half dates its growth.
"""

import unittest

from support import load

ME = "andre@example.com"
THEM = "sofie@example.com"


class WhatCountsAsPlayedSince(unittest.TestCase):

    def setUp(self):
        self.mod = load(IV_SUGGEST_ACCOUNT=ME)

    def test_everything_after_the_marked_video_is_new(self):
        self.assertEqual(["c", "d"], self.mod.played_since_the_mark(
            ["a", "b", "c", "d"], ("b", 2)))

    def test_a_rewatch_counts_because_invidious_moves_the_id_to_the_end(self):
        """array_append(array_remove(watched, id), id). The move IS the play."""
        self.assertEqual(["a"], self.mod.played_since_the_mark(
            ["b", "c", "a"], ("c", 3)))

    def test_nothing_new_means_nothing_played(self):
        self.assertEqual([], self.mod.played_since_the_mark(
            ["a", "b"], ("b", 2)))

    def test_a_cleared_history_falls_back_to_the_length_it_had(self):
        """The marked id is gone, so position cannot answer; the growth still can."""
        self.assertEqual(["y", "z"], self.mod.played_since_the_mark(
            ["w", "x", "y", "z"], ("gone", 2)))

    def test_a_history_that_shrank_claims_nothing(self):
        self.assertEqual([], self.mod.played_since_the_mark(
            ["a"], ("gone", 9)))


class AFirstLook(unittest.TestCase):
    """Forward-only by construction: there are no dates behind the mark to invent."""

    def setUp(self):
        self.mod = load(IV_SUGGEST_ACCOUNT=ME)
        self.mod.watched_array = lambda email: ["a", "b", "c"]
        self.mod.watch_mark = lambda email: None
        self.written = []
        self.mod.execute = self.written.append
        self.mod.record_a_play = lambda email, vid: self.fail(
            "a first look claimed a play it cannot date")

    def test_it_records_the_mark_and_claims_no_play(self):
        self.assertEqual(0, self.mod.harvest_one_watched_array(ME, dry=False))
        self.assertEqual(1, len(self.written))
        self.assertIn("watch_marks", self.written[0])
        self.assertIn("'c'", self.written[0])

    def test_a_dry_first_look_writes_no_mark_either(self):
        self.assertEqual(0, self.mod.harvest_one_watched_array(ME, dry=True))
        self.assertEqual([], self.written)


class ALaterLook(unittest.TestCase):

    def setUp(self):
        self.mod = load(IV_SUGGEST_ACCOUNT=ME)
        self.mod.watched_array = lambda email: ["a", "b", "c", "d"]
        self.mod.watch_mark = lambda email: ("b", 2)
        self.recorded = []
        self.mod.record_a_play = lambda email, vid: \
            self.recorded.append((email, vid))
        self.marks = []
        self.mod.note_watch_mark = lambda email, watched: \
            self.marks.append((email, watched))

    def test_each_new_entry_is_recorded_as_a_play(self):
        self.assertEqual(2, self.mod.harvest_one_watched_array(ME, dry=False))
        self.assertEqual([(ME, "c"), (ME, "d")], self.recorded)

    def test_the_mark_moves_to_the_end_of_the_array(self):
        self.mod.harvest_one_watched_array(ME, dry=False)
        self.assertEqual([(ME, ["a", "b", "c", "d"])], self.marks)

    def test_a_dry_run_counts_them_and_writes_nothing(self):
        self.assertEqual(2, self.mod.harvest_one_watched_array(ME, dry=True))
        self.assertEqual([], self.recorded)
        self.assertEqual([], self.marks)


class EveryServedAccount(unittest.TestCase):
    """The reason this half exists: `mark_watched` can only speak for PRIMARY."""

    def setUp(self):
        self.mod = load(IV_SUGGEST_ACCOUNT=ME)
        self.looked = []
        self.mod.harvest_one_watched_array = lambda email, dry: \
            self.looked.append(email) or 1

    def test_it_reads_the_accounts_table_rather_than_the_config(self):
        """A quarter-hourly timer must not depend on parsing lanes.yml."""
        asked = []
        self.mod.query = lambda sql: asked.append(sql) or [[ME], [THEM]]
        self.assertEqual(2, self.mod.harvest_every_watched_array(dry=False))
        self.assertEqual([ME, THEM], self.looked)
        self.assertIn("suggest.accounts", asked[0])


class TheDoubleCountGuard(unittest.TestCase):
    """The API half marks a video watched, which appends it to this very array."""

    def setUp(self):
        self.mod = load(IV_SUGGEST_ACCOUNT=ME)
        self.written = []
        self.mod.execute = self.written.append
        self.mod.record_a_play(THEM, "dQw4w9WgXcQ")
        self.sql = self.written[0]

    def test_it_refuses_a_play_already_recorded_for_that_account(self):
        self.assertIn("NOT EXISTS", self.sql)
        self.assertIn("'%s'" % THEM, self.sql)
        self.assertIn("%d minutes" % self.mod.ALREADY_RECORDED_MINUTES, self.sql)

    def test_it_denormalises_the_channel_onto_the_row(self):
        """Affinity must not depend on video_meta still holding the video."""
        self.assertIn("author_id", self.sql)
        self.assertIn("ucid", self.sql)

    def test_it_is_recorded_as_watched_rather_than_a_new_outcome(self):
        """Three outcomes are what the metrics group by; this is not a fourth."""
        self.assertIn("'%s'" % self.mod.WATCHED, self.sql)
        self.assertIn(self.mod.SEEN_IN_HISTORY, self.sql)


if __name__ == "__main__":
    unittest.main()

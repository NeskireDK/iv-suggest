"""Counting what Invidious fetched, from Invidious' own record.

`videos.updated` is when Invidious last went and got that video, so the rows
that moved since the previous sample ARE the fetches over that span. No
correlation with this engine's calls, no clock window, no latency threshold --
which is what the column it replaces needed, and got wrong eleven times.

It counts EVERYONE's fetches, the household's browsing as well as this bot's.
That is the point: it answers "how hard is the instance leaning on YouTube",
which is the question the rate limit is about.

Two properties matter and neither is obvious, so both are asserted here: the
spans have to be contiguous, or fetches fall between two samples and are never
counted; and a gap has to be reported rather than read as a quiet hour, because
Invidious deletes the rows after six hours and then the fetches are gone.
"""

import time
import unittest

from support import load
from synthetic import ALICE, LANES_FILE, Database, require_docker

HARNESS = {}


def setUpModule():
    require_docker()
    engine = load(IV_SUGGEST_ACCOUNT=ALICE.email, IV_SUGGEST_CONFIG=LANES_FILE)
    db = Database().start()
    db.attach(engine)
    engine.execute(engine.SCHEMA_SQL)
    HARNESS.update(engine=engine, db=db)


def tearDownModule():
    HARNESS["db"].stop()


class Sampling(unittest.TestCase):

    def setUp(self):
        self.engine = HARNESS["engine"]
        self.db = HARNESS["db"]
        self.db.psql("DELETE FROM suggest.upstream_samples; DELETE FROM videos;")

    def invidious_fetches(self, *vids):
        for vid in vids:
            self.db.psql("INSERT INTO videos(id, info, updated) VALUES "
                         "('%s','{}', now()) ON CONFLICT (id) DO UPDATE "
                         "SET updated = now();" % vid)

    def sample(self):
        self.engine.sample_upstream_fetches()
        return int(self.db.value(
            "SELECT videos FROM suggest.upstream_samples ORDER BY at DESC "
            "LIMIT 1;") or 0)

    def spans(self):
        return self.db.rows("SELECT since, at FROM suggest.upstream_samples "
                            "ORDER BY at;")

    def test_a_first_sample_counts_nothing_behind_it(self):
        """Invidious keeps six hours of rows; counting them as this sample's
        would report a day's fetches as half an hour's."""
        self.invidious_fetches("aaaaaaaaaaa")
        self.assertEqual(0, self.sample())

    def test_the_next_sample_counts_what_was_fetched_since(self):
        self.sample()
        self.invidious_fetches("aaaaaaaaaaa", "bbbbbbbbbbb")
        self.assertEqual(2, self.sample())

    def test_a_sample_with_nothing_new_counts_nothing(self):
        self.sample()
        self.invidious_fetches("aaaaaaaaaaa")
        self.sample()
        self.assertEqual(0, self.sample())

    def test_nothing_is_counted_twice(self):
        self.sample()
        self.invidious_fetches("aaaaaaaaaaa")
        first = self.sample()
        second = self.sample()
        self.assertEqual(1, first)
        self.assertEqual(0, second)

    def test_the_spans_touch_so_nothing_falls_between_them(self):
        for _ in range(3):
            self.sample()
        spans = self.spans()
        self.assertEqual(3, len(spans))
        for earlier, later in zip(spans, spans[1:]):
            self.assertEqual(earlier[1], later[0])

    def test_one_video_fetched_twice_in_a_span_counts_once(self):
        """The known undercount: the row holds one value, so a re-fetch inside
        one span is invisible. Written down rather than papered over."""
        self.sample()
        self.invidious_fetches("aaaaaaaaaaa")
        self.invidious_fetches("aaaaaaaaaaa")
        self.assertEqual(1, self.sample())

    def test_no_two_spans_ever_overlap(self):
        """Overlap counts one span twice and drives the reported gap below
        zero, which reads healthier than a real gap does."""
        for _ in range(5):
            self.invidious_fetches("aaaaaaaaaaa")
            self.sample()
        spans = self.spans()
        for earlier, later in zip(spans, spans[1:]):
            self.assertLessEqual(earlier[1], later[0])

    def test_every_span_runs_forwards(self):
        """`now()` is the TRANSACTION time, fixed at BEGIN and so before the
        advisory lock is granted: a run that began earlier and won the lock
        later would stamp a row whose `at` precedes its own `since`. The
        sampler reads clock_timestamp() instead, after the wait."""
        for _ in range(4):
            self.sample()
        spans = self.spans()
        for since, at in spans:
            self.assertLessEqual(since, at,
                                 "%s .. %s runs backwards" % (since, at))
        for since, at in spans[1:]:
            self.assertLess(since, at, "only the first span is zero length")

    def test_it_survives_the_table_not_existing_yet(self):
        """A tag pull lands before `init`, and the harvest fires every half
        hour in between."""
        self.db.psql("ALTER TABLE suggest.upstream_samples "
                     "RENAME TO upstream_samples_hidden;")
        try:
            self.engine.sample_upstream_fetches()
        finally:
            self.db.psql("ALTER TABLE suggest.upstream_samples_hidden "
                         "RENAME TO upstream_samples;")


class TheReportedTotal(unittest.TestCase):

    def setUp(self):
        self.engine = HARNESS["engine"]
        self.db = HARNESS["db"]
        self.db.psql("DELETE FROM suggest.upstream_samples; DELETE FROM videos;")

    def test_the_gauge_sums_the_samples_over_the_window(self):
        self.db.psql(
            "INSERT INTO suggest.upstream_samples(at, since, videos) VALUES "
            "(now() - interval '10 min', now() - interval '40 min', 12),"
            "(now(), now() - interval '10 min', 5);")
        self.assertEqual([("", "17")], self.engine.upstream_sampled("1 day"))

    def test_the_longest_span_is_what_says_something_was_lost(self):
        """It survives the harvest coming back, which is the whole point: a
        span past the cache lifetime means Invidious deleted rows inside it."""
        self.db.psql(
            "INSERT INTO suggest.upstream_samples(at, since, videos) VALUES "
            "(now() - interval '20 min', now() - interval '9 hours', 3),"
            "(now(), now() - interval '20 min', 1);")
        longest = int(self.engine.upstream_longest_span("1 day")[0][1])
        self.assertAlmostEqual(9 * 3600 - 20 * 60, longest, delta=60)

    def test_no_span_at_all_is_zero_not_missing(self):
        self.assertEqual(0, int(self.engine.upstream_longest_span("1 day")[0][1]))

class TheBotsOwnShare(unittest.TestCase):
    """`bot_fetches_24h` and `cache_served_calls_24h`, which carry the one
    inference left in the metrics: a channel listing that answered cleanly went
    out, because Invidious never caches them. It used to be tested where the
    verdict lived, and that module is gone."""

    def setUp(self):
        self.engine = HARNESS["engine"]
        self.db = HARNESS["db"]
        self.db.psql("DELETE FROM suggest.fetches;")

    def call(self, kind, moved="NULL", status=200):
        self.db.psql("INSERT INTO suggest.fetches"
                     "(at,account,job,kind,target,status,attempt,error,ms,"
                     "cache_row_moved) VALUES "
                     "(now(), 'a@b', 'run', '%s', 'x', %d, 0, '', 9, %s);"
                     % (kind, status, moved))

    def counted(self, sampler, kind):
        return dict(sampler).get('{kind="%s"}' % kind)

    def bot(self):
        """The gauge's own clause, not a copy of it: a copy would keep passing
        after the gauge changed."""
        return self.engine.fetch_samples(
            self.engine.WENT_OUT_SQL, "kind", self.engine.CAN_REACH_KINDS)

    def served(self):
        return self.engine.fetch_samples(
            self.engine.CACHE_SERVED_SQL, "kind", self.engine.GET_VIDEO_KINDS)

    def test_a_video_whose_row_moved_counts_as_a_fetch(self):
        self.call("video", moved="true")
        self.assertEqual(1, self.counted(self.bot(), "video"))
        self.assertEqual(0, self.counted(self.served(), "video"))

    def test_a_video_the_cache_answered_counts_the_other_way(self):
        self.call("video", moved="false")
        self.assertEqual(0, self.counted(self.bot(), "video"))
        self.assertEqual(1, self.counted(self.served(), "video"))

    def test_a_clean_channel_listing_counts_as_a_fetch(self):
        self.call("channel_latest")
        self.assertEqual(1, self.counted(self.bot(), "channel_latest"))

    def test_a_refused_channel_listing_does_not(self):
        """Retried three times, so counting one would log three fetches that
        never happened."""
        for status in (403, 429):
            self.db.psql("DELETE FROM suggest.fetches;")
            self.call("channel_latest", status=status)
            self.assertEqual(0, self.counted(self.bot(), "channel_latest"),
                             "status %d" % status)

    def test_a_failed_video_call_counts_as_neither(self):
        """The error path deletes the row, so nothing moved -- and a failure
        must not read as a clean cache answer either."""
        self.call("video", moved="false", status=500)
        self.assertEqual(0, self.counted(self.bot(), "video"))
        self.assertEqual(0, self.counted(self.served(), "video"))

    def test_a_playlist_read_is_in_neither(self):
        self.call("playlist_read")
        self.assertIsNone(self.counted(self.bot(), "playlist_read"))


if __name__ == "__main__":
    unittest.main()

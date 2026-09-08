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

    def test_a_window_no_sample_covers_is_reported_as_a_gap(self):
        """Otherwise a stopped harvest and a quiet night are the same number."""
        self.db.psql(
            "INSERT INTO suggest.upstream_samples(at, since, videos) VALUES "
            "(now(), now() - interval '10 minutes', 3);")
        gap = int(self.engine.upstream_sample_gap("1 hour")[0][1])
        self.assertAlmostEqual(3000, gap, delta=60)

    def test_a_fully_covered_window_has_no_gap(self):
        self.db.psql(
            "INSERT INTO suggest.upstream_samples(at, since, videos) VALUES "
            "(now(), now() - interval '2 hours', 3);")
        self.assertEqual(0, int(self.engine.upstream_sample_gap("1 hour")[0][1]))

    def test_no_samples_at_all_is_the_whole_window(self):
        gap = int(self.engine.upstream_sample_gap("1 hour")[0][1])
        self.assertAlmostEqual(3600, gap, delta=60)


if __name__ == "__main__":
    unittest.main()

"""What the call log records about Invidious' cache, against a real PostgreSQL.

`cache_row_moved` is an observation, not a verdict. `get_video` rewrites
`videos.updated` only when it really fetches, so a move inside the call's own
window is that fetch and no move is the cache answering. Nothing else is
claimed: NULL for a kind that has no such row.

An earlier column here tried to be the verdict -- whether the call reached
YouTube -- by ranking the row against the call duration, a latency ceiling and
a per-kind reading of every failure status. Eleven review rounds found eleven
ways for it to be wrong, every one of them counting a call that never left the
machine. This is what is left once the guessing is removed, and the totals come
from `suggest.upstream_samples`, which reads Invidious' own record instead.
"""

import time
import unittest

from support import load
from synthetic import ALICE, LANES_FILE, Database, require_docker

HARNESS = {}
VID = "dQw4w9WgXcQ"


def setUpModule():
    require_docker()
    engine = load(IV_SUGGEST_ACCOUNT=ALICE.email, IV_SUGGEST_CONFIG=LANES_FILE)
    db = Database().start()
    db.attach(engine)
    engine.execute(engine.SCHEMA_SQL)
    HARNESS.update(engine=engine, db=db)


def tearDownModule():
    HARNESS["db"].stop()


class Observation(unittest.TestCase):

    def setUp(self):
        self.engine = HARNESS["engine"]
        self.db = HARNESS["db"]
        self.db.psql("DELETE FROM suggest.fetches; DELETE FROM videos;")

    def cache_row(self, vid, moved_at):
        self.db.psql("INSERT INTO videos(id, info, updated) "
                     "VALUES ('%s','{}', %s);" % (vid, moved_at))

    def observed(self, kind, target=VID, ms=7, status=200):
        self.engine.LANE, self.engine.JOB = "autos", "run"
        self.engine.FETCH_LOG[:] = []
        self.engine.note_fetch(time.time(), kind, target, status, 0, "", ms)
        self.engine.flush_fetch_log()
        return self.db.value("SELECT coalesce(cache_row_moved::text, 'null') "
                             "FROM suggest.fetches;")

    def test_a_row_that_moved_during_the_call_is_the_fetch(self):
        self.cache_row(VID, "now()")
        self.assertEqual("true", self.observed("video"))

    def test_a_row_left_alone_is_the_cache_answering(self):
        self.cache_row(VID, "now() - interval '1 hour'")
        self.assertEqual("false", self.observed("video"))

    def test_no_row_at_all_is_not_a_move(self):
        """A failed fetch deletes the row, so this is what a failure leaves."""
        self.assertEqual("false", self.observed("video", status=500))

    def test_a_playlist_add_is_observed_the_same_way(self):
        """It reaches `get_video` too, so it leaves the same evidence."""
        self.cache_row(VID, "now()")
        self.assertEqual("true", self.observed("playlist_add"))

    def test_a_channel_listing_has_no_row_to_observe(self):
        """Invidious does not cache them, so nothing is claimed either way."""
        self.assertEqual("null", self.observed("channel_latest", target="UCabc"))

    def test_a_playlist_read_has_none_either(self):
        self.assertEqual("null", self.observed("playlist_read", target="IVPLx"))

    def test_a_move_a_second_before_the_call_is_not_this_calls(self):
        """In a fill the metadata fetch and the playlist add of one video are a
        fraction of a second apart; a wide window would count both."""
        self.cache_row(VID, "now() - interval '1 second'")
        self.assertEqual("false", self.observed("playlist_add"))

    def test_the_duration_is_recorded_but_decides_nothing(self):
        """It is published as a percentile, not used as a threshold -- the
        6ms figure it would have to come from was measured at idle."""
        self.cache_row(VID, "now() - interval '1 hour'")
        self.assertEqual("false", self.observed("video", ms=30000))


class TheClockOffset(unittest.TestCase):
    """`at` comes from this process, `videos.updated` from the database. The
    window between them is 50ms wide, so they have to be on one clock."""

    def setUp(self):
        self.engine = HARNESS["engine"]
        self.engine._CLOCK_OFFSET[:] = []

    def test_it_is_measured_once_and_reused(self):
        """A run is minutes long and two machines' clocks do not drift inside
        it, so this must not cost a round trip per flush."""
        asked = []
        real = self.engine.one
        self.engine.one = lambda sql: asked.append(sql) or real(sql)
        try:
            for _ in range(4):
                self.engine.clock_offset()
        finally:
            self.engine.one = real
        self.assertEqual(1, len(asked))
        self.assertIn("clock_timestamp", asked[0])

    def test_it_is_small_against_a_shared_clock(self):
        """The synthetic database runs on this machine, so the only thing left
        to measure is the transport, and half of it is corrected away."""
        self.assertLess(abs(self.engine.clock_offset()), 0.5)

    def test_it_does_not_charge_the_transport_to_the_offset(self):
        """A naive `now() - to_timestamp(before)` measures the offset plus the
        psql spawn and connect, all of it biased one way against a 50ms
        window."""
        real = self.engine.one
        self.engine.one = lambda sql: (time.sleep(0.4), real(sql))[1]
        try:
            offset = self.engine.clock_offset()
        finally:
            self.engine.one = real
        self.assertLess(abs(offset), 0.25)


class TheLatencyPercentiles(unittest.TestCase):
    """Measured over the calls that went OUT, not over every call.

    Percentiled over all of them it would track the cache hit ratio instead of
    YouTube: a thousand calls with ten real fetches at 5000ms puts p95 at 7ms,
    so the warning it exists to give disappears exactly when the cache is warm.
    """

    def setUp(self):
        self.engine = HARNESS["engine"]
        self.db = HARNESS["db"]
        self.db.psql("DELETE FROM suggest.fetches;")

    def calls(self, kind, moved, *durations):
        rows = ",".join(
            "(now(), 'a@b', 'run', '%s', 'x', 200, 0, '', %d, %s)"
            % (kind, ms, moved) for ms in durations)
        self.db.psql("INSERT INTO suggest.fetches"
                     "(at,account,job,kind,target,status,attempt,error,ms,"
                     "cache_row_moved) VALUES %s;" % rows)

    def sample(self, kind, quantile):
        wanted = '{kind="%s",quantile="%s"}' % (kind, quantile)
        return dict(self.engine.latency_samples("1 day")).get(wanted)

    def test_the_median_and_the_tail_are_both_published(self):
        self.calls("video", "true", *([900] * 90 + [5000] * 10))
        self.assertEqual("900", self.sample("video", "0.5"))
        self.assertEqual("5000", self.sample("video", "0.95"))

    def test_a_cache_answer_is_not_measured_at_all(self):
        """The dilution this exists to avoid: ninety of them would drag p95
        down to a number that says nothing about YouTube."""
        self.calls("video", "false", *([7] * 90))
        self.calls("video", "true", *([5000] * 10))
        self.assertEqual("5000", self.sample("video", "0.5"))

    def test_a_channel_listing_is_measured_because_it_always_goes_out(self):
        """Invidious does not cache them, so there is no row to have moved."""
        self.calls("channel_latest", "NULL", 600, 650, 700)
        self.assertEqual("650", self.sample("channel_latest", "0.5"))

    def test_each_sort_is_measured_separately(self):
        self.calls("video", "true", 2270)
        self.calls("channel_latest", "NULL", 600)
        self.assertEqual("2270", self.sample("video", "0.5"))
        self.assertEqual("600", self.sample("channel_latest", "0.5"))

    def test_a_sort_that_never_leaves_the_machine_is_not_measured(self):
        self.calls("playlist_read", "NULL", 14)
        self.assertIsNone(self.sample("playlist_read", "0.5"))


if __name__ == "__main__":
    unittest.main()

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


class TheLatencyPercentiles(unittest.TestCase):
    """Published as p50 and p95 rather than a mean, and used as a signal rather
    than a threshold: a rising p95 on `video` is YouTube getting slow, which
    arrives before it starts refusing."""

    def setUp(self):
        self.engine = HARNESS["engine"]
        self.db = HARNESS["db"]
        self.db.psql("DELETE FROM suggest.fetches;")

    def calls(self, kind, *durations):
        rows = ",".join("(now(), 'a@b', 'run', '%s', 'x', 200, 0, '', %d)"
                        % (kind, ms) for ms in durations)
        self.db.psql("INSERT INTO suggest.fetches"
                     "(at,account,job,kind,target,status,attempt,error,ms) "
                     "VALUES %s;" % rows)

    def sample(self, kind, quantile):
        wanted = '{kind="%s",quantile="%s"}' % (kind, quantile)
        return dict(self.engine.latency_samples("1 day")).get(wanted)

    def test_the_median_and_the_tail_are_both_published(self):
        self.calls("video", *([7] * 90 + [2270] * 10))
        self.assertEqual("7", self.sample("video", "0.5"))
        self.assertEqual("2270", self.sample("video", "0.95"))

    def test_the_tail_is_what_a_mean_would_hide(self):
        """Ninety cache answers and ten real fetches average to 233ms, which
        looks like neither."""
        self.calls("video", *([7] * 90 + [2270] * 10))
        self.assertGreater(int(self.sample("video", "0.95")), 1000)
        self.assertLess(int(self.sample("video", "0.5")), 100)

    def test_each_sort_is_measured_separately(self):
        self.calls("video", 2270)
        self.calls("channel_latest", 600)
        self.assertEqual("2270", self.sample("video", "0.5"))
        self.assertEqual("600", self.sample("channel_latest", "0.5"))

    def test_a_sort_that_never_leaves_the_machine_is_not_measured(self):
        self.calls("playlist_read", 14)
        self.assertIsNone(self.sample("playlist_read", "0.5"))


if __name__ == "__main__":
    unittest.main()

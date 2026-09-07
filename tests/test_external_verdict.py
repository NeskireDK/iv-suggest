"""Whether a call really left the machine, decided against a real PostgreSQL.

`external` is a SQL expression, so a test that greps the expression proves
nothing about what it decides -- and the first version of it had a clause that
could never be reached, because a transport failure carries an error string like
any other failure and the error alone was enough to mark the call as having gone
out. This runs the real expression over a matrix of real rows.

Three sorts of evidence, and which of them counts depends on the call:

- **How long it took.** Measured on the reference instance: 6-7 ms for a call
  Invidious served from its own cache, 530-680 ms for a channel listing and
  1648-2270 ms for a video it had to go and get.
- **Whether the cache row moved.** `get_video` rewrites `videos.updated` only
  when it actually fetches, so a move inside the call's own window is proof.
- **How it failed.** A 404 on a video is Invidious reporting that upstream has
  lost it, which it had to ask to find out. The same status on a playlist add is
  an expired session or a missing playlist and never reached `get_video`.
"""

import time
import unittest

from support import load
from synthetic import ALICE, LANES_FILE, Database, require_docker

HARNESS = {}
VID = "dQw4w9WgXcQ"
FAST, SLOW = 7, 2270


def setUpModule():
    require_docker()
    engine = load(IV_SUGGEST_ACCOUNT=ALICE.email, IV_SUGGEST_CONFIG=LANES_FILE)
    db = Database().start()
    db.attach(engine)
    engine.execute(engine.SCHEMA_SQL)
    HARNESS.update(engine=engine, db=db)


def tearDownModule():
    HARNESS["db"].stop()


class Case:
    """One call put through the real expression, against real rows.

    Not a TestCase itself, or every subclass would re-run its cases.
    """

    def setUp(self):
        self.engine = HARNESS["engine"]
        self.db = HARNESS["db"]
        self.db.psql("DELETE FROM suggest.fetches; DELETE FROM videos;")

    def cache_row_for(self, vid, moved):
        """A `videos` row either refreshed by this call or left alone by it."""
        when = "now()" if moved else "now() - interval '1 hour'"
        self.db.psql("INSERT INTO videos(id, info, updated) VALUES ('%s','{}',%s);"
                     % (vid, when))

    def verdict(self, kind, status=200, ms=FAST, error="", target=VID):
        self.engine.LANE, self.engine.JOB = "autos", "run"
        self.engine.FETCH_LOG[:] = []
        self.engine.note_fetch(time.time(), kind, target, status, 0, error, ms)
        self.engine.flush_fetch_log()
        return self.db.value("SELECT external FROM suggest.fetches;")


class AnyCall(Case, unittest.TestCase):

    def test_a_channel_listing_always_went_out(self):
        self.assertEqual("t", self.verdict("channel_latest", target="UCabc"))

    def test_a_playlist_read_never_did(self):
        self.assertEqual("f", self.verdict("playlist_read", target="IVPLx"))

    def test_a_fast_video_call_with_an_untouched_row_was_the_cache(self):
        self.cache_row_for(VID, moved=False)
        self.assertEqual("f", self.verdict("video"))

    def test_a_slow_video_call_went_out_even_with_no_row_to_show(self):
        """The row is the better evidence, but it is not always available."""
        self.assertEqual("t", self.verdict("video", ms=SLOW))

    def test_a_fast_video_call_whose_row_moved_went_out(self):
        self.cache_row_for(VID, moved=True)
        self.assertEqual("t", self.verdict("video"))

    def test_a_video_upstream_has_lost_went_out(self):
        self.assertEqual("t", self.verdict("video", status=404))

    def test_a_200_carrying_an_error_went_out(self):
        """Invidious asked, could not parse the answer, and deleted the row."""
        self.assertEqual("t", self.verdict("video", error="could not parse"))

    def test_a_call_nothing_answered_did_not_go_out(self):
        """Status 0 never reached Invidious, let alone YouTube. It carries an
        error string like any other failure, which is why the error alone
        cannot be the test -- the first version of this got it wrong."""
        self.assertEqual("f", self.verdict(
            "video", status=0, ms=30, error="URLError('timed out')"))


class APlaylistAdd(Case, unittest.TestCase):
    """It reaches `get_video` too, so it is judged like a video -- except when
    it fails, because a refusal there is about the playlist, not upstream."""

    def test_a_fast_one_off_a_warm_row_was_the_cache(self):
        self.cache_row_for(VID, moved=False)
        self.assertEqual("f", self.verdict("playlist_add"))

    def test_one_whose_row_moved_went_out(self):
        self.cache_row_for(VID, moved=True)
        self.assertEqual("t", self.verdict("playlist_add"))

    def test_an_expired_session_did_not_go_out(self):
        """401 and 403 are refused before `get_video` is ever reached."""
        for status in (401, 403, 404):
            self.db.psql("DELETE FROM suggest.fetches;")
            self.assertEqual("f", self.verdict("playlist_add", status=status),
                             "status %d" % status)

    def test_a_server_error_did(self):
        self.assertEqual("t", self.verdict("playlist_add", status=500))


if __name__ == "__main__":
    unittest.main()

"""Dating every call the engine makes, because nothing else does.

`suggest.runs` carries one fetch count per lane per night, so "how many
requests reached YouTube in that hour, for whom, of what sort" had no answer at
all. This is the log that answers it, written at the one choke point every call
already passes through.

Two things it must not get wrong. Counting every call as a request to YouTube
would overstate the number several times over -- most of them are answered from
the Invidious database and never leave the machine. And a dry run makes real
read calls, so a run that writes nothing else must not write these either.
"""

import unittest
import urllib.error

from support import load

ME = "andre@example.com"
VID = "dQw4w9WgXcQ"
UCID = "UCsXVk37bltHxD1rDPwtNM8Q"


def engine(**env):
    mod = load(IV_SUGGEST_ACCOUNT=ME, **env)
    mod.ACCOUNT = ME
    return mod


class WhatSortOfCallItWas(unittest.TestCase):
    def setUp(self):
        self.mod = engine()

    def kind(self, method, path):
        return self.mod.kind_of_call(method, path)

    def test_a_video_and_a_channel_listing_are_the_only_upstream_sorts(self):
        """Everything else is answered from the Invidious database."""
        self.assertEqual(("video", "channel_latest"), self.mod.UPSTREAM_KINDS)

    def test_it_names_each_sort(self):
        self.assertEqual("video", self.kind("GET", "/api/v1/videos/" + VID))
        self.assertEqual("channel_latest",
                         self.kind("GET", "/api/v1/channels/%s/latest" % UCID))
        self.assertEqual("stats", self.kind("GET", "/api/v1/stats"))
        self.assertEqual("history_write",
                         self.kind("POST", "/api/v1/auth/history/" + VID))
        self.assertEqual("playlist_read",
                         self.kind("GET", "/api/v1/auth/playlists/IVPLx"))
        self.assertEqual("playlist_write",
                         self.kind("POST", "/api/v1/auth/playlists/IVPLx/videos"))
        self.assertEqual("playlist_write",
                         self.kind("DELETE", "/api/v1/auth/playlists/IVPLx/videos/7"))

    def test_a_playlist_read_is_never_counted_as_a_request_to_youtube(self):
        for method, path in (("GET", "/api/v1/auth/playlists"),
                             ("PATCH", "/api/v1/auth/playlists/IVPLx"),
                             ("GET", "/api/v1/stats")):
            self.assertNotIn(self.kind(method, path), self.mod.UPSTREAM_KINDS)


class WhatTheCallWasAbout(unittest.TestCase):
    def setUp(self):
        self.mod = engine()

    def test_a_video_call_names_the_video(self):
        self.assertEqual(VID, self.mod.target_of_call("/api/v1/videos/" + VID, None))

    def test_an_added_video_names_the_video_not_the_playlist(self):
        self.assertEqual(VID, self.mod.target_of_call(
            "/api/v1/auth/playlists/IVPLx/videos", {"videoId": VID}))

    def test_a_channel_call_names_the_channel(self):
        self.assertEqual(UCID, self.mod.target_of_call(
            "/api/v1/channels/%s/latest" % UCID, None))

    def test_a_call_about_nothing_in_particular_names_nothing(self):
        self.assertEqual("", self.mod.target_of_call("/api/v1/stats", None))


class Recorded:
    """The transport and the database, as the log reaches them."""

    def __init__(self, raises=None):
        self.raises = raises
        self.written = []

    def install(self, mod):
        mod.execute = self.written.append
        mod.api = self.api
        self.mod = mod
        return self

    def api(self, method, path, body=None):
        if self.raises:
            raise self.raises
        return {}


class EveryCallIsLogged(unittest.TestCase):
    def setUp(self):
        self.mod = engine()
        self.mod.note_bot_touch = lambda vid: None

    def call(self, raises=None, **over):
        Recorded(raises).install(self.mod)
        for name, value in over.items():
            setattr(self.mod, name, value)
        return self.mod

    def test_a_call_that_answered_is_logged_as_200(self):
        mod = self.call()
        mod.bot_api("GET", "/api/v1/videos/" + VID)
        self.assertEqual([(ME, "", "video", VID, 200, 0)], mod.FETCH_LOG)

    def test_a_refused_call_is_logged_with_its_status_and_still_raises(self):
        mod = self.call(raises=urllib.error.HTTPError("u", 429, "no", {}, None))
        with self.assertRaises(urllib.error.HTTPError):
            mod.bot_api("GET", "/api/v1/videos/" + VID)
        self.assertEqual([(ME, "", "video", VID, 429, 0)], mod.FETCH_LOG)

    def test_a_call_nothing_answered_is_logged_as_zero_and_still_raises(self):
        mod = self.call(raises=urllib.error.URLError("timed out"))
        with self.assertRaises(urllib.error.URLError):
            mod.bot_api("GET", "/api/v1/videos/" + VID)
        self.assertEqual([(ME, "", "video", VID, 0, 0)], mod.FETCH_LOG)

    def test_the_attempt_number_is_carried_so_a_retry_is_visible(self):
        mod = self.call()
        mod.bot_api("GET", "/api/v1/videos/" + VID, attempt=2)
        self.assertEqual(2, mod.FETCH_LOG[0][-1])

    def test_the_lane_is_carried_so_the_cost_can_be_attributed(self):
        mod = self.call(LANE="autos")
        mod.bot_api("GET", "/api/v1/videos/" + VID)
        self.assertEqual("autos", mod.FETCH_LOG[0][1])

    def test_a_dry_run_logs_nothing(self):
        """It makes real read calls; a run that wrote nothing must write none."""
        mod = self.call(DRY=True)
        mod.bot_api("GET", "/api/v1/videos/" + VID)
        self.assertEqual([], mod.FETCH_LOG)


class TheFlush(unittest.TestCase):
    def setUp(self):
        self.mod = engine()
        self.recorded = Recorded().install(self.mod)

    def buffer(self, *rows):
        self.mod.FETCH_LOG[:] = list(rows)

    def test_many_calls_cost_one_statement(self):
        self.buffer((ME, "autos", "video", VID, 200, 0),
                    (ME, "autos", "video", "abcdefghijk", 429, 1))
        self.mod.flush_fetch_log()
        self.assertEqual(1, len(self.recorded.written))

    def test_it_marks_only_the_upstream_sorts_as_upstream(self):
        self.buffer((ME, "autos", "video", VID, 200, 0),
                    (ME, "autos", "playlist_write", VID, 200, 0))
        self.mod.flush_fetch_log()
        written = self.recorded.written[0]
        self.assertIn("'video','%s',true" % VID, written)
        self.assertIn("'playlist_write','%s',false" % VID, written)

    def test_it_empties_the_buffer_so_nothing_is_written_twice(self):
        self.buffer((ME, "", "video", VID, 200, 0))
        self.mod.flush_fetch_log()
        self.mod.flush_fetch_log()
        self.assertEqual(1, len(self.recorded.written))

    def test_an_empty_buffer_writes_nothing(self):
        self.mod.flush_fetch_log()
        self.assertEqual([], self.recorded.written)

    def test_a_lost_log_is_said_out_loud_and_never_fails_the_run(self):
        said = []
        self.mod.log = said.append
        self.mod.execute = lambda sql: (_ for _ in ()).throw(RuntimeError("no table"))
        self.buffer((ME, "", "video", VID, 200, 0))
        self.mod.flush_fetch_log()
        self.assertTrue(any("fetch log" in line for line in said))
        self.assertEqual([], self.mod.FETCH_LOG)


class WhatSortOfNonAnswer(unittest.TestCase):
    def setUp(self):
        self.mod = engine()

    def test_it_tells_the_three_apart(self):
        self.assertIsNone(self.mod.failure_class(200))
        self.assertEqual("rate_limited", self.mod.failure_class(429))
        self.assertEqual("upstream_5xx", self.mod.failure_class(503))
        self.assertEqual("client_4xx", self.mod.failure_class(404))
        self.assertEqual("unanswered", self.mod.failure_class(0))

    def test_every_class_it_can_return_has_a_metric_label(self):
        for status in (429, 503, 404, 0):
            self.assertIn(self.mod.failure_class(status), self.mod.FAILURE_CLASSES)


class TheCacheHitRatio(unittest.TestCase):
    def setUp(self):
        self.mod = engine()

    def answer(self, got, asked):
        self.mod.query = lambda sql: [[str(got), str(asked)]]
        return self.mod.cache_hit_ratio("true")

    def test_it_is_the_share_the_cache_answered(self):
        self.assertEqual(0.9, self.answer(90, 100))

    def test_a_night_that_looked_nothing_up_is_zero_not_a_crash(self):
        self.assertEqual(0, self.answer(0, 0))


class TheLaneLabel(unittest.TestCase):
    """Changing account leaves whatever lane you were in."""

    def test_serving_an_account_clears_it(self):
        mod = engine()
        mod.open_session = lambda email: "a-session"
        mod.LANE = "autos"
        mod.serve_account(ME)
        self.assertEqual("", mod.LANE)

    def test_pointing_at_an_account_clears_it_too(self):
        mod = engine()
        mod.session_of = lambda email: "a-session"
        mod.LANE = "autos"
        mod.use_account(ME)
        self.assertEqual("", mod.LANE)


if __name__ == "__main__":
    unittest.main()

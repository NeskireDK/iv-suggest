"""Dating every call the engine makes, because nothing else does.

`suggest.runs` carries one fetch count per lane per night, so "how many
requests reached YouTube in that hour, for whom, of what sort" had no answer at
all. This is the log that answers it, written at the one choke point every call
already passes through.

Two things it must not get wrong. Counting every call as a request to YouTube
would overstate the number several times over -- most of them are answered from
the Invidious database and never leave the machine. And a row has to be dated
when the call happened rather than when the batch is written, or a `views` run
spanning ten minutes lands on a single instant.
"""

import ast
import pathlib
import time
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

    def test_only_three_sorts_can_reach_youtube_at_all(self):
        """Adding a video reaches get_video too, so it can go out; a delete,
        a read and a create never can. Whether one DID is decided per call."""
        self.assertEqual(("video", "channel_latest", "playlist_add"),
                         self.mod.CAN_REACH_KINDS)

    def test_it_names_each_sort(self):
        self.assertEqual("video", self.kind("GET", "/api/v1/videos/" + VID))
        self.assertEqual("channel_latest",
                         self.kind("GET", "/api/v1/channels/%s/latest" % UCID))
        self.assertEqual("stats", self.kind("GET", "/api/v1/stats"))
        self.assertEqual("history_write",
                         self.kind("POST", "/api/v1/auth/history/" + VID))
        self.assertEqual("playlist_read",
                         self.kind("GET", "/api/v1/auth/playlists/IVPLx"))
        self.assertEqual("playlist_add",
                         self.kind("POST", "/api/v1/auth/playlists/IVPLx/videos"))
        self.assertEqual("playlist_write",
                         self.kind("DELETE", "/api/v1/auth/playlists/IVPLx/videos/7"))
        self.assertEqual("playlist_write",
                         self.kind("POST", "/api/v1/auth/playlists"))

    def test_a_playlist_read_is_never_counted_as_a_request_to_youtube(self):
        for method, path in (("GET", "/api/v1/auth/playlists"),
                             ("PATCH", "/api/v1/auth/playlists/IVPLx"),
                             ("GET", "/api/v1/stats")):
            self.assertNotIn(self.kind(method, path), self.mod.CAN_REACH_KINDS)


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

    def test_a_playlist_call_names_the_playlist(self):
        """`auth` sits between the version and the collection on these paths."""
        for path in ("/api/v1/auth/playlists/IVPLx",
                     "/api/v1/auth/playlists/IVPLx/videos",
                     "/api/v1/auth/playlists/IVPLx/videos/12345"):
            self.assertEqual("IVPLx", self.mod.target_of_call(path, None), path)

    def test_a_history_write_names_the_video(self):
        self.assertEqual(VID, self.mod.target_of_call(
            "/api/v1/auth/history/" + VID, None))

    def test_a_collection_with_no_id_names_nothing(self):
        self.assertEqual("", self.mod.target_of_call(
            "/api/v1/auth/playlists", None))

    def test_it_never_records_the_collection_name_as_the_target(self):
        for path in ("/api/v1/auth/playlists/IVPLx",
                     "/api/v1/auth/history/" + VID,
                     "/api/v1/channels/%s/latest" % UCID):
            self.assertNotIn(self.mod.target_of_call(path, None),
                             ("playlists", "history", "channels"))


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
        mod = self.call(JOB="run")
        mod.bot_api("GET", "/api/v1/videos/" + VID)
        self.assertEqual([(ME, "", "run", "video", VID, 200, 0, "")],
                         [row[1:9] for row in mod.FETCH_LOG])

    def test_the_time_the_call_took_is_recorded(self):
        """The other cache tell: 6ms answered from cache, 2270ms went out."""
        mod = self.call(JOB="run")
        mod.bot_api("GET", "/api/v1/videos/" + VID)
        self.assertIsInstance(mod.FETCH_LOG[0][9], int)

    def test_it_is_dated_when_it_happened_and_not_when_it_is_written(self):
        """A views batch spans ten minutes and flushes on one instant."""
        mod = self.call(JOB="views")
        before = time.time()
        mod.bot_api("GET", "/api/v1/videos/" + VID)
        self.assertGreaterEqual(mod.FETCH_LOG[0][0], before)
        self.assertLessEqual(mod.FETCH_LOG[0][0], time.time())

    def test_the_job_is_carried_so_a_hand_run_is_not_read_as_the_nightly(self):
        mod = self.call(JOB="views")
        mod.bot_api("GET", "/api/v1/videos/" + VID)
        self.assertEqual("views", mod.FETCH_LOG[0][3])

    def test_a_refused_call_is_logged_with_its_status_and_still_raises(self):
        mod = self.call(raises=urllib.error.HTTPError("u", 429, "no", {}, None))
        with self.assertRaises(urllib.error.HTTPError):
            mod.bot_api("GET", "/api/v1/videos/" + VID)
        self.assertEqual([(ME, "", "", "video", VID, 429, 0, "")],
                         [row[1:9] for row in mod.FETCH_LOG])

    def test_a_call_nothing_answered_is_logged_as_zero_and_still_raises(self):
        mod = self.call(raises=urllib.error.URLError("timed out"))
        with self.assertRaises(urllib.error.URLError):
            mod.bot_api("GET", "/api/v1/videos/" + VID)
        self.assertEqual((ME, "", "", "video", VID, 0, 0),
                         mod.FETCH_LOG[0][1:8])

    def test_it_keeps_the_reason_nothing_answered(self):
        """The journal that used to hold it is thrown away every night."""
        mod = self.call(raises=urllib.error.URLError("timed out"))
        with self.assertRaises(urllib.error.URLError):
            mod.bot_api("GET", "/api/v1/videos/" + VID)
        self.assertIn("timed out", mod.FETCH_LOG[0][8])

    def test_the_attempt_number_is_carried_so_a_retry_is_visible(self):
        mod = self.call()
        mod.bot_api("GET", "/api/v1/videos/" + VID, attempt=2)
        self.assertEqual(2, mod.FETCH_LOG[0][7])

    def test_the_lane_is_carried_so_the_cost_can_be_attributed(self):
        mod = self.call(LANE="autos")
        mod.bot_api("GET", "/api/v1/videos/" + VID)
        self.assertEqual("autos", mod.FETCH_LOG[0][2])

    def test_the_buffer_is_flushed_before_it_can_grow_unbounded(self):
        """A command with no lane loop would otherwise lose the lot to a kill."""
        mod = self.call(JOB="views")
        mod.FETCH_LOG[:] = [(0.0, ME, "", "views", "video", VID, 200, 0, "", 7)] * (
            mod.FETCH_LOG_BATCH - 1)
        mod.bot_api("GET", "/api/v1/videos/" + VID)
        self.assertEqual([], mod.FETCH_LOG)

    def test_a_dry_run_is_logged_like_any_other(self):
        """It makes the same real calls, and already fills the metadata cache."""
        mod = self.call(JOB="run")
        mod.bot_api("GET", "/api/v1/videos/" + VID)
        self.assertEqual(1, len(mod.FETCH_LOG))

    def test_a_call_with_no_credential_is_not_logged_as_a_request(self):
        """Aborted is raised before anything leaves the process."""
        mod = self.call()
        mod.api = lambda *a, **k: (_ for _ in ()).throw(mod.Aborted("no cred"))
        with self.assertRaises(mod.Aborted):
            mod.bot_api("GET", "/api/v1/videos/" + VID)
        self.assertEqual([], mod.FETCH_LOG)


class AnErrorInsideA200(unittest.TestCase):
    """Invidious answers 200 with an error body when it cannot parse a video."""

    def setUp(self):
        self.mod = engine()
        self.mod.note_bot_touch = lambda vid: None

    def logged(self, answer):
        self.mod.api = lambda *a, **k: answer
        self.mod.bot_api("GET", "/api/v1/videos/" + VID)
        return self.mod.FETCH_LOG[0]

    def test_the_error_is_recorded_beside_the_200(self):
        row = self.logged({"error": "could not parse"})
        self.assertEqual(200, row[6])
        self.assertEqual("could not parse", row[8])

    def test_a_clean_answer_records_no_error(self):
        self.assertEqual("", self.logged({"title": "fine"})[8])

    def test_an_unreadable_answer_is_not_recorded_as_unanswered(self):
        """Upstream did answer; the body was the problem."""
        self.mod.api = lambda *a, **k: (_ for _ in ()).throw(
            ValueError("Expecting value"))
        with self.assertRaises(ValueError):
            self.mod.bot_api("GET", "/api/v1/videos/" + VID)
        row = self.mod.FETCH_LOG[0]
        self.assertEqual(200, row[6])
        self.assertIn("unreadable answer", row[8])


class TheFlush(unittest.TestCase):
    def setUp(self):
        self.mod = engine()
        self.recorded = Recorded().install(self.mod)

    def buffer(self, *rows):
        self.mod.FETCH_LOG[:] = list(rows)

    def test_many_calls_cost_one_statement(self):
        self.buffer((0.0, ME, "autos", "run", "video", VID, 200, 0, "", 2270),
                    (1.0, ME, "autos", "run", "video", "abcdefghijk", 429, 1, "", 90))
        self.mod.flush_fetch_log()
        self.assertEqual(1, len(self.recorded.written))

    def test_it_leaves_the_verdict_to_the_database(self):
        """Whether a call went out is evidence, not something Python can
        assert from the sort of call it was."""
        self.buffer((0.0, ME, "autos", "run", "video", VID, 200, 0, "", 2270))
        self.mod.flush_fetch_log()
        written = self.recorded.written[0]
        self.assertIn("FROM videos v WHERE v.id = c.target", written)
        self.assertIn("v.updated >= c.at", written)

    def test_a_channel_listing_is_always_out_and_a_playlist_read_never(self):
        self.buffer((0.0, ME, "", "run", "channel_latest", "UCx", 200, 0, "", 600))
        self.mod.flush_fetch_log()
        written = self.recorded.written[0]
        self.assertIn("WHEN c.kind = 'channel_latest' THEN true", written)
        self.assertIn("ELSE false END", written)

    def test_a_call_that_failed_counts_as_having_gone_out(self):
        """The error path deletes the cache row, so its absence must not read
        as a cache hit."""
        self.buffer((0.0, ME, "", "run", "video", VID, 500, 0, "", 300))
        self.mod.flush_fetch_log()
        self.assertIn("c.status <> 200 OR c.error <> ''",
                      self.recorded.written[0])

    def test_it_empties_the_buffer_so_nothing_is_written_twice(self):
        self.buffer((0.0, ME, "", "run", "video", VID, 200, 0, "", 7))
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
        self.buffer((0.0, ME, "", "run", "video", VID, 200, 0, "", 7))
        self.mod.flush_fetch_log()
        self.assertTrue(any("logged calls were not written" in line
                            for line in said))
        self.assertEqual([], self.mod.FETCH_LOG)


class WhatSortOfNonAnswer(unittest.TestCase):
    def setUp(self):
        self.mod = engine()

    def test_it_tells_them_apart(self):
        self.assertIsNone(self.mod.failure_class(200))
        self.assertEqual("rate_limited", self.mod.failure_class(429))
        self.assertEqual("upstream_5xx", self.mod.failure_class(503))
        self.assertEqual("client_4xx", self.mod.failure_class(404))
        self.assertEqual("unanswered", self.mod.failure_class(0))

    def test_a_200_carrying_an_error_is_a_failure_not_a_clean_call(self):
        """It is what buries a video for 30 days."""
        self.assertEqual("answered_an_error",
                         self.mod.failure_class(200, "could not parse"))

    def test_every_class_it_can_return_has_a_metric_label(self):
        for status, error in ((429, ""), (503, ""), (404, ""), (0, ""),
                              (200, "boom")):
            self.assertIn(self.mod.failure_class(status, error),
                          self.mod.FAILURE_CLASSES)


class TheCacheHitRatio(unittest.TestCase):
    def setUp(self):
        self.mod = engine()

    def answer(self, got, asked, migrated=True):
        self.mod._COLUMNS_SEEN.clear()
        self.mod.one = lambda sql: "t" if migrated else "f"
        self.mod.query = lambda sql: [[str(got), str(asked)]]
        return self.mod.cache_hit_ratio("true")

    def samples(self, got, asked, migrated=True):
        self.mod._COLUMNS_SEEN.clear()
        self.mod.one = lambda sql: "t" if migrated else "f"
        self.mod.query = lambda sql: [[str(got), str(asked)]]
        return self.mod.cache_hit_samples("true")

    def test_it_is_the_share_the_cache_answered(self):
        self.assertEqual(0.9, self.answer(90, 100))

    def test_it_divides_by_lookups_and_never_by_fetches(self):
        """`fetches` counts channel listings and retries, so a rate-limited
        night would move a ratio built on it while the cache did nothing."""
        self.mod._COLUMNS_SEEN.clear()
        self.mod.one = lambda sql: "t"
        asked = []
        self.mod.query = lambda sql: asked.append(sql) or [["9", "10"]]
        self.mod.cache_hit_ratio("true")
        self.assertIn("sum(lookups)", asked[0])
        self.assertNotIn("sum(fetches)", asked[0])

    def test_a_window_nothing_was_asked_in_gets_no_sample_at_all(self):
        """0 would mean the cache answered nothing, which is the alarming one."""
        self.assertEqual([], self.samples(0, 0))

    def test_a_real_share_does_get_a_sample(self):
        self.assertEqual([("", 0.9)], self.samples(90, 100))

    def test_it_publishes_nothing_before_the_column_exists(self):
        """A pull that lands before `init` must not take the exporter down."""
        self.assertEqual([], self.samples(90, 100, migrated=False))


class OneLanesShareOfTheRun(unittest.TestCase):
    """`begin_lane` is the only thing that sets the lane label and resets the
    per-lane cache hits, so a policy that never reached it logged its calls
    under the previous lane and rewrote that lane's hit count."""

    def test_only_the_dispatcher_starts_a_lane(self):
        text = (pathlib.Path(__file__).resolve().parent.parent
                / "iv-suggest").read_text()
        callers = {node.name for node in ast.walk(ast.parse(text))
                   if isinstance(node, ast.FunctionDef)
                   and "begin_lane" in {
                       call.func.attr for call in ast.walk(node)
                       if isinstance(call, ast.Call)
                       and isinstance(call.func, ast.Attribute)}}
        self.assertEqual({"run_one_lane"}, callers,
                         "every policy goes through the dispatcher, so a lane "
                         "must be started there and nowhere else")


class TheCacheHitCount(unittest.TestCase):
    """Both lookups count, or the ratio reads falsely low for a genre lane."""

    def fetcher(self):
        mod = engine()
        mod.unfetchable_videos = lambda: set()
        mod.cached_video_meta = lambda: {
            VID: {"genre": "Music", "genre_known": True},
            "unknowngenr": {"genre": None, "genre_known": False}}
        fetcher = mod.Fetcher()
        fetcher.begin_lane("music-discover", None)
        return fetcher

    def test_a_plain_lookup_counts(self):
        fetcher = self.fetcher()
        fetcher.known(VID)
        self.assertEqual(1, fetcher.lane_cache_hits)

    def test_a_lookup_is_counted_whether_it_hits_or_misses(self):
        """The hit rate's denominator, and the reason `fetches` is not it:
        that counts channel listings and every retry attempt."""
        fetcher = self.fetcher()
        fetcher.known(VID)
        fetcher.known("nothereatall")
        self.assertEqual(2, fetcher.lane_lookups)
        self.assertEqual(1, fetcher.lane_cache_hits)

    def test_starting_a_lane_clears_the_lookups_too(self):
        fetcher = self.fetcher()
        fetcher.known(VID)
        fetcher.begin_lane("autos", None)
        self.assertEqual(0, fetcher.lane_lookups)

    def test_a_genre_lookup_counts_too(self):
        fetcher = self.fetcher()
        fetcher.meta_of(VID)
        self.assertEqual(1, fetcher.lane_cache_hits)

    def test_the_two_counters_never_disagree(self):
        fetcher = self.fetcher()
        fetcher.known(VID)
        fetcher.meta_of(VID)
        self.assertEqual(fetcher.cache_hits, fetcher.lane_cache_hits)

    def test_starting_a_lane_clears_it(self):
        fetcher = self.fetcher()
        fetcher.known(VID)
        fetcher.begin_lane("autos", None)
        self.assertEqual(0, fetcher.lane_cache_hits)


class EveryCommandNamesItsJob(unittest.TestCase):
    """Or a call logged by one is indistinguishable from the nightly fill's."""

    def test_no_command_forgets(self):
        text = (pathlib.Path(__file__).resolve().parent.parent
                / "iv-suggest").read_text()
        silent = []
        for node in ast.walk(ast.parse(text)):
            if not isinstance(node, ast.FunctionDef):
                continue
            if not node.name.startswith("cmd_"):
                continue
            named = {call.func.id for call in ast.walk(node)
                     if isinstance(call, ast.Call)
                     and isinstance(call.func, ast.Name)}
            if "for_job" not in named:
                silent.append(node.name)
        self.assertEqual([], silent,
                         "a command that names no job logs its calls as the "
                         "last one's: %s" % ", ".join(silent))


class TheRunRow(unittest.TestCase):
    """The nightly timer can fire before `init` has applied a new column."""

    def record(self, migrated):
        mod = engine()
        written = []
        mod.execute = written.append
        mod.one = lambda sql: "t" if migrated else "f"
        mod._COLUMNS_SEEN.clear()
        self.assertTrue(
            mod.record_the_lane_run("autos", (1, 2, 3, 4), "", 99, 120))
        return written[0]

    def test_it_carries_both_terms_of_the_ratio_once_the_columns_are_there(self):
        written = self.record(migrated=True)
        self.assertIn("cache_hits,lookups", written)
        self.assertIn("99,120", written)

    def test_it_still_writes_the_row_when_the_column_is_not(self):
        written = self.record(migrated=False)
        self.assertNotIn("cache_hits", written)
        self.assertNotIn("lookups", written)
        self.assertIn("INSERT INTO suggest.runs(account,lane", written)

    def test_the_column_is_looked_up_once_per_process(self):
        mod = engine()
        asked = []
        mod.one = lambda sql: asked.append(sql) or "t"
        mod.execute = lambda sql: None
        mod._COLUMNS_SEEN.clear()
        for _ in range(5):
            mod.record_the_lane_run("autos", (0, 0, 0, 0), "", 0, 0)
        self.assertEqual(1, len(asked))


class TheRetention(unittest.TestCase):
    def pruned(self, exists=True):
        mod = engine()
        written = []
        mod.execute = written.append
        mod.one = lambda sql: "t" if exists else "f"
        mod.forget_the_far_past()
        return mod, written

    def test_the_call_log_is_pruned_with_the_others(self):
        _, written = self.pruned()
        tables = [sql.split("FROM ")[1].split(" ")[0] for sql in written]
        self.assertEqual(["suggest.plays", "suggest.fetches",
                          "suggest.bot_touches"], tables)

    def test_the_two_logs_share_one_retention_knob(self):
        mod, written = self.pruned()
        days = "%d days" % mod.LOG_RETENTION_DAYS
        self.assertIn(days, written[0])
        self.assertIn(days, written[1])

    def test_a_table_init_has_not_made_yet_is_left_alone(self):
        """Deploy before `init` would otherwise crash the harvest every half hour."""
        _, written = self.pruned(exists=False)
        self.assertEqual([], written)


class WorkDoneForEverybody(unittest.TestCase):
    """`views` walks a set de-duplicated across accounts."""

    def test_it_stops_naming_an_account(self):
        mod = engine()
        mod.for_nobody_in_particular()
        self.assertEqual("", mod.ACCOUNT)

    def test_views_asks_for_that_before_it_fetches_anything(self):
        text = (pathlib.Path(__file__).resolve().parent.parent
                / "iv-suggest").read_text()
        body = text[text.index("def cmd_views("):]
        body = body[:body.index("\n\n\n")]
        self.assertLess(body.index("for_nobody_in_particular()"),
                        body.index("fetcher.video("),
                        "the label has to be cleared before the first fetch, "
                        "or those rows carry whichever account was served last")


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

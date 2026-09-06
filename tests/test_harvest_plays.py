"""Turning a video somebody opened into a watch, when the client says nothing.

Yattee syncs subscriptions and playlists with Invidious but reports no play, so
a video watched on the Apple TV never reaches `users.watched` and every lane
keeps offering it back. The only server side trace of that playback is Invidious
refreshing its own `videos` cache row, which it does on any fetch of metadata
more than ten minutes stale.

The hazard is that this bot refreshes the same rows: it makes about 160 metadata
fetches a night. Marking those watched would have the fill reject its own
candidates, so telling a person's open from the bot's own fetch is the whole
correctness question here, and a miss has to fall on the side of skipping a real
play rather than inventing one.
"""

import unittest

from support import load

ME = "andre@example.com"
NOW = "2026-09-06 10:30:00.500000+00"
HUMAN_OPEN = ("dQw4w9WgXcQ", "2026-09-06 10:04:00", "f")
BOT_FETCH = ("AtijFP893Fo", "2026-09-06 03:41:12", "t")
SECOND_HUMAN_OPEN = ("irTExR9_FRY", "2026-09-06 10:22:31", "f")


class Instance:
    """The database and the history API, as the harvest reaches them."""

    def __init__(self, opens=(), watermark=NOW, refuse=None):
        self.opens = list(opens)
        self.watermark = watermark
        self.refuse = refuse or {}
        self.written = []
        self.pruned = False
        self.marked = []
        self.scan_sql = ""
        self.watermark_sql = ""
        self.served = []
        self.dated = False

    def install(self, mod):
        mod.one = self.one
        mod.query = self.query
        mod.execute = self.execute
        mod.api = self.api
        mod.serve_account = self.serve_account
        self.mod = mod
        return self

    def serve_account(self, email):
        self.served.append(email)
        self.mod.ACCOUNT = email
        self.mod.SESSION = "session-for-" + email

    def one(self, sql):
        if "suggest.job_runs" in sql:
            self.watermark_sql = sql
            return self.watermark
        if "now()" in sql:
            return NOW
        return ""

    def query(self, sql):
        if "FROM videos v" in sql:
            self.scan_sql = sql
            return [list(row) for row in self.opens]
        return []

    def execute(self, sql):
        if sql.startswith("DELETE FROM"):
            self.pruned = True
            return
        if "suggest.job_runs" in sql:
            self.dated = True
            return
        self.written.append(sql)

    def api(self, method, path, body=None):
        vid = path.rsplit("/", 1)[-1]
        self.marked.append((method, vid))
        status = self.refuse.get(vid)
        if status:
            raise self.mod.urllib.error.HTTPError(path, status, "no", {}, None)
        return None


class Args:
    def __init__(self, dry_run=False):
        self.dry_run = dry_run


def harvest(instance, dry_run=False):
    """Run the command against one fake instance and return its exit code."""
    mod = load(IV_SUGGEST_ACCOUNT=ME)
    instance.install(mod)
    return mod.cmd_harvest_plays(Args(dry_run)), mod


class AHumanOpen(unittest.TestCase):
    def test_it_is_marked_watched_for_the_primary_account(self):
        inst = Instance(opens=[HUMAN_OPEN])
        code, _ = harvest(inst)
        self.assertEqual(0, code)
        self.assertEqual([("POST", "dQw4w9WgXcQ")], inst.marked)

    def test_the_session_is_guaranteed_rather_than_merely_pointed_at(self):
        inst = Instance(opens=[HUMAN_OPEN])
        harvest(inst)
        self.assertEqual([ME], inst.served)

    def test_it_is_recorded_with_the_watched_outcome(self):
        inst = Instance(opens=[HUMAN_OPEN])
        harvest(inst)
        rows = [s for s in inst.written if "INSERT INTO suggest.plays" in s]
        self.assertEqual(1, len(rows))
        self.assertIn("'watched'", rows[0])


class TheBotsOwnFetch(unittest.TestCase):
    """The fill makes ~160 of these a night; marking them would starve the lanes."""

    def test_it_is_never_marked_watched(self):
        inst = Instance(opens=[BOT_FETCH])
        code, _ = harvest(inst)
        self.assertEqual(0, code)
        self.assertEqual([], inst.marked)

    def test_it_is_still_recorded_so_the_watermark_moves_past_it(self):
        inst = Instance(opens=[BOT_FETCH])
        harvest(inst)
        rows = [s for s in inst.written if "INSERT INTO suggest.plays" in s]
        self.assertEqual(1, len(rows))
        self.assertIn("'bot'", rows[0])
        self.assertIn("the bot fetched it", rows[0])

    def test_a_human_open_beside_it_is_still_marked(self):
        inst = Instance(opens=[BOT_FETCH, HUMAN_OPEN, SECOND_HUMAN_OPEN])
        harvest(inst)
        self.assertEqual([("POST", "dQw4w9WgXcQ"), ("POST", "irTExR9_FRY")],
                         inst.marked)


class TheScanWindow(unittest.TestCase):
    def test_a_first_run_starts_from_now_rather_than_reaching_back(self):
        """The fill before the upgrade left refreshes with no touch beside them."""
        inst = Instance(opens=[])
        harvest(inst)
        self.assertIn("coalesce(max(at)::text, now()::text)",
                      inst.watermark_sql)

    def test_the_watermark_is_the_run_and_not_the_newest_judged_open(self):
        """Read off the judged rows it would reset to now on every quiet run."""
        inst = Instance(opens=[])
        harvest(inst)
        self.assertIn("suggest.job_runs", inst.watermark_sql)
        self.assertNotIn("max(played)", inst.watermark_sql)

    def test_a_later_run_starts_where_the_last_one_stopped(self):
        inst = Instance(opens=[], watermark="2026-09-06 10:04:00.123456+00")
        harvest(inst)
        self.assertIn("'2026-09-06 10:04:00.123456+00'::timestamptz",
                      inst.scan_sql)
        self.assertNotIn("interval '6 hours'", inst.scan_sql)

    def test_the_watermark_keeps_the_microseconds_it_was_given(self):
        """Truncated to the second, the newest open is re-judged every run."""
        inst = Instance(opens=[], watermark="2026-09-06 10:04:00.123456+00")
        harvest(inst)
        self.assertIn(".123456", inst.scan_sql)

    def test_a_refresh_already_judged_is_left_out(self):
        """What makes a run that stopped part way through resumable."""
        inst = Instance(opens=[])
        harvest(inst)
        self.assertIn("suggest.plays p", inst.scan_sql)

    def test_an_empty_scan_marks_nothing_but_still_dates_the_run(self):
        inst = Instance(opens=[])
        code, _ = harvest(inst)
        self.assertEqual(0, code)
        self.assertEqual([], inst.written)
        self.assertEqual([], inst.marked)
        self.assertTrue(inst.dated)
        self.assertTrue(inst.pruned)


class ADryRun(unittest.TestCase):
    def test_it_calls_no_api_and_writes_nothing(self):
        inst = Instance(opens=[HUMAN_OPEN, BOT_FETCH])
        code, _ = harvest(inst, dry_run=True)
        self.assertEqual(0, code)
        self.assertEqual([], inst.marked)
        self.assertEqual([], inst.written)
        self.assertFalse(inst.pruned)
        self.assertFalse(inst.dated)


class ARefusal(unittest.TestCase):
    """409 is the account's own watch_history preference; no retry fixes it."""

    def test_it_fails_the_run(self):
        inst = Instance(opens=[HUMAN_OPEN], refuse={"dQw4w9WgXcQ": 409})
        code, _ = harvest(inst)
        self.assertEqual(1, code)

    def test_it_is_recorded_as_refused_with_the_status(self):
        inst = Instance(opens=[HUMAN_OPEN], refuse={"dQw4w9WgXcQ": 409})
        harvest(inst)
        rows = [s for s in inst.written if "INSERT INTO suggest.plays" in s]
        self.assertIn("409", rows[0])
        self.assertIn("'refused'", rows[0])

    def test_one_refusal_does_not_stop_the_next_open(self):
        inst = Instance(opens=[HUMAN_OPEN, SECOND_HUMAN_OPEN],
                        refuse={"dQw4w9WgXcQ": 409})
        code, _ = harvest(inst)
        self.assertEqual(1, code)
        self.assertEqual([("POST", "dQw4w9WgXcQ"), ("POST", "irTExR9_FRY")],
                         inst.marked)


class TheLog(unittest.TestCase):
    def test_a_real_run_prunes_the_far_past(self):
        inst = Instance(opens=[HUMAN_OPEN])
        harvest(inst)
        self.assertTrue(inst.pruned)

    def test_every_open_is_recorded_under_one_of_the_three_outcomes(self):
        inst = Instance(opens=[HUMAN_OPEN, BOT_FETCH, SECOND_HUMAN_OPEN],
                        refuse={"irTExR9_FRY": 409})
        _, mod = harvest(inst)
        rows = [s for s in inst.written if "INSERT INTO suggest.plays" in s]
        self.assertEqual(3, len(rows))
        recorded = [outcome for outcome in mod.PLAY_OUTCOMES
                    for row in rows if "'%s'" % outcome in row]
        self.assertEqual(sorted(mod.PLAY_OUTCOMES), sorted(recorded))


class TheScanSql(unittest.TestCase):
    """The join is the whole correctness argument, so its shape is asserted.

    `insert_video_into_playlist` calls `get_video` as well, so every candidate
    the fill adds to a lane rewrites the same cache row a viewer does. Reading
    the metadata cache instead of the touch log would call each of those a play.
    """

    def test_it_reads_the_touch_log_and_not_the_metadata_cache(self):
        inst = Instance(opens=[])
        _, mod = harvest(inst)
        self.assertIn("suggest.bot_touches", inst.scan_sql)
        self.assertNotIn("video_meta", inst.scan_sql)

    def test_it_matches_a_touch_on_either_side_of_the_refresh(self):
        inst = Instance(opens=[])
        _, mod = harvest(inst)
        window = "interval '%d seconds'" % mod.BOT_FETCH_MATCH_SECONDS
        self.assertEqual(2, inst.scan_sql.count(window))


class EveryCallThatRefreshesACacheRow(unittest.TestCase):
    """Recorded at the choke point, or a new call site starts looking human."""

    def cases(self):
        mod = load(IV_SUGGEST_ACCOUNT=ME)
        return mod, mod.video_the_call_refreshes

    def test_a_metadata_fetch_is_a_touch(self):
        _, refreshes = self.cases()
        self.assertEqual("dQw4w9WgXcQ",
                         refreshes("/api/v1/videos/dQw4w9WgXcQ", None))

    def test_adding_a_video_to_a_lane_is_a_touch(self):
        _, refreshes = self.cases()
        self.assertEqual("dQw4w9WgXcQ",
                         refreshes("/api/v1/auth/playlists/IVPLx/videos",
                                   {"videoId": "dQw4w9WgXcQ"}))

    def test_reading_the_playlists_is_not(self):
        _, refreshes = self.cases()
        self.assertIsNone(refreshes("/api/v1/auth/playlists", None))

    def test_removing_a_video_by_index_is_not(self):
        _, refreshes = self.cases()
        self.assertIsNone(
            refreshes("/api/v1/auth/playlists/IVPLx/videos/12345", None))

    def test_a_channel_listing_is_not(self):
        _, refreshes = self.cases()
        self.assertIsNone(refreshes("/api/v1/channels/UCabc/latest", None))


class TheMetricSamples(unittest.TestCase):
    """A follow-the-trend gauge is useless if an outcome's series disappears."""

    def test_every_outcome_gets_a_sample_even_at_zero(self):
        inst = Instance()
        mod = load(IV_SUGGEST_ACCOUNT=ME)
        inst.install(mod)
        mod.one = lambda sql: "t"
        samples = mod.plays_samples("true")
        self.assertEqual(len(mod.PLAY_OUTCOMES), len(samples))
        for outcome in mod.PLAY_OUTCOMES:
            self.assertIn(('{outcome="%s"}' % outcome, 0), samples)

    def test_it_reads_zero_before_init_has_made_the_table(self):
        inst = Instance()
        mod = load(IV_SUGGEST_ACCOUNT=ME)
        inst.install(mod)
        mod.one = lambda sql: "f"
        self.assertEqual([0, 0, 0], [rows for _, rows in mod.plays_samples("true")])


if __name__ == "__main__":
    unittest.main()

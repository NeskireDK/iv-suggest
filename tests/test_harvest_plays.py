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
HUMAN_OPEN = ("dQw4w9WgXcQ", "2026-09-06 10:04:00", "f")
BOT_FETCH = ("AtijFP893Fo", "2026-09-06 03:41:12", "t")
SECOND_HUMAN_OPEN = ("irTExR9_FRY", "2026-09-06 10:22:31", "f")


class Instance:
    """The database and the history API, as the harvest reaches them."""

    def __init__(self, opens=(), watermark=0, refuse=None):
        self.opens = list(opens)
        self.watermark = watermark
        self.refuse = refuse or {}
        self.written = []
        self.pruned = False
        self.marked = []
        self.scan_sql = ""
        self.served = []

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
        if "max(played)" in sql:
            return str(self.watermark)
        return ""

    def query(self, sql):
        if "FROM videos v" in sql:
            self.scan_sql = sql
            return [list(row) for row in self.opens]
        return []

    def execute(self, sql):
        if "DELETE FROM suggest.plays" in sql:
            self.pruned = True
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
    def test_a_first_run_looks_back_only_as_far_as_the_cache_lives(self):
        inst = Instance(opens=[], watermark=0)
        _, mod = harvest(inst)
        self.assertIn("interval '%d hours'" % mod.VIDEO_CACHE_LIFETIME_HOURS,
                      inst.scan_sql)
        self.assertNotIn("to_timestamp", inst.scan_sql)

    def test_a_later_run_starts_where_the_last_one_stopped(self):
        inst = Instance(opens=[], watermark=1757145600)
        harvest(inst)
        self.assertIn("to_timestamp(1757145600", inst.scan_sql)
        self.assertNotIn("interval '6 hours'", inst.scan_sql)

    def test_an_empty_scan_writes_nothing_and_succeeds(self):
        inst = Instance(opens=[])
        code, _ = harvest(inst)
        self.assertEqual(0, code)
        self.assertEqual([], inst.written)
        self.assertEqual([], inst.marked)
        self.assertFalse(inst.pruned)


class ADryRun(unittest.TestCase):
    def test_it_calls_no_api_and_writes_nothing(self):
        inst = Instance(opens=[HUMAN_OPEN, BOT_FETCH])
        code, _ = harvest(inst, dry_run=True)
        self.assertEqual(0, code)
        self.assertEqual([], inst.marked)
        self.assertEqual([], inst.written)
        self.assertFalse(inst.pruned)


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

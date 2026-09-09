"""Getting a watched video off the page inside the hour instead of by 03:30.

The harvest already spots a play in about four minutes. Retiring the video was
the slow half: it only happened in the nightly run, so something watched in the
evening stayed on the front page until the small hours.

The sweep is removal only. What comes IN is still the nightly run's decision,
and the reorder stays free on the hours nothing needs dropping: it asks over
SQL first and makes no API call at all when the answer is nothing.
"""

import unittest

from support import load

ME = "andre@example.com"


class WhichLanes(unittest.TestCase):
    """A lane whose policy has no one viewer must not be swept."""

    def setUp(self):
        self.mod = load(IV_SUGGEST_ACCOUNT=ME)

    def lane(self, **over):
        return dict({"id": "l", "policy": "refill", "exclude_watched": True}, **over)

    def test_a_refill_lane_is_swept(self):
        self.assertTrue(self.mod.sweeps_the_watched(self.lane()))

    def test_a_mix_lane_is_swept_because_it_filters_per_viewer(self):
        self.assertTrue(self.mod.sweeps_the_watched(self.lane(policy="mix")))

    def test_a_lane_that_turned_the_filter_off_is_left_alone(self):
        """music-discover: re-hearing a good song is the point of the lane."""
        self.assertFalse(
            self.mod.sweeps_the_watched(self.lane(exclude_watched=False)))

    def test_last_played_is_left_alone_because_plays_are_its_content(self):
        self.assertFalse(self.mod.sweeps_the_watched(self.lane(policy="last_played")))

    def test_a_consensus_lane_is_left_alone_because_it_has_no_viewer(self):
        """A public feed is compiled for a logged out visitor. Whose history?"""
        self.assertFalse(self.mod.sweeps_the_watched(self.lane(policy="consensus")))


class Cost(unittest.TestCase):

    def setUp(self):
        self.mod = load(IV_SUGGEST_ACCOUNT=ME)
        self.calls = []
        self.mod.bot_api = lambda *a, **k: self.calls.append(a) or {}
        self.lane = dict(self.mod.DEFAULTS, id="suggested", policy="refill")

    def test_an_hour_with_nothing_watched_costs_no_api_call(self):
        """The common case. 16 lanes an hour must not become 16 playlist reads."""
        self.mod.watched_still_offered = lambda plid: set()
        self.assertEqual(0, self.mod.drop_the_watched(self.lane, "PL1", dry=False))
        self.assertEqual([], self.calls)

    def test_a_lane_the_sweep_skips_is_not_even_queried(self):
        def refuse(plid):
            self.fail("asked the database about a lane it may not sweep")

        self.mod.watched_still_offered = refuse
        lane = dict(self.lane, policy="consensus")
        self.assertEqual(0, self.mod.drop_the_watched(lane, "PL1", dry=False))

    def test_a_dry_run_reports_without_deleting(self):
        self.mod.watched_still_offered = lambda plid: {"v1"}
        self.assertEqual(1, self.mod.drop_the_watched(self.lane, "PL1", dry=True))
        self.assertEqual([], self.calls)


class Bookkeeping(unittest.TestCase):
    """A mix lane keeps no state, so retiring one the refill way writes junk rows."""

    def setUp(self):
        self.mod = load(IV_SUGGEST_ACCOUNT=ME)
        self.retired, self.deleted = [], []
        self.mod.retire_item = lambda lane_id, plid, v, cooldown_days=0, reason="": \
            self.retired.append((lane_id, v["videoId"], cooldown_days, reason))
        self.mod.bot_api = lambda method, path, body=None: \
            self.deleted.append((method, path)) or {}
        self.video = {"videoId": "v1", "indexId": "ix1", "title": "T"}

    def test_a_refill_lane_retires_with_the_watched_cooldown(self):
        lane = dict(self.mod.DEFAULTS, id="suggested", policy="refill")
        self.mod.forget_a_watched_entry(lane, "PL1", self.video)
        self.assertEqual([("suggested", "v1", lane["watched_cooldown_days"],
                           "watched")], self.retired)
        self.assertEqual([], self.deleted)

    def test_a_mix_lane_only_leaves_the_playlist(self):
        lane = dict(self.mod.DEFAULTS, id="home-mix", policy="mix")
        self.mod.forget_a_watched_entry(lane, "PL1", self.video)
        self.assertEqual([], self.retired)
        self.assertEqual(
            [("DELETE", "/api/v1/auth/playlists/PL1/videos/ix1")], self.deleted)


class InTheReorder(unittest.TestCase):

    def setUp(self):
        self.mod = load(IV_SUGGEST_ACCOUNT=ME)
        self.mod.lane_plid = lambda account, lane_id: "PL1"
        self.mod.permute_playlist_index = lambda plid, ordered: None
        self.mod.age_fatigue = lambda lane_id, cfg, after: None
        self.mod.execute = lambda sql: None
        self.swept = []
        self.mod.drop_the_watched = lambda lane, plid, dry: \
            self.swept.append(lane["id"]) or 0

    def test_the_sweep_runs_before_the_order_is_read(self):
        """A video on its way out must not be ranked, or it takes a slot for an hour."""
        order = []
        self.mod.drop_the_watched = lambda lane, plid, dry: order.append("swept")
        self.mod.read_order = lambda plid, lane_id: order.append("read") or [
            [n + 1, v, "UC", 1.0, 0, 1.0, 1.0, v, 1.0]
            for n, v in enumerate(["a", "b", "c"])]
        lane = dict(self.mod.DEFAULTS, id="suggested", policy="refill",
                    shuffle=dict(self.mod.SHUFFLE_DEFAULTS))
        self.mod.shuffle_lane(lane, {}, dry=False)
        self.assertEqual(["swept", "read"], order)

    def test_a_lane_with_no_playlist_yet_is_not_swept(self):
        self.mod.lane_plid = lambda account, lane_id: ""
        lane = dict(self.mod.DEFAULTS, id="suggested", policy="refill",
                    shuffle=dict(self.mod.SHUFFLE_DEFAULTS))
        self.assertIsNone(self.mod.shuffle_lane(lane, {}, dry=False))
        self.assertEqual([], self.swept)


if __name__ == "__main__":
    unittest.main()

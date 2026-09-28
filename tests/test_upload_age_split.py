"""Two lanes split one set of channels by upload age.

`just-uploaded` holds the last two days and is what Trending publishes;
`fresh-uploads` takes only what is older and feeds Popular. Without a floor on
`fresh-uploads`, whichever lane ran first that night took a new upload, and the
run order is least-recently-succeeded first, not the file's.
"""

import time
import unittest

from support import load

ME = "andre@example.com"


def upload(vid, days_old):
    return {"videoId": vid, "title": "An upload", "author": "Someone",
            "authorId": "UCwatched", "lengthSeconds": 900,
            "published": int(time.time() - days_old * 86400)}


class Expanding(unittest.TestCase):

    def setUp(self):
        self.mod = load(IV_SUGGEST_ACCOUNT=ME)
        self.mod.channel_affinity = lambda run, cfg: self.mod.Affinity(
            {"UCwatched": 5.0}, {})
        videos = [upload("hours000001", 0.3), upload("oneday00001", 1.5),
                  upload("threedays01", 3.0), upload("tendays0001", 10.0)]

        class Fetcher:
            def channel_latest(self, ucid):
                return {"videos": videos}

        self.run = type("Run", (), {"fetcher": Fetcher(), "subs": set(), "blocked": {}})()

    def found(self, **lane):
        lane = dict(self.mod.DEFAULTS, id="lane", **lane)
        scores, _ = self.mod.expand_by_channel_uploads(lane, [], 20, self.run)
        return set(scores)

    def test_the_young_lane_takes_only_the_last_two_days(self):
        self.assertEqual({"hours000001", "oneday00001"}, self.found(max_age_days=2))

    def test_the_old_lane_takes_only_what_the_young_one_does_not(self):
        self.assertEqual({"threedays01", "tendays0001"},
                         self.found(max_age_days=30, min_age_days=2))

    def test_no_floor_takes_everything_it_used_to(self):
        self.assertEqual(4, len(self.found(max_age_days=30)))


class Sweeping(unittest.TestCase):

    def setUp(self):
        self.mod = load(IV_SUGGEST_ACCOUNT=ME)
        self.mod.upload_age_in = lambda plid: {"young000001": 0.5, "old00000001": 2.4}
        self.lane = dict(self.mod.DEFAULTS, max_age_days=2, expire_by_upload_age=True)

    def test_an_entry_past_max_age_leaves_however_recently_it_was_added(self):
        self.assertEqual({"old00000001"},
                         self.mod.uploaded_too_long_ago(self.lane, "PLx"))

    def test_off_by_default(self):
        lane = dict(self.lane, expire_by_upload_age=False)
        self.assertEqual(frozenset(), self.mod.uploaded_too_long_ago(lane, "PLx"))

    def test_a_dry_run_with_no_playlist_yet_asks_nothing(self):
        self.assertEqual(frozenset(), self.mod.uploaded_too_long_ago(self.lane, None))

    def test_it_leaves_as_aged_out(self):
        run = self.mod.LaneRun([], set(), set(), {}, None, True, None, set())
        why = self.mod.why_it_leaves({"videoId": "old00000001", "authorId": "UCx"},
                                     self.lane, run, {}, "", "", frozenset(),
                                     {"old00000001"})
        self.assertEqual("aged_out", why)

"""The one source that can reach a named channel's older uploads.

`channel_latest` asks `/latest`, which is the newest ~60 with no paging. So a
channel's back catalogue was unreachable however much somebody watched it — and
its newest ~60 is exactly what the subscription feed already shows, which is why
the dedupe in the same plan item does not re-admit anything by itself.

`/videos?sort_by=popular` returns the same shape ordered by views instead, at
any age. Verified live: one call, 60 videos, one to five years old.
"""

import unittest

from support import load

ME = "andre@example.com"


class Picking(unittest.TestCase):

    def setUp(self):
        self.mod = load(IV_SUGGEST_ACCOUNT=ME)
        self.asked = []
        self.mod.channel_affinity = lambda run, cfg: self.mod.Affinity(
            {"UCbig": 9.0, "UCsmall": 1.0, "UCbad": 5.0}, {})
        self.lane = dict(self.mod.DEFAULTS, id="deep-cuts", max_channels=8)

    def run_with(self, videos, blocked=()):
        asked = self.asked

        class Fetcher:
            def channel_popular(self, ucid):
                asked.append(ucid)
                return {"videos": videos}

        return type("Run", (), {"fetcher": Fetcher(), "subs": set(),
                                "blocked": dict.fromkeys(blocked, "Bad")})()

    def video(self, vid="aaaaaaaaaaa", **over):
        return dict({"videoId": vid, "title": "An old favourite",
                     "author": "Someone", "authorId": "UCbig",
                     "lengthSeconds": 900, "viewCount": 9000000}, **over)

    def test_it_asks_the_channels_the_account_watches(self):
        self.mod.expand_by_channel_back_catalogue(
            self.lane, [], 20, self.run_with([self.video()]))
        self.assertEqual({"UCbig", "UCsmall", "UCbad"}, set(self.asked))

    def test_a_blocked_channel_is_never_asked(self):
        self.mod.expand_by_channel_back_catalogue(
            self.lane, [], 20, self.run_with([self.video()], blocked=["UCbad"]))
        self.assertNotIn("UCbad", self.asked)

    def test_it_stops_at_max_channels(self):
        lane = dict(self.lane, max_channels=1)
        self.mod.expand_by_channel_back_catalogue(
            lane, [], 20, self.run_with([self.video()]))
        self.assertEqual(1, len(self.asked))

    def test_a_subscribed_channel_is_still_asked(self):
        """Its back catalogue is the half the subscription feed never shows."""
        run = self.run_with([self.video()])
        run.subs = {"UCbig"}
        self.mod.expand_by_channel_back_catalogue(self.lane, [], 20, run)
        self.assertIn("UCbig", self.asked)


class Scoring(unittest.TestCase):

    def setUp(self):
        self.mod = load(IV_SUGGEST_ACCOUNT=ME)
        self.lane = dict(self.mod.DEFAULTS, id="deep-cuts", max_channels=8)

    def expand(self, videos, weights=None):
        self.mod.channel_affinity = lambda run, cfg: self.mod.Affinity(
            weights or {"UCbig": 9.0}, {})

        class Fetcher:
            def channel_popular(self, ucid):
                return {"videos": [v for v in videos
                                   if v.get("authorId") == ucid]}

        run = type("Run", (), {"fetcher": Fetcher(), "subs": set(),
                               "blocked": {}})()
        return self.mod.expand_by_channel_back_catalogue(self.lane, [], 20, run)

    def video(self, vid, **over):
        return dict({"videoId": vid, "title": "t", "author": "a",
                     "authorId": "UCbig", "lengthSeconds": 900}, **over)

    def test_the_channel_s_own_best_leads(self):
        scores, _ = self.expand([self.video("aaaaaaaaaaa"),
                                 self.video("bbbbbbbbbbb")])
        self.assertGreater(scores["aaaaaaaaaaa"], scores["bbbbbbbbbbb"])

    def test_a_better_watched_channel_outscores_a_thinner_one_at_the_same_place(self):
        scores, _ = self.expand(
            [self.video("aaaaaaaaaaa"), self.video("bbbbbbbbbbb", authorId="UCthin")],
            weights={"UCbig": 9.0, "UCthin": 0.5})
        self.assertGreater(scores["aaaaaaaaaaa"], scores["bbbbbbbbbbb"])

    def test_age_is_not_a_filter_here(self):
        """A five year old video with two million views is the point of the lane."""
        scores, _ = self.expand([self.video("aaaaaaaaaaa", published=1)])
        self.assertIn("aaaaaaaaaaa", scores)

    def test_a_live_or_upcoming_entry_is_skipped(self):
        scores, _ = self.expand([self.video("aaaaaaaaaaa", liveNow=True),
                                 self.video("bbbbbbbbbbb", isUpcoming=True)])
        self.assertEqual({}, scores)

    def test_a_channel_that_answers_nothing_is_not_an_error(self):
        scores, meta = self.expand([])
        self.assertEqual(({}, {}), (scores, meta))


class ItIsWiredIn(unittest.TestCase):

    def setUp(self):
        self.mod = load(IV_SUGGEST_ACCOUNT=ME)

    def test_the_expander_is_reachable_by_name(self):
        self.assertIs(self.mod.expand_by_channel_back_catalogue,
                      self.mod.EXPANDERS["channel_popular"])

    def test_it_needs_no_seeds(self):
        """It derives its channels from the affinity table, not from a seed walk."""
        self.assertIn("channel_popular", self.mod.SEEDLESS_EXPANDERS)

    def test_the_fetcher_asks_for_popular_and_not_latest(self):
        from support import source
        self.assertIn("sort_by=popular", source())


if __name__ == "__main__":
    unittest.main()

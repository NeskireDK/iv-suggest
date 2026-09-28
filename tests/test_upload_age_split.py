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
            latest = {}

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


class UndatedListing(unittest.TestCase):
    """Invidious stamps an undated listing entry as fetched this instant."""

    def setUp(self):
        self.mod = load(IV_SUGGEST_ACCOUNT=ME)

    def undated(self):
        return dict(upload("sixmonths01", 0), publishedText="0 seconds ago")

    def test_zero_seconds_ago_is_no_date(self):
        self.assertEqual(0.0, self.mod.parse_published(self.undated()))

    def test_a_real_recent_upload_keeps_its_date(self):
        rec = dict(upload("hours000001", 0.3), publishedText="7 hours ago")
        self.assertGreater(self.mod.parse_published(rec), 0)

    def test_channel_latest_refuses_it_like_every_other_cutoff(self):
        self.mod.channel_affinity = lambda run, cfg: self.mod.Affinity(
            {"UCwatched": 5.0}, {})
        videos = [self.undated(), upload("hours000001", 0.3)]

        class Fetcher:
            latest = {}

            def channel_latest(self, ucid):
                return {"videos": videos}

        run = type("Run", (), {"fetcher": Fetcher(), "subs": set(), "blocked": {}})()
        lane = dict(self.mod.DEFAULTS, id="lane", max_age_days=2)
        scores, _ = self.mod.expand_by_channel_uploads(lane, [], 20, run)
        self.assertEqual({"hours000001"}, set(scores))


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


class ChannelListingsReadOncePerRun(unittest.TestCase):
    """fresh-uploads throws away everything under two days old. Reading the same
    listing again for just-uploaded would pay twice for what it already holds."""

    def setUp(self):
        self.mod = load(IV_SUGGEST_ACCOUNT=ME)
        self.mod.query = lambda sql: []
        self.mod.execute = lambda sql: None
        self.mod.log = lambda line: None
        self.calls = []
        self.mod.bot_api = lambda method, path, attempt=0: (
            self.calls.append(path) or {"videos": []})
        self.fetcher = self.mod.Fetcher(rate_per_min=10**6, budget=100)

    def test_a_second_read_of_one_channel_costs_nothing(self):
        self.fetcher.channel_latest("UCa")
        self.fetcher.channel_latest("UCa")
        self.assertEqual(1, len(self.calls))
        self.assertEqual(1, self.fetcher.fetches)

    def test_a_channel_read_earlier_is_taken_on_top_of_the_draw(self):
        self.mod.channel_affinity = lambda run, cfg: self.mod.Affinity(
            {"UCread": 1.0, "UCother": 1.0}, {})
        self.fetcher.latest = {"UCread": {"videos": []}}
        run = type("Run", (), {"fetcher": self.fetcher, "subs": set(), "blocked": {}})()
        lane = dict(self.mod.DEFAULTS, id="lane", max_channels=1, max_age_days=2)
        self.mod.expand_by_channel_uploads(lane, [], 20, run)
        self.assertEqual(["/api/v1/channels/UCother/latest"], self.calls)

    def test_an_ineligible_channel_read_earlier_is_not_taken(self):
        """Read for another account, or subscribed here: the draw's rules still hold."""
        self.mod.channel_affinity = lambda run, cfg: self.mod.Affinity({"UCother": 1.0}, {})
        self.fetcher.latest = {"UCsubscribed": {"videos": [upload("hours000001", 0.3)]}}
        run = type("Run", (), {"fetcher": self.fetcher, "subs": {"UCsubscribed"}, "blocked": {}})()
        lane = dict(self.mod.DEFAULTS, id="lane", max_channels=1, max_age_days=2)
        scores, _ = self.mod.expand_by_channel_uploads(lane, [], 20, run)
        self.assertEqual({}, scores)


class RepeatChannelDiscount(unittest.TestCase):

    def setUp(self):
        self.mod = load(IV_SUGGEST_ACCOUNT=ME)

    def test_a_channels_repeats_take_the_factor_then_its_square(self):
        weights = {"a1": 1.0, "a2": 0.9, "a3": 0.8, "b1": 0.5}
        channels = {"a1": "UCa", "a2": "UCa", "a3": "UCa", "b1": "UCb"}
        out = self.mod.discount_repeat_channels(weights, channels, 0.3)
        self.assertAlmostEqual(1.0, out["a1"])
        self.assertAlmostEqual(0.27, out["a2"])
        self.assertAlmostEqual(0.072, out["a3"])
        self.assertAlmostEqual(0.5, out["b1"])

    def test_the_heaviest_keeps_full_weight_whatever_order_it_arrives_in(self):
        out = self.mod.discount_repeat_channels(
            {"late": 0.2, "best": 0.9}, {"late": "UCa", "best": "UCa"}, 0.5)
        self.assertAlmostEqual(0.9, out["best"])
        self.assertAlmostEqual(0.1, out["late"])

    def test_a_video_with_no_known_channel_is_its_own(self):
        out = self.mod.discount_repeat_channels({"x": 1.0, "y": 1.0}, {}, 0.3)
        self.assertEqual({"x": 1.0, "y": 1.0}, out)

    def test_off_by_default_asks_the_database_nothing(self):
        self.mod.query = lambda sql: self.fail("queried: " + sql)
        self.mod.upload_age_weights = lambda vids, cfg: {}
        cfg = dict(self.mod.CONSENSUS_DEFAULTS)
        weights = self.mod.consensus_weights([{"vids": ["a", "b"]}], cfg)
        self.assertEqual({"a", "b"}, set(weights))

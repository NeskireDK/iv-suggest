"""News channels are left out of the back catalogue.

`deep-cuts` rests on an old video from a well-watched channel still being worth
watching. For news that is false: a 2023 segment is stale, not a deep cut.
YouTube's category catches the channels that file themselves as news, and
`news_channels.always` catches the commentary channels that do not.
"""

import time
import unittest

from support import load

ME = "andre@example.com"
NEWS = "News & Politics"


def row(cid, genre, known=True):
    return {"authorId": cid, "genre": genre, "genre_known": known}


class Classifying(unittest.TestCase):

    def setUp(self):
        self.mod = load(IV_SUGGEST_ACCOUNT=ME)
        self.cfg = dict(self.mod.NEWS_CHANNEL_DEFAULTS)

    def news(self, *rows):
        meta = {"v%010d" % i: r for i, r in enumerate(rows)}
        return self.mod.news_channels(meta, self.cfg)

    def test_eight_of_ten_filed_under_news_is_news(self):
        self.assertEqual({"UCnews"}, self.news(
            *[row("UCnews", NEWS)] * 8, *[row("UCnews", "Education")] * 2))

    def test_seven_of_ten_is_not(self):
        self.assertEqual(set(), self.news(
            *[row("UCmixed", NEWS)] * 7, *[row("UCmixed", "Education")] * 3))

    def test_too_few_videos_is_not_evidence_however_they_are_filed(self):
        """Found live: a gaming streamer read as news off two cached videos."""
        self.assertEqual(set(), self.news(*[row("UCfew", NEWS)] * 9))

    def test_a_row_without_a_real_category_is_not_counted(self):
        """A listing-filled row has genre_known false; its genre is not evidence."""
        self.assertEqual(set(), self.news(
            *[row("UCx", NEWS, known=False)] * 10, row("UCx", NEWS)))

    def test_a_channel_with_no_category_at_all_is_not_news(self):
        self.assertEqual(set(), self.news(row("UCx", None)))

    def test_always_adds_a_channel_the_category_misses(self):
        self.cfg["always"] = {"UCcommentary": "Some Commentary"}
        self.assertEqual({"UCcommentary"}, self.news(row("UCcommentary", "People & Blogs")))

    def test_always_needs_no_evidence(self):
        self.cfg["always"] = {"UCunseen": "Never Cached"}
        self.assertEqual({"UCunseen"}, self.news())

    def test_never_overrides_the_category(self):
        self.cfg["never"] = {"UCnews": "Filed Wrong"}
        self.assertEqual(set(), self.news(*[row("UCnews", NEWS)] * 10))

    def test_a_lane_without_skip_news_skips_nothing_and_reads_no_cache(self):
        lane = dict(self.mod.DEFAULTS)
        run = type("Run", (), {"fetcher": None})()
        self.assertEqual(frozenset(), self.mod.news_channels_to_skip(lane, run))


class BackCatalogue(unittest.TestCase):

    def setUp(self):
        self.mod = load(IV_SUGGEST_ACCOUNT=ME)
        self.mod.channel_affinity = lambda run, cfg: self.mod.Affinity(
            {"UCgame": 9.0, "UCnews": 5.0}, {})
        self.mod.news_channel_config = lambda: dict(self.mod.NEWS_CHANNEL_DEFAULTS)
        self.asked = []

    def expand(self, skip_news):
        asked = self.asked

        class Fetcher:
            meta = dict({"n%010d" % i: row("UCnews", NEWS) for i in range(10)},
                        v0000000002=row("UCgame", "Gaming"))

            def channel_popular(self, ucid):
                asked.append(ucid)
                return {"videos": []}

        run = type("Run", (), {"fetcher": Fetcher(), "subs": set(), "blocked": {}})()
        lane = dict(self.mod.DEFAULTS, id="deep-cuts", max_channels=8,
                    skip_news=skip_news)
        self.mod.expand_by_channel_back_catalogue(lane, [], 20, run)

    def test_a_news_channel_is_never_asked(self):
        self.expand(skip_news=True)
        self.assertEqual(["UCgame"], self.asked)

    def test_without_skip_news_it_is_asked_as_before(self):
        self.expand(skip_news=False)
        self.assertEqual({"UCgame", "UCnews"}, set(self.asked))


class Sweep(unittest.TestCase):

    def setUp(self):
        self.mod = load(IV_SUGGEST_ACCOUNT=ME)
        self.run = self.mod.LaneRun([], set(), set(), {}, None, True, None, set())
        self.lane = dict(self.mod.DEFAULTS, skip_news=True)

    def test_an_entry_from_a_news_channel_leaves_as_news(self):
        why = self.mod.why_it_leaves({"videoId": "v0000000001", "authorId": "UCnews"},
                                     self.lane, self.run, {}, "", "", {"UCnews"})
        self.assertEqual("news", why)

    def test_it_carries_no_cooldown_so_a_never_override_is_instant(self):
        self.assertEqual(0, self.mod.cooldown_days_for(self.lane, "news"))


class OldNewsAgesOut(unittest.TestCase):
    """News is wanted while it is news. After `max_age_days` it leaves every lane."""

    def setUp(self):
        self.mod = load(IV_SUGGEST_ACCOUNT=ME)
        self.mod.news_channel_config = lambda: dict(
            self.mod.NEWS_CHANNEL_DEFAULTS, always={"UCnews": "News"}, max_age_days=3)
        self.news = self.mod.channels_whose_news_ages_out({})

    def test_news_older_than_the_limit_is_old_news(self):
        self.assertTrue(self.mod.is_old_news("UCnews", 3.5, self.news))

    def test_news_inside_the_limit_stays(self):
        self.assertFalse(self.mod.is_old_news("UCnews", 2.0, self.news))

    def test_an_old_video_from_any_other_channel_stays(self):
        self.assertFalse(self.mod.is_old_news("UCgame", 900.0, self.news))

    def test_a_limit_of_zero_ages_nothing_out(self):
        self.mod.news_channel_config = lambda: dict(
            self.mod.NEWS_CHANNEL_DEFAULTS, always={"UCnews": "News"})
        self.assertEqual(frozenset(), self.mod.channels_whose_news_ages_out({}))

    def test_the_sweep_finds_old_news_by_the_playlists_upload_date(self):
        self.mod.upload_age_in = lambda plid: {"v0000000001": 5.0, "v0000000002": 1.0,
                                               "v0000000003": 9.0}
        current = [{"videoId": "v0000000001", "authorId": "UCnews"},
                   {"videoId": "v0000000002", "authorId": "UCnews"},
                   {"videoId": "v0000000003", "authorId": "UCgame"}]
        self.assertEqual({"v0000000001"},
                         set(self.mod.old_news_in(current, "PL", self.news)))

    def test_old_news_leaves_with_its_own_reason_and_no_cooldown(self):
        lane = dict(self.mod.DEFAULTS)
        run = self.mod.LaneRun([], set(), set(), {}, None, True, None, set())
        why = self.mod.why_it_leaves({"videoId": "v0000000001", "authorId": "UCnews"},
                                     lane, run, {}, "", "", frozenset(), frozenset(),
                                     {"v0000000001"})
        self.assertEqual("old_news", why)
        self.assertEqual(0, self.mod.cooldown_days_for(lane, "old_news"))

    def test_an_old_news_candidate_is_refused(self):
        self.mod.query = lambda sql: []
        self.mod.videos_on_cooldown = lambda lane_id, drop: set()
        old = time.time() - 5 * 86400.0
        fresh = time.time() - 3600.0
        meta_seen = {"v0000000001": {"authorId": "UCnews", "published": old},
                     "v0000000002": {"authorId": "UCnews", "published": fresh}}

        class Fetcher:
            meta, dead = {}, set()

            def known(self, vid):
                return None

        run = self.mod.LaneRun([], set(), set(), {}, Fetcher(), True, None, set(),
                               self.news)
        lane = dict(self.mod.DEFAULTS, id="suggested", sample_pool=0)
        chosen = self.mod.choose_candidates(
            lane, {"v0000000001": 2.0, "v0000000002": 1.0}, meta_seen, [], [], 5, run)
        self.assertEqual(["v0000000002"], [c[0] for c in chosen])

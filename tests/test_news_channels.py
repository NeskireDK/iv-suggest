"""News channels are left out of the back catalogue.

`deep-cuts` rests on an old video from a well-watched channel still being worth
watching. For news that is false: a 2023 segment is stale, not a deep cut.
YouTube's category catches the channels that file themselves as news, and
`news_channels.always` catches the commentary channels that do not.
"""

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

    def test_a_channel_mostly_filed_under_news_is_news(self):
        self.assertEqual({"UCnews"}, self.news(
            row("UCnews", NEWS), row("UCnews", NEWS), row("UCnews", "Education")))

    def test_one_news_video_does_not_make_a_gaming_channel_news(self):
        self.assertEqual(set(), self.news(
            row("UCgame", "Gaming"), row("UCgame", "Gaming"), row("UCgame", NEWS)))

    def test_a_row_without_a_real_category_is_not_counted(self):
        """A listing-filled row has genre_known false; its genre is not evidence."""
        self.assertEqual(set(), self.news(
            row("UCx", NEWS, known=False), row("UCx", NEWS, known=False),
            row("UCx", "Gaming")))

    def test_a_channel_with_no_category_at_all_is_not_news(self):
        self.assertEqual(set(), self.news(row("UCx", None)))

    def test_always_adds_a_channel_the_category_misses(self):
        self.cfg["always"] = {"UCcommentary": "Some Commentary"}
        self.assertEqual({"UCcommentary"}, self.news(row("UCcommentary", "People & Blogs")))

    def test_never_overrides_the_category(self):
        self.cfg["never"] = {"UCnews": "Filed Wrong"}
        self.assertEqual(set(), self.news(row("UCnews", NEWS)))

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
            meta = {"v0000000001": row("UCnews", NEWS),
                    "v0000000002": row("UCgame", "Gaming")}

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

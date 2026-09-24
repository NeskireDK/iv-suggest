"""`exclude_subscribed` excluded the channel; it now excludes the feed's top N.

The justification was always *"their feed already shows them"*, and that is only
true of the videos the subscription view is currently showing. A channel's older
uploads fall out of that view and become eligible on their own, so the rule
maintains itself instead of being a standing ban.

Measured before this, over five nights and 52 lane-runs: `subscribed` rejected
**13 videos in total**, about 2.6 a night. The reason to do it is not the count.
A banned channel never entered a lane, so it never became a *seed* either, and
the recommendation graph was never asked what sits near it.
"""

import unittest

from support import load, source

ME = "andre@example.com"
THEM = "sofie@example.com"
SUBS = {"UCone", "UCtwo"}


class WhatALaneMaySkip(unittest.TestCase):

    def setUp(self):
        self.mod = load(IV_SUGGEST_ACCOUNT=ME)
        self.mod._SUBS_FEED.clear()
        self.mod.query = lambda sql: [["inthefeed1"], ["inthefeed2"]]

    def run_with(self, subs=SUBS):
        return type("Run", (), {"subs": set(subs)})()

    def lane(self, **over):
        return dict(self.mod.DEFAULTS, **over)

    def test_by_default_only_the_feed_s_own_videos_are_skipped(self):
        skip, why = self.mod.subscribed_videos_to_skip(self.lane(),
                                                       self.run_with())
        self.assertEqual({"inthefeed1", "inthefeed2"}, skip)
        self.assertEqual("subs_feed", why)

    def test_a_window_of_zero_means_the_whole_channel_as_it_used_to(self):
        skip, why = self.mod.subscribed_videos_to_skip(
            self.lane(subs_feed_window=0), self.run_with())
        self.assertIsNone(skip)
        self.assertEqual("subscribed", why)

    def test_a_lane_that_wants_subscriptions_skips_nothing(self):
        """The subs lanes ARE the feed, so neither rule applies to them."""
        skip, why = self.mod.subscribed_videos_to_skip(
            self.lane(exclude_subscribed=False), self.run_with())
        self.assertEqual(set(), skip)

    def test_an_account_with_no_subscriptions_asks_the_database_nothing(self):
        asked = []
        self.mod.query = lambda sql: asked.append(sql) or []
        skip, _ = self.mod.subscribed_videos_to_skip(self.lane(),
                                                     self.run_with(subs=()))
        self.assertEqual(set(), skip)
        self.assertEqual([], asked)


class TheFeedQuery(unittest.TestCase):

    def setUp(self):
        self.mod = load(IV_SUGGEST_ACCOUNT=ME)
        self.mod._SUBS_FEED.clear()
        self.asked = []
        self.mod.query = lambda sql: self.asked.append(sql) or [["a"]]

    def test_it_takes_the_newest_of_the_subscribed_channels(self):
        self.mod.videos_in_the_subs_feed(SUBS, 50)
        sql = self.asked[0]
        self.assertIn("channel_videos", sql)
        self.assertIn("ORDER BY published DESC", sql)
        self.assertIn("LIMIT 50", sql)

    def test_it_is_asked_once_however_many_lanes_want_it(self):
        for _ in range(4):
            self.mod.videos_in_the_subs_feed(SUBS, 50)
        self.assertEqual(1, len(self.asked))

    def test_another_account_does_not_inherit_the_first_one_s_feed(self):
        """One invocation serves every managed account in turn."""
        self.mod.videos_in_the_subs_feed(SUBS, 50)
        self.mod.ACCOUNT = THEM
        self.mod.videos_in_the_subs_feed(SUBS, 50)
        self.assertEqual(2, len(self.asked))


class WhatItIsNot(unittest.TestCase):

    def setUp(self):
        self.mod = load(IV_SUGGEST_ACCOUNT=ME)

    def test_channel_latest_still_polls_only_unsubscribed_channels(self):
        """The dedupe changes which candidates survive, not whom the bot asks.
        A subscribed channel's newest ~60 IS the feed, so polling it buys nothing."""
        self.assertIn("cid not in run.subs", source())

    def test_the_window_ships_on_by_default(self):
        self.assertEqual(50, self.mod.DEFAULTS["subs_feed_window"])
        self.assertIs(True, self.mod.DEFAULTS["exclude_subscribed"])



if __name__ == "__main__":
    unittest.main()

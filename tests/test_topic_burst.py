"""The lane for a topic the account was not watching a month ago and is now.

Two filters, because either alone is noise: lift on its own promotes a word
watched twice, and a raw count promotes whatever the account always watches. A
term has to clear both against the account's own baseline, which is what lets
the stopword list stay short.
"""

import unittest

from support import load

ME = "andre@example.com"


def titles(recent, older):
    return [("recent", t) for t in recent] + [("older", t) for t in older]


class Terms(unittest.TestCase):

    def setUp(self):
        self.mod = load(IV_SUGGEST_ACCOUNT=ME)

    def test_a_title_gives_its_words_and_its_adjacent_pairs(self):
        self.assertEqual({"wow", "forever", "beta", "wow forever",
                          "forever beta"},
                         self.mod.title_terms("WoW Forever Beta"))

    def test_filler_and_short_words_are_dropped(self):
        self.assertEqual({"beta"}, self.mod.title_terms("The New Beta"))

    def test_a_term_new_to_the_account_lifts_rather_than_divides_by_zero(self):
        self.assertGreater(self.mod.term_lift(10, 100, 0, 1000), 100.0)

    def test_an_empty_side_cannot_lift_anything(self):
        self.assertEqual(0.0, self.mod.term_lift(10, 0, 0, 1000))
        self.assertEqual(0.0, self.mod.term_lift(10, 100, 0, 0))


class WhatBursts(unittest.TestCase):

    def setUp(self):
        self.mod = load(IV_SUGGEST_ACCOUNT=ME)
        # 5, not the shipped 20: these fixtures are a few titles each, and lift
        # is bounded by how much baseline there is to be rare against.
        self.cfg = dict(self.mod.DEFAULTS["burst"], terms=2, min_recent=3,
                        min_lift=5.0)

    def answering(self, rows):
        self.mod.query = lambda sql: rows

    def test_a_topic_started_this_week_is_found(self):
        self.answering(titles(
            ["Kraken Beta Impressions", "Kraken Beta Classes",
             "Kraken Beta Raids", "Kraken Beta Launch"],
            ["Sailing to Norway", "A Barn Find", "Piano Practice",
             "Bread From Scratch"]))
        self.assertEqual(["kraken beta"],
                         [t for t, _, _ in self.mod.bursting_terms(self.cfg)])

    def test_what_the_account_always_watches_does_not_burst(self):
        """A raw count would promote it; the lift against the baseline will not."""
        always = ["Piano Practice %d" % n for n in range(6)]
        self.answering(titles(always, always * 3))
        self.assertEqual([], self.mod.bursting_terms(self.cfg))

    def test_one_stray_play_does_not_burst(self):
        """A huge lift on two titles is a coincidence, not a topic."""
        self.answering(titles(
            ["Kraken Beta Impressions", "Kraken Beta Raids", "Bread", "Piano"],
            ["Sailing", "Barn Find", "Piano Practice", "Bread From Scratch"]))
        self.assertEqual([], self.mod.bursting_terms(self.cfg))

    def test_too_little_history_says_so_rather_than_guessing(self):
        self.answering(titles(["Only One"], ["A", "B"]))
        self.assertEqual([], self.mod.bursting_terms(self.cfg))


class OneTermPerTopic(unittest.TestCase):
    """Two slots on one topic would leave none for the next."""

    def setUp(self):
        self.mod = load(IV_SUGGEST_ACCOUNT=ME)

    def pick(self, ranked, most=2):
        return [t for t, _, _ in self.mod.pick_distinct_terms(ranked, most)]

    def test_a_shared_word_means_one_topic(self):
        """"wow forever" and "warcraft forever" sit in different titles, so only
        the shared word can tell they are the same thing."""
        self.assertEqual(["wow forever"], self.pick([
            ("wow forever", 300.0, {1, 2, 3}),
            ("warcraft forever", 150.0, {4, 5, 6})]))

    def test_the_same_videos_mean_one_topic_even_with_no_shared_word(self):
        self.assertEqual(["kraken beta"], self.pick([
            ("kraken beta", 300.0, {1, 2, 3, 4}),
            ("class deep", 150.0, {1, 2, 3, 4})]))

    def test_a_genuinely_separate_topic_still_gets_a_slot(self):
        self.assertEqual(["kraken beta", "barn find"], self.pick([
            ("kraken beta", 300.0, {1, 2, 3}),
            ("barn find", 90.0, {7, 8, 9})]))

    def test_a_pair_is_preferred_to_either_word_alone(self):
        """It makes the better search, and the bare word would take the slot."""
        self.assertEqual(["kraken beta"], self.pick([
            ("kraken", 500.0, {1, 2, 3}),
            ("kraken beta", 300.0, {1, 2, 3})], most=1))


class TheExpander(unittest.TestCase):

    def setUp(self):
        self.mod = load(IV_SUGGEST_ACCOUNT=ME)
        self.searched = []
        self.mod.bursting_terms = lambda cfg: [("kraken beta", 300.0, 4)]
        self.lane = dict(self.mod.DEFAULTS, id="burst", max_age_days=21)

    def run_with(self, found):
        searched = self.searched

        class Fetcher:
            def search(self, term):
                searched.append(term)
                return found

        run = type("Run", (), {"fetcher": Fetcher(), "subs": set(),
                               "blocked": set()})()
        return self.mod.expand_by_topic_burst(self.lane, [], 30, run)

    def video(self, **over):
        return dict({"videoId": "aaaaaaaaaaa", "title": "Kraken Beta Raids",
                     "author": "Someone", "authorId": "UCx", "lengthSeconds": 600,
                     "published": 9e9}, **over)

    def test_it_searches_each_bursting_term_once(self):
        self.run_with([self.video()])
        self.assertEqual(["kraken beta"], self.searched)

    def test_a_found_video_is_scored_by_the_term_that_found_it(self):
        scores, meta = self.run_with([self.video()])
        self.assertGreater(scores["aaaaaaaaaaa"], 0.0)
        self.assertEqual("Kraken Beta Raids", meta["aaaaaaaaaaa"]["title"])

    def test_a_live_or_upcoming_result_is_skipped(self):
        scores, _ = self.run_with([self.video(liveNow=True),
                                   self.video(videoId="bbbbbbbbbbb",
                                              isUpcoming=True)])
        self.assertEqual({}, scores)

    def test_a_result_older_than_the_lane_allows_is_skipped(self):
        scores, _ = self.run_with([self.video(published=0)])
        self.assertEqual({}, scores)

    def test_nothing_bursting_costs_no_search(self):
        self.mod.bursting_terms = lambda cfg: []
        scores, meta = self.run_with([self.video()])
        self.assertEqual(({}, {}), (scores, meta))
        self.assertEqual([], self.searched)


class ItIsWiredIn(unittest.TestCase):

    def setUp(self):
        self.mod = load(IV_SUGGEST_ACCOUNT=ME)

    def test_the_expander_is_reachable_by_name(self):
        self.assertIs(self.mod.expand_by_topic_burst,
                      self.mod.EXPANDERS["topic_burst"])

    def test_burst_is_merged_key_by_key_like_the_other_blocks(self):
        """A lane setting one burst key must keep the defaults for the rest."""
        merged = self.mod.merge_lane(dict(self.mod.DEFAULTS),
                                     {"burst": {"terms": 1}})
        self.assertEqual(1, merged["burst"]["terms"])
        self.assertEqual(self.mod.DEFAULTS["burst"]["min_lift"],
                         merged["burst"]["min_lift"])


if __name__ == "__main__":
    unittest.main()

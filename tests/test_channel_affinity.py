"""How much somebody has been watching a channel lately, as a score multiplier.

Positions in `users.watched`, not dates. That array is the complete watch record
and has no clock; `suggest.plays` has dates but only started keeping them on
2026-09-23, and it cannot see a play of anything Invidious fetched in the last
ten minutes.

`expand_by_channel_uploads` already weighed channels by a raw watch count. What
this adds is *when* — and it reaches `expand_by_recommendation`, which had no
channel affinity at all.
"""

import unittest

from support import load

ME = "andre@example.com"
THEM = "sofie@example.com"


def meta(**channels):
    return {vid: {"authorId": cid} for vid, cid in channels.items()}


class Weighing(unittest.TestCase):

    def setUp(self):
        self.mod = load(IV_SUGGEST_ACCOUNT=ME)

    def weigh(self, watched, channels, halflife=2):
        return self.mod.weigh_by_recency(watched, meta(**channels), halflife)

    def test_the_newest_entry_counts_whole(self):
        self.assertAlmostEqual(
            1.0, self.weigh(["a"], {"a": "UC1"})["UC1"])

    def test_a_watch_one_halflife_back_counts_half(self):
        weights = self.weigh(["a", "b"], {"a": "UC1", "b": "UC2"}, halflife=1)
        self.assertAlmostEqual(0.5, weights["UC1"])
        self.assertAlmostEqual(1.0, weights["UC2"])

    def test_the_same_channel_accumulates_across_its_watches(self):
        weights = self.weigh(["a", "b"], {"a": "UC1", "b": "UC1"}, halflife=1)
        self.assertAlmostEqual(1.5, weights["UC1"])

    def test_recent_beats_often(self):
        """Four watches a fortnight ago must not outweigh two from yesterday."""
        watched = ["old%d" % n for n in range(4)] + ["new0", "new1"]
        channels = dict({"old%d" % n: "UCold" for n in range(4)},
                        new0="UCnew", new1="UCnew")
        weights = self.weigh(watched, channels, halflife=1)
        self.assertGreater(weights["UCnew"], weights["UCold"])

    def test_a_video_the_cache_knows_no_channel_for_is_skipped(self):
        self.assertEqual({}, self.weigh(["a"], {}))


class Multipliers(unittest.TestCase):

    def setUp(self):
        self.mod = load(IV_SUGGEST_ACCOUNT=ME)

    def test_the_most_watched_channel_takes_the_boost(self):
        got = self.mod.as_multipliers({"UC1": 10.0, "UC2": 1.0}, 2.0, 0.7)
        self.assertAlmostEqual(2.0, got["UC1"])

    def test_everything_lands_inside_the_range(self):
        got = self.mod.as_multipliers(
            {"UC%d" % n: float(n) for n in range(1, 40)}, 2.0, 0.7)
        self.assertTrue(all(0.7 <= v <= 2.0 for v in got.values()))

    def test_the_curve_is_log_so_the_middle_is_not_flattened_onto_the_floor(self):
        """Linear against the top left 574 of 586 real channels within 0.08 of
        the floor, which is a two-channel promotion rather than an affinity."""
        weights = dict({"UCtop": 15.0},
                       **{"UC%d" % n: 3.0 for n in range(20)})
        got = self.mod.as_multipliers(weights, 2.0, 0.7)
        middle = got["UC0"]
        self.assertGreater(middle, 1.0)
        self.assertLess(middle, got["UCtop"])

    def test_no_history_gives_no_table_rather_than_a_division(self):
        self.assertEqual({}, self.mod.as_multipliers({}, 2.0, 0.7))
        self.assertEqual({}, self.mod.as_multipliers({"UC1": 0.0}, 2.0, 0.7))


class LookingOneUp(unittest.TestCase):

    def setUp(self):
        self.mod = load(IV_SUGGEST_ACCOUNT=ME)
        self.cfg = dict(self.mod.DEFAULTS["affinity"])

    def test_a_channel_the_history_never_named_takes_the_floor(self):
        """A floor, not a zero: the cache knows no channel for most of the history."""
        self.assertEqual(self.cfg["floor"],
                         self.mod.affinity_of({"UC1": 2.0}, "UCnew", self.cfg))

    def test_an_empty_table_changes_nothing_at_all(self):
        """A fresh account has no history to be judged against."""
        self.assertEqual(1.0, self.mod.affinity_of({}, "UC1", self.cfg))

    def test_a_known_channel_takes_its_own_multiplier(self):
        self.assertEqual(2.0,
                         self.mod.affinity_of({"UC1": 2.0}, "UC1", self.cfg))


class ComputedOncePerAccount(unittest.TestCase):

    def setUp(self):
        self.mod = load(IV_SUGGEST_ACCOUNT=ME)
        self.mod._AFFINITY.clear()
        self.cfg = dict(self.mod.DEFAULTS["affinity"])

    def run_for(self, watched, channels):
        fetcher = type("F", (), {"meta": meta(**channels)})()
        return type("Run", (), {"watched": watched, "fetcher": fetcher})()

    def test_a_second_call_reuses_the_first_answer(self):
        run = self.run_for(["a"], {"a": "UC1"})
        first = self.mod.channel_affinity(run, self.cfg)
        self.assertIs(first, self.mod.channel_affinity(run, self.cfg))

    def test_another_account_does_not_get_the_first_one_s_table(self):
        """One invocation serves every managed account in turn."""
        mine = self.mod.channel_affinity(self.run_for(["a"], {"a": "UC1"}),
                                         self.cfg)
        self.mod.ACCOUNT = THEM
        theirs = self.mod.channel_affinity(self.run_for(["b"], {"b": "UC2"}),
                                           self.cfg)
        self.assertEqual({"UC1"}, set(mine.weights))
        self.assertEqual({"UC2"}, set(theirs.weights))

    def test_it_reads_only_the_scan_window(self):
        run = self.run_for(["old", "new"], {"old": "UCold", "new": "UCnew"})
        got = self.mod.channel_affinity(run, dict(self.cfg, scan=1))
        self.assertEqual({"UCnew"}, set(got.weights))


class WhereItIsApplied(unittest.TestCase):
    """Two readers, two shapes. `weights` is a recency-weighted watch count and
    replaces the raw count the channel draw used; `multipliers` is bounded."""

    def setUp(self):
        self.mod = load(IV_SUGGEST_ACCOUNT=ME)
        self.mod._AFFINITY.clear()

    def test_the_channel_draw_still_gets_a_count_shaped_number(self):
        weights = {"UC1": 9.0, "UC2": 0.1}
        drawn = dict(self.mod.channels_to_read(weights, 2))
        self.assertEqual({"UC1", "UC2"}, set(drawn))

    def test_weights_below_one_still_order_against_each_other(self):
        """A recency-weighted count is often under 1, where a raw count never was.
        The old guard clamped everything below 1 to 1 and lost the order."""
        self.mod.random.random = lambda: 0.5
        drawn = [cid for cid, _ in self.mod.channels_to_read(
            {"UCfaint": 0.2, "UCsome": 0.6, "UCstrong": 0.9}, 3)]
        self.assertEqual(["UCstrong", "UCsome", "UCfaint"], drawn)


if __name__ == "__main__":
    unittest.main()

"""The public feed: one playlist compiled from every account's home mix.

Three properties carry it. Agreement between mixes raises a video's weight
without gating its inclusion, so the feed ranks on what the household has in
common rather than on whoever contributed most. The playlist is a weighted
random sample, because a strict score ordering would pin the same dozen videos
to a public playlist for as long as the mixes hold them. And with one account
enrolled every weight collapses to that mix's 1/(rank+k), which is what the
instance already shows -- so the first version cannot regress the status quo.
"""

import ast
import unittest

from support import load, source
from test_sessions import callers_of, named_functions

ME = "andre@example.com"
OTHER = "sofie@example.com"
COLD = "newcomer@example.com"
HOUSEHOLD = (ME, OTHER, COLD)


def function_source(text, name):
    """One function's own source, for a property about what it may mention."""
    return ast.get_source_segment(text, named_functions(text)[name])


class Weighing(unittest.TestCase):
    """What depth in a mix and agreement between mixes each buy."""

    def setUp(self):
        self.mod = load(IV_SUGGEST_ACCOUNT=ME)
        self.cfg = self.mod.consensus_config({})

    def mixes(self, *lists):
        return [{"key": "mix%d" % n, "vids": list(vids)}
                for n, vids in enumerate(lists)]

    def weigh(self, *lists, **over):
        return self.mod.consensus_weights(self.mixes(*lists),
                                          dict(self.cfg, **over))

    def test_one_mix_weighs_every_video_at_one_over_its_rank_plus_k(self):
        """The status quo, stated as arithmetic: nothing else is in play."""
        k = self.cfg["rank_offset"]
        weights = self.weigh(["a", "b", "c"])
        self.assertEqual({"a": 1 / (0 + k), "b": 1 / (1 + k), "c": 1 / (2 + k)},
                         weights)

    def test_depth_in_one_mix_still_counts_for_something(self):
        weights = self.weigh(["a", "b", "c"])
        self.assertGreater(weights["a"], weights["b"])
        self.assertGreater(weights["b"], weights["c"])

    def tied_on_depth(self):
        """One mix's top pick against a video two mixes hold at rank 4: both 1/4."""
        return self.mixes(["solo_top"],
                          ["f0", "f1", "f2", "f3", "agreed"],
                          ["g0", "g1", "g2", "g3", "agreed"])

    def test_two_mixes_agreeing_beat_the_same_depth_reached_in_one(self):
        weights = self.mod.consensus_weights(self.tied_on_depth(), self.cfg)
        self.assertAlmostEqual(0.5, weights["agreed"])
        self.assertAlmostEqual(0.25, weights["solo_top"])

    def test_the_agreement_multiplier_is_what_breaks_that_tie(self):
        """Without it the two are equal, so the multiplier is doing the work."""
        depth = self.mod.depth_weights(self.tied_on_depth(),
                                       self.cfg["rank_offset"])
        self.assertAlmostEqual(depth["solo_top"], depth["agreed"])

    def test_a_video_every_mix_holds_outweighs_one_only_a_mix_holds(self):
        weights = self.weigh(["all", "mine"], ["all", "theirs"],
                             ["all", "somebody"])
        self.assertGreater(weights["all"], 3 * weights["mine"])

    def test_a_video_listed_twice_in_one_mix_is_still_one_holder(self):
        """Agreement is between accounts; a duplicated entry is not a second opinion."""
        self.assertEqual(self.mod.agreement_counts(self.mixes(["a", "a", "b"])),
                         {"a": 1, "b": 1})

    def test_a_duplicate_entry_does_not_pay_twice_for_depth_either(self):
        k = self.cfg["rank_offset"]
        self.assertEqual(self.weigh(["a", "a", "b"]),
                         {"a": 1 / (0 + k), "b": 1 / (1 + k)})

    def test_agreement_power_zero_leaves_the_plain_depth_sum(self):
        mixes = self.mixes(["a", "b"], ["a", "c"])
        self.assertEqual(
            self.mod.consensus_weights(mixes, dict(self.cfg, agreement_power=0)),
            self.mod.depth_weights(mixes, self.cfg["rank_offset"]))

    def test_a_rank_offset_of_zero_is_a_config_error_not_a_crash(self):
        with self.assertRaises(self.mod.Aborted) as caught:
            self.mod.consensus_config({"consensus": {"rank_offset": 0}})
        self.assertIn("rank_offset", str(caught.exception))

    def test_an_empty_mix_contributes_nothing_rather_than_breaking_the_merge(self):
        self.assertEqual(self.weigh(["a"], []), self.weigh(["a"]))


class Drawing(unittest.TestCase):
    """The weighted sample without replacement, which is what makes the feed random."""

    def setUp(self):
        self.mod = load(IV_SUGGEST_ACCOUNT=ME)
        self.mod.random.seed(20260901)

    def draw(self, weights, size):
        return self.mod.consensus_draw(weights, size)

    def leads(self, weights, size, trials=400):
        counted = {}
        for _ in range(trials):
            for vid in self.draw(weights, size):
                counted[vid] = counted.get(vid, 0) + 1
        return counted

    def test_it_draws_the_asked_for_number_and_never_repeats(self):
        drawn = self.draw({"a": 1.0, "b": 1.0, "c": 1.0, "d": 1.0}, 3)
        self.assertEqual(3, len(drawn))
        self.assertEqual(3, len(set(drawn)))

    def test_it_draws_only_from_the_pool(self):
        pool = {"a": 1.0, "b": 0.001}
        self.assertEqual(set(), set(self.draw(pool, 5)) - set(pool))

    def test_a_size_over_the_pool_returns_the_whole_pool_permuted(self):
        """The hourly redraw asks for everything the lane holds."""
        pool = {"a": 1.0, "b": 2.0, "c": 3.0}
        self.assertEqual(set(pool), set(self.draw(pool, 99)))

    def test_the_heavier_weight_leads_far_more_often(self):
        counted = self.leads({"heavy": 10.0, "light": 1.0}, 1)
        self.assertGreater(counted["heavy"], 4 * counted.get("light", 0))

    def test_a_low_weight_video_is_unlikely_rather_than_excluded(self):
        counted = self.leads({"heavy": 20.0, "light": 1.0}, 1, trials=2000)
        self.assertGreater(counted.get("light", 0), 0)
        self.assertLess(counted["light"], counted["heavy"])


class Degrading(unittest.TestCase):
    """One account enrolled: the feed is that account's home mix, shuffled hourly."""

    def setUp(self):
        self.mod = load(IV_SUGGEST_ACCOUNT=ME)
        self.mod.random.seed(20260901)
        self.mix = ["v%02d" % n for n in range(30)]
        self.cfg = self.mod.consensus_config({})
        self.weights = self.mod.consensus_weights(
            [{"key": ME + "/home-mix", "vids": self.mix}], self.cfg)

    def test_every_weight_is_that_mix_s_one_over_rank_plus_k(self):
        k = self.cfg["rank_offset"]
        self.assertEqual({vid: 1 / (rank + k)
                          for rank, vid in enumerate(self.mix)}, self.weights)

    def test_no_agreement_multiplier_can_apply_with_one_mix(self):
        self.assertEqual({vid: 1 for vid in self.mix},
                         self.mod.agreement_counts(
                             [{"key": "only", "vids": self.mix}]))

    def test_a_draw_at_the_mix_s_own_size_is_the_whole_mix_reordered(self):
        drawn = self.mod.consensus_draw(self.weights, len(self.mix))
        self.assertEqual(sorted(self.mix), sorted(drawn))

    def test_the_head_of_the_mix_leads_more_often_than_its_tail(self):
        led = {}
        for _ in range(600):
            top = self.mod.consensus_draw(self.weights, 1)[0]
            led[top] = led.get(top, 0) + 1
        head = sum(led.get(vid, 0) for vid in self.mix[:5])
        tail = sum(led.get(vid, 0) for vid in self.mix[-5:])
        self.assertGreater(head, tail)


class NoFetcher:
    """Any use of the run's fetcher at all fails the test that installed it."""

    def __getattr__(self, name):
        raise AssertionError("the consensus lane touched the fetcher: %s" % name)


class Rebuilding(unittest.TestCase):
    """run_lane_consensus against doubles for the two transports."""

    def setUp(self):
        self.mod = load(IV_SUGGEST_ACCOUNT=ME)
        self.mod.log = lambda msg: None
        self.mod.read_config = lambda: {}
        self.mod.load_users = lambda: [{"email": email} for email in HOUSEHOLD]
        self.mixes = {"PL_" + ME: [("mine1", "UC_ok"), ("shared", "UC_ok")],
                      "PL_" + OTHER: [("shared", "UC_ok"), ("theirs1", "UC_bad")]}
        self.blocked_rows = []
        self.queries = []
        self.mod.one = self.one
        self.mod.query = self.query
        self.mod.api = lambda method, path, body=None: self.called(method, path)
        self.mod.playlist_of = lambda lane, dry: ("PL_feed", {"videos": []})
        self.mod.execute = lambda sql: self.fail("a consensus lane wrote state")
        self.calls = []

    def called(self, method, path):
        self.calls.append((method, path))
        return {}

    def one(self, sql):
        if "FROM suggest.lanes" not in sql:
            return ""
        for email in self.mixes:
            if "'%s'" % email.split("PL_")[-1] in sql:
                return email
        return ""

    def query(self, sql):
        self.queries.append(sql)
        if "lower(p.title)" in sql:
            return [list(row) for row in self.blocked_rows]
        for plid, videos in self.mixes.items():
            if "'%s'" % plid in sql:
                return [list(v) for v in videos]
        return []

    def lane(self, **over):
        lane = {"id": "public", "title": "Public", "size": 10, "privacy": "public"}
        lane.update(over)
        return lane

    def context(self, watched=(), blocked=(), dry=False):
        return self.mod.LaneRun(list(watched), set(watched), set(), set(blocked),
                               NoFetcher(), dry, None)

    def compiled(self, watched=(), blocked=(), **over):
        drawn = []
        self.mod.consensus_draw = lambda weights, size: drawn.extend(
            sorted(weights)) or sorted(weights)[:size]
        self.mod.run_lane_consensus(self.lane(**over), self.context(watched, blocked))
        return drawn

    def test_the_sources_are_read_over_sql_not_the_api(self):
        self.compiled()
        self.assertTrue(any("playlist_videos" in q for q in self.queries))
        self.assertEqual({"POST"}, {method for method, _ in self.calls})

    def test_no_call_reaches_youtube(self):
        """Every path a fetch could take: the fetcher, /videos and /channels."""
        removed, added, kept, used = self.mod.run_lane_consensus(
            self.lane(), self.context())
        self.assertEqual(0, used)
        self.assertTrue(added)
        for method, path in self.calls:
            self.assertTrue(path.startswith("/api/v1/auth/playlists"), path)

    def test_it_writes_no_state_of_its_own(self):
        self.compiled()

    def test_what_somebody_watched_is_not_filtered_out(self):
        """There is no viewer, so there is no history it could be."""
        self.assertIn("shared", self.compiled(watched=["shared", "mine1"]))

    def test_a_channel_any_account_blocked_is_kept_out(self):
        self.blocked_rows = [("UC_bad", "Bad Channel")]
        drawn = self.compiled()
        self.assertNotIn("theirs1", drawn)
        self.assertIn("shared", drawn)

    def test_the_blocklist_is_asked_of_every_managed_account(self):
        self.compiled()
        asked = [q for q in self.queries if "lower(p.title)" in q]
        self.assertEqual(1, len(asked))
        for email in HOUSEHOLD:
            self.assertIn(email, asked[0])

    def test_the_acting_account_s_own_blocked_set_is_not_what_filters(self):
        """It reads the union, which already contains it; run.blocked is not a source."""
        self.assertIn("theirs1", self.compiled(blocked=["UC_bad"]))

    def test_an_account_with_no_mix_yet_contributes_nothing(self):
        self.assertEqual(["mine1", "shared", "theirs1"], sorted(set(self.compiled())))

    def test_a_named_source_lane_that_does_not_exist_aborts_by_name(self):
        self.mod.one = lambda sql: ""
        with self.assertRaises(self.mod.Aborted) as caught:
            self.mod.run_lane_consensus(
                self.lane(consensus={"sources": [{"user": OTHER,
                                                  "lane": "home-mix"}]}),
                self.context())
        self.assertIn("home-mix", str(caught.exception))

    def test_no_source_mix_at_all_aborts_rather_than_emptying_the_playlist(self):
        self.mod.one = lambda sql: ""
        with self.assertRaises(self.mod.Aborted):
            self.mod.run_lane_consensus(self.lane(), self.context())

    def test_the_playlist_is_cleared_before_it_is_refilled(self):
        self.mod.playlist_of = lambda lane, dry: (
            "PL_feed", {"videos": [{"videoId": "old1", "indexId": "ix1"}]})
        self.mod.run_lane_consensus(self.lane(), self.context())
        self.assertEqual(("DELETE", "/api/v1/auth/playlists/PL_feed/videos/ix1"),
                         self.calls[0])
        self.assertEqual({"POST"}, {m for m, _ in self.calls[1:]})

    def test_a_rebuild_with_nothing_in_it_keeps_the_playlist_as_it_is(self):
        """A public feed going blank is not an answer worth publishing."""
        self.mixes = {"PL_" + ME: [], "PL_" + OTHER: []}
        self.mod.playlist_of = lambda lane, dry: (
            "PL_feed", {"videos": [{"videoId": "old1", "indexId": "ix1"}]})
        with self.assertRaises(self.mod.Aborted) as caught:
            self.mod.run_lane_consensus(self.lane(), self.context())
        self.assertIn("keeping the 1 videos", str(caught.exception))
        self.assertEqual([], self.calls)

    def test_a_lane_asked_to_hold_nothing_is_allowed_to_hold_nothing(self):
        """size 0 means empty on purpose, so emptying it is not a failure."""
        self.mixes = {"PL_" + ME: [], "PL_" + OTHER: []}
        self.mod.playlist_of = lambda lane, dry: (
            "PL_feed", {"videos": [{"videoId": "old1", "indexId": "ix1"}]})
        removed, added, kept, used = self.mod.run_lane_consensus(
            self.lane(size=0), self.context())
        self.assertEqual((1, 0, 0, 0), (removed, added, kept, used))

    def test_an_empty_draw_into_an_empty_playlist_is_not_an_error(self):
        """Nothing to keep, so nothing to protect: the first run of a new lane."""
        self.mixes = {"PL_" + ME: [], "PL_" + OTHER: []}
        removed, added, kept, used = self.mod.run_lane_consensus(
            self.lane(), self.context())
        self.assertEqual((0, 0, 0, 0), (removed, added, kept, used))

    def test_a_dry_run_writes_nothing_at_all(self):
        self.mod.api = lambda *a, **k: self.fail("a dry run called the API")
        removed, added, kept, used = self.mod.run_lane_consensus(
            self.lane(), self.context(dry=True))
        self.assertEqual(0, used)


class Redrawing(unittest.TestCase):
    """The hourly redraw: consensus_order, which may permute and nothing else."""

    def setUp(self):
        self.mod = load(IV_SUGGEST_ACCOUNT=ME)
        self.mod.log = lambda msg: None
        self.mod.random.seed(20260901)
        self.mod.load_users = lambda: [{"email": email} for email in (ME, OTHER)]
        self.orders = {ME: ["a", "b", "c"], OTHER: ["a", "c", "b"]}
        self.mod.source_order = lambda src: list(self.orders.get(src["user"], []))

    def items(self, *vids, **over):
        return [dict({"idx": n + 1, "vid": vid, "ucid": "UC", "title": vid,
                      "score": 1.0, "top": 0, "age": 1.0, "since_top": 1.0},
                     **over.get(vid, {})) for n, vid in enumerate(vids)]

    def order(self, items):
        return self.mod.consensus_order({"id": "public"}, items, {})

    def test_the_redraw_is_a_permutation_and_never_changes_membership(self):
        """Membership stays the nightly run's call, exactly as it does for a mix."""
        items = self.items("a", "b", "c")
        ordered = self.order(items)
        self.assertEqual(sorted(it["vid"] for it in items),
                         sorted(it["vid"] for it in ordered))

    def test_a_video_no_mix_holds_any_more_sinks_to_the_end(self):
        ordered = self.order(self.items("a", "b", "c", "stale"))
        self.assertEqual("stale", ordered[-1]["vid"])

    def test_it_draws_fresh_and_reads_no_fatigue_counter(self):
        """Fresh every hour: a public playlist has no one viewer to disorient."""
        weary = {"a": {"top": 12, "score": 0.01}, "c": {"score": 100.0}}
        leads = {}
        for _ in range(200):
            top = self.order(self.items("a", "b", "c", **weary))[0]["vid"]
            leads[top] = leads.get(top, 0) + 1
        self.assertGreater(leads.get("a", 0), leads.get("b", 0))
        self.assertGreater(leads.get("a", 0), leads.get("c", 0))

    def test_no_source_with_an_order_yet_skips_the_lane(self):
        self.mod.source_order = lambda src: []
        self.assertIsNone(self.order(self.items("a", "b", "c")))

    def test_the_shuffle_skips_a_lane_whose_sources_have_no_order(self):
        self.mod.source_order = lambda src: []
        self.assertEqual(["a", "b", "c"], self.shuffled(policy="consensus"))

    def test_a_consensus_lane_ages_no_fatigue_counter(self):
        self.mod.age_fatigue = lambda lane_id, cfg, after: self.fail(
            "a compiled lane aged a fatigue counter it does not write")
        self.assertEqual(3, len(self.shuffled(policy="consensus")))

    def shuffled(self, **over):
        lane = dict({"id": "public", "policy": "consensus", "size": 10,
                     "shuffle": dict(self.mod.SHUFFLE_DEFAULTS)}, **over)
        self.mod.lane_plid = lambda account, lane_id: "PL_feed"
        self.mod.read_order = lambda plid, lane_id: [
            [n + 1, vid, "UC", 1.0, 0, 1.0, 1.0, vid]
            for n, vid in enumerate(["a", "b", "c"])]
        self.mod.permute_playlist_index = lambda plid, ordered: None
        self.mod.execute = lambda sql: None
        return self.mod.shuffle_lane(lane, {}, dry=False)


class Configuring(unittest.TestCase):
    """What the config machinery has to know about the new policy."""

    def setUp(self):
        self.mod = load(IV_SUGGEST_ACCOUNT=ME)

    def test_the_keys_a_consensus_lane_never_reads_are_named(self):
        self.assertEqual(
            ["exclude_watched", "expand", "mix"],
            self.mod.ignored_keys("consensus", {
                "id": "public", "title": "Public", "size": 40, "privacy": "public",
                "consensus": {}, "exclude_watched": True, "expand": "none",
                "mix": {}}))

    def test_the_keys_it_does_read_are_not_reported_inert(self):
        for key in ("consensus", "size", "privacy", "shuffle", "min_watched"):
            self.assertEqual([], self.mod.ignored_keys(
                "consensus", {"id": "public", "title": "Public", key: {}}))

    def test_the_consensus_block_merges_key_by_key_with_the_defaults(self):
        """Setting one knob must not drop the source list with it."""
        merged = self.mod.merge_lane(
            {"consensus": {"sources": [{"users": "all", "lane": "home-mix"}],
                           "rank_offset": 4.0}},
            {"consensus": {"rank_offset": 9.0}})
        self.assertEqual(9.0, merged["consensus"]["rank_offset"])
        self.assertEqual([{"users": "all", "lane": "home-mix"}],
                         merged["consensus"]["sources"])

    def test_the_default_source_is_every_account_s_home_mix(self):
        self.assertEqual([{"users": "all", "lane": "home-mix"}],
                         self.mod.consensus_config({})["sources"])

    def test_a_consensus_lane_is_counted_from_its_last_run(self):
        """It writes no suggest.items row, so counting those would report zero."""
        self.assertEqual(40, self.mod.lane_video_count(
            "consensus", 0, ["public", "1700000000", "40", "0", "0", "0", ""]))

    def test_a_consensus_lane_is_alerted_against_no_target_size(self):
        self.assertEqual(0, self.mod.lane_target(
            {"policy": "consensus", "size": 40}))


class Statically(unittest.TestCase):
    """The two properties a behavioural test only covers where somebody drove it."""

    def setUp(self):
        self.source = source()

    def test_the_hourly_redraw_never_reaches_the_api(self):
        self.assertNotIn("consensus_order", callers_of(self.source, "api"))
        self.assertNotIn("consensus_mixes_now", callers_of(self.source, "api"))

    def test_the_consensus_run_never_touches_the_fetcher(self):
        for name in ("run_lane_consensus", "consensus_mixes", "consensus_weights",
                     "consensus_draw", "rebuild_playlist"):
            self.assertNotIn("fetcher", function_source(self.source, name), name)

    def test_the_consensus_run_reaches_no_candidate_expansion(self):
        for spender in ("expand_candidates", "pick_seeds", "choose_candidates"):
            self.assertNotIn("run_lane_consensus",
                             callers_of(self.source, spender), spender)


if __name__ == "__main__":
    unittest.main()

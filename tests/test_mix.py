"""The household mix: everybody's best in one feed, filtered per viewer.

Two properties carry the feature. Sources are read over SQL, so blending
somebody else's lane never needs their session. And the filtering happens for
the person looking at the feed, not for the person the video came from --
otherwise the mix is just a copy of whoever contributed most.
"""

import unittest

from support import load

ME = "andre@example.com"
OTHER = "sofie@example.com"


class Sources(unittest.TestCase):

    def setUp(self):
        self.mod = load(IV_SUGGEST_ACCOUNT=ME)

    def test_a_source_without_a_user_means_the_account_being_filled(self):
        """Every mix config written before this has to read the same."""
        got = self.mod.mix_sources(
            {"sources": [{"lane": "suggested", "share": 0.9}]})
        self.assertEqual("", got[0]["user"])
        self.assertEqual("suggested", got[0]["key"])

    def test_the_old_base_blend_form_still_parses(self):
        got = self.mod.mix_sources({"base": "suggested", "blend": "music", "ratio": 3})
        self.assertEqual(["suggested", "music"], [s["lane"] for s in got])
        self.assertEqual([3.0, 1.0], [s["share"] for s in got])

    def test_two_accounts_sharing_a_lane_name_are_two_sources(self):
        """Keyed by lane alone they would collapse into one share."""
        got = self.mod.mix_sources({"sources": [
            {"user": ME, "lane": "suggested", "share": 0.5},
            {"user": OTHER, "lane": "suggested", "share": 0.5}]})
        self.assertEqual(2, len({s["key"] for s in got}))

    def test_a_mix_lane_with_no_sources_is_a_config_error(self):
        with self.assertRaises(self.mod.Aborted):
            self.mod.mix_sources({})


class Blend(unittest.TestCase):
    """weighted_mix over cross-account sources."""

    def setUp(self):
        self.mod = load(IV_SUGGEST_ACCOUNT=ME)

    def blend(self, sources, size, pure=0):
        prepared = self.mod.mix_sources({"sources": [
            {"user": s[0], "lane": "suggested", "share": s[1]} for s in sources]})
        for s, (_, _, vids) in zip(prepared, sources):
            s["vids"] = list(vids)
        return self.mod.weighted_mix(prepared, size, pure)

    def test_equal_shares_split_the_feed_evenly(self):
        mine = ["a%d" % i for i in range(10)]
        theirs = ["b%d" % i for i in range(10)]
        out = self.blend([(ME, 0.5, mine), (OTHER, 0.5, theirs)], 10)
        self.assertEqual(5, sum(1 for v in out if v.startswith("a")))
        self.assertEqual(5, sum(1 for v in out if v.startswith("b")))

    def test_a_heavier_share_keeps_the_feed_feeling_like_the_viewer_s(self):
        mine = ["a%d" % i for i in range(10)]
        theirs = ["b%d" % i for i in range(10)]
        out = self.blend([(ME, 0.8, mine), (OTHER, 0.2, theirs)], 10)
        self.assertEqual(8, sum(1 for v in out if v.startswith("a")))

    def test_a_video_both_accounts_hold_appears_once(self):
        out = self.blend([(ME, 0.5, ["x", "a1"]), (OTHER, 0.5, ["x", "b1"])], 4)
        self.assertEqual(1, out.count("x"))

    def test_an_empty_account_costs_the_others_nothing(self):
        """A new account with no history must not leave holes in the feed."""
        mine = ["a%d" % i for i in range(10)]
        out = self.blend([(ME, 0.5, mine), (OTHER, 0.5, [])], 10)
        self.assertEqual(10, len(out))


class PerViewer(unittest.TestCase):
    """run_lane_mix filters for whoever is being filled."""

    def setUp(self):
        self.mod = load(IV_SUGGEST_ACCOUNT=ME)
        self.mod.log = lambda msg: None
        self.queries = []
        # Both accounts hold a "suggested" lane; the plid says whose.
        self.playlists = {
            "PL_me": [("mine1", "UC_ok"), ("shared", "UC_ok")],
            "PL_them": [("theirs1", "UC_ok"), ("theirs2", "UC_bad")],
        }

        def one(sql):
            if "FROM suggest.lanes" in sql:
                return "PL_me" if ("'%s'" % ME) in sql else "PL_them"
            return ""

        def query(sql):
            self.queries.append(sql)
            for plid, vids in self.playlists.items():
                if "'%s'" % plid in sql:
                    return [list(v) for v in vids]
            return []

        self.mod.one = one
        self.mod.query = query
        self.mod.playlist_of = lambda lane, dry: (None, {"videos": []})

    def lane(self, **over):
        lane = {"id": "home", "title": "Home", "size": 10, "exclude_watched": True,
                "mix": {"sources": [
                    {"user": ME, "lane": "suggested", "share": 0.5},
                    {"user": OTHER, "lane": "suggested", "share": 0.5}]}}
        lane.update(over)
        return lane

    # NB: not called run(); that is TestCase's own entry point.
    def mixed(self, watched=(), blocked=()):
        return self.mod.run_lane_mix(self.lane(), set(watched), set(blocked),
                                     dry=True)

    def test_the_sources_are_read_over_sql_not_the_api(self):
        """Blending another account's lane must not need their session."""
        self.mod.api = lambda *a, **k: self.fail("the mix called the API")
        self.mixed()
        self.assertTrue(any("playlist_videos" in q for q in self.queries))

    def test_what_this_viewer_already_watched_is_dropped(self):
        picked = []
        self.mod.weighted_mix = lambda sources, size, pure=0: picked.extend(
            v for s in sources for v in s["vids"]) or []
        self.mixed(watched=["theirs1"])
        self.assertNotIn("theirs1", picked)
        self.assertIn("theirs2", picked)

    def test_this_viewer_s_blocklist_applies_to_what_others_contributed(self):
        picked = []
        self.mod.weighted_mix = lambda sources, size, pure=0: picked.extend(
            v for s in sources for v in s["vids"]) or []
        self.mixed(blocked=["UC_bad"])
        self.assertNotIn("theirs2", picked)
        self.assertIn("theirs1", picked)

    def test_a_source_lane_that_does_not_exist_yet_aborts_by_name(self):
        self.mod.one = lambda sql: ""
        with self.assertRaises(self.mod.Aborted) as caught:
            self.mixed()
        self.assertIn("suggested", str(caught.exception))

    def test_a_mix_writes_no_state_and_costs_no_fetches(self):
        self.mod.execute = lambda sql: self.fail("a mix wrote state")
        removed, added, kept, used = self.mixed()
        self.assertEqual(0, used)


if __name__ == "__main__":
    unittest.main()

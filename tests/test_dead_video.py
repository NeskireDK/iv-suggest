"""One video the uploader removed must not truncate a rebuilt playlist.

2026-09-15, live: `4BNrCpPnMss` was added to `suggested` on the 13th and pulled
by its uploader on the 15th. `rebuild_playlist` deletes every video and then
re-adds them, so the 500 on that one add raised AFTER the deletes had run and
left `home-mix` holding 32 of 54 and both `household` lanes 13 short. `popular`
then compiled from the truncated mix. `IvSuggestLaneError` caught it, but the
front page was 22 slots short for twelve hours.

The refill path had always handled this -- `add_to_lane` catches the refusal,
buries the video and moves on. The compiled path never got the same treatment.

Three things follow, and each is a test below: a refusal may not raise, a video
known to be dead is never offered to a rebuild in the first place, and a dead
video leaves the lane that still holds it rather than waiting out its TTL.
"""

import unittest
import urllib.error

from support import load

ME = "andre@example.com"


def refuse(vid, code=500):
    def call(method, path, body=None):
        if method == "POST" and (body or {}).get("videoId") == vid:
            raise urllib.error.HTTPError(path, code, "Server Error", {}, None)
        return {}
    return call


class ARefusalDoesNotRaise(unittest.TestCase):

    def setUp(self):
        self.mod = load(IV_SUGGEST_ACCOUNT=ME)
        self.buried, self.forgotten = [], []
        self.mod.bury_video = lambda dead, vid, days, why: \
            self.buried.append((vid, days, why))
        self.mod.forget_a_dead_video_everywhere = self.forgotten.append
        self.mod.execute = lambda sql: None
        self.mod.playlist_of = lambda lane, dry: ("PL1", {"videos": []})

    def run_with(self, desired, bad):
        self.mod.bot_api = refuse(bad)
        lane = dict(self.mod.DEFAULTS, id="home-mix", policy="mix", size=4)
        run = self.mod.LaneRun([], set(), set(), set(), None, False, None, set())
        return self.mod.rebuild_playlist(lane, desired, run)

    def test_the_rest_of_the_lane_still_lands(self):
        """The bug: the lane published whatever had been re-added so far."""
        removed, added, kept, fetches = self.run_with(["a", "b", "bad", "d"], "bad")
        self.assertEqual(3, added)

    def test_the_count_it_reports_is_what_landed_not_what_was_wanted(self):
        """A run row claiming 4 of 4 would hide the hole from every metric."""
        _, added, _, _ = self.run_with(["a", "b", "bad", "d"], "bad")
        self.assertNotEqual(4, added)

    def test_the_refused_video_is_buried_so_no_later_run_retries_it(self):
        self.run_with(["a", "bad"], "bad")
        self.assertEqual(1, len(self.buried))
        self.assertEqual("bad", self.buried[0][0])
        self.assertEqual(self.mod.DAYS_BEFORE_RETRYING_A_BROKEN_VIDEO,
                         self.buried[0][1])

    def test_it_is_dropped_from_every_lane_that_still_holds_it(self):
        """It lives in a SOURCE lane; leaving it there re-offers it tomorrow."""
        self.run_with(["a", "bad"], "bad")
        self.assertEqual(["bad"], self.forgotten)

    def test_a_404_is_treated_the_same_as_a_500(self):
        self.mod.bot_api = refuse("bad", 404)
        lane = dict(self.mod.DEFAULTS, id="home-mix", policy="mix", size=4)
        run = self.mod.LaneRun([], set(), set(), set(), None, False, None, set())
        _, added, _, _ = self.mod.rebuild_playlist(lane, ["a", "bad"], run)
        self.assertEqual(1, added)


class NotOfferedInTheFirstPlace(unittest.TestCase):
    """Catching the refusal is the backstop. Not asking is the fix."""

    def setUp(self):
        self.mod = load(IV_SUGGEST_ACCOUNT=ME)
        self.mod.read_config = lambda: {}
        self.mod.lane_plid = lambda account, lane_id: "PL_src"
        self.mod.playlist_videos = lambda plid: [
            ("good0000001", "UCa"), ("dead0000001", "UCb")]

    def test_a_mix_source_skips_a_video_known_to_be_dead(self):
        self.mod.rebuild_playlist = lambda lane, desired, run: desired
        lane = dict(self.mod.DEFAULTS, id="home-mix", title="Home", policy="mix",
                    size=10, mix={"sources": [{"lane": "suggested", "share": 1.0}]})
        run = self.mod.LaneRun([], set(), set(), set(), None, False, None,
                               {"dead0000001"})
        self.assertEqual(["good0000001"], self.mod.run_lane_mix(lane, run))

    def test_a_consensus_source_skips_it_too(self):
        cfg = dict(self.mod.CONSENSUS_DEFAULTS)
        got = self.mod.consensus_mixes(cfg, set(), {"dead0000001"})
        self.assertEqual(["good0000001"], got[0]["vids"])

    def test_the_consensus_path_reads_the_run_and_never_the_fetcher(self):
        """A compiled lane costs no fetch, and the dead set must not smuggle one in."""
        from support import source
        text = source()
        for name in ("run_lane_consensus", "consensus_mixes", "rebuild_playlist"):
            body = text.split("def %s(" % name)[1].split("\ndef ")[0]
            self.assertNotIn("fetcher", body, name)


class ItLeavesTheLaneHoldingIt(unittest.TestCase):

    def setUp(self):
        self.mod = load(IV_SUGGEST_ACCOUNT=ME)
        self.lane = dict(self.mod.DEFAULTS)

    def why(self, dead=()):
        run = self.mod.LaneRun([], set(), set(), set(), None, True, None, set(dead))
        return self.mod.why_it_leaves(
            {"videoId": "dead0000001", "authorId": "UCx"},
            self.lane, run, {"dead0000001": "2026-09-13"}, "", "")

    def test_a_live_video_stays(self):
        self.assertIsNone(self.why())

    def test_a_dead_one_leaves_rather_than_waiting_out_its_ttl(self):
        """Its TTL was 14 days. A removed video should not sit in a lane that long."""
        self.assertEqual("dead", self.why(dead={"dead0000001"}))

    def test_it_carries_no_cooldown_because_video_dead_already_holds_it(self):
        self.assertEqual(0, self.mod.cooldown_days_for(self.lane, "dead"))


if __name__ == "__main__":
    unittest.main()

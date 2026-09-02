"""What a fetch costs: the run budget, and the lane's share of that budget.

`budget` is the run's total YouTube spend and `fetch_cap` is one lane's share of
it, so both have to count the same thing. A retry is another call upstream, so it
is another fetch. These pin that, and pin where BudgetSpent is raised: at the top
of a call, never in the middle of one, so a call already retrying is never cut in
half and the overshoot stays inside the call that caused it.
"""

import time
import unittest
import urllib.error

from support import load

ME = "andre@example.com"
OUTAGE = urllib.error.HTTPError("/api/v1/videos/x", 503, "boom", None, None)
GONE = urllib.error.HTTPError("/api/v1/videos/x", 404, "gone", None, None)
RATE_LIMITED = urllib.error.HTTPError("/api/v1/videos/x", 429, "slow down",
                                      None, None)
LISTING = {"videos": []}
VIDEO = {"videoId": "v", "title": "V", "authorId": "UC-a", "lengthSeconds": 60}


class NoWaiting:
    """The time module with the waiting taken out, so a retry test runs instantly."""

    monotonic = staticmethod(time.monotonic)
    time = staticmethod(time.time)

    @staticmethod
    def sleep(seconds):
        pass


class Upstream:
    """The video endpoint without a network, answering calls from a script."""

    def __init__(self, *answers, alive=False):
        self.answers = list(answers)
        self.alive = alive
        self.asked = []

    def call(self, method, path, body=None):
        if path.endswith("/stats"):
            return {"version": "2"} if self.alive else {}
        self.asked.append(path)
        answer = self.answers.pop(0) if self.answers else VIDEO
        if isinstance(answer, Exception):
            raise answer
        return answer


class BudgetCase(unittest.TestCase):

    def setUp(self):
        self.mod = load(IV_SUGGEST_ACCOUNT=ME)
        self.mod.query = lambda sql: []
        self.mod.one = lambda sql: ""
        self.mod.execute = lambda sql: None
        self.mod.log = lambda line: None
        self.mod.time = NoWaiting

    def fetcher(self, upstream, budget=10, lane_cap=None):
        self.mod.api = upstream.call
        fetch = self.mod.Fetcher(budget=budget)
        fetch.begin_lane(lane_cap)
        return fetch


class RunBudget(BudgetCase):
    """What the run's total budget allows, and when it refuses."""

    def test_a_plain_fetch_costs_one(self):
        fetch = self.fetcher(Upstream(VIDEO))
        fetch.video("v0000000000")
        self.assertEqual(1, fetch.fetches)

    def test_the_budget_refuses_the_call_after_it_is_spent(self):
        fetch = self.fetcher(Upstream(VIDEO, VIDEO), budget=1)
        fetch.video("v0000000000")
        with self.assertRaises(self.mod.BudgetSpent):
            fetch.video("v0000000001")

    def test_a_retried_call_is_never_cut_in_half_by_the_budget(self):
        upstream = Upstream(OUTAGE, OUTAGE, OUTAGE)
        fetch = self.fetcher(upstream, budget=2)
        self.assertIsNone(fetch.video("v0000000000"))
        self.assertEqual(3, len(upstream.asked),
                         "the budget is checked before the retry loop, so the "
                         "loop runs to its end even once the budget is over")
        self.assertEqual(3, fetch.fetches)

    def test_a_video_upstream_has_lost_costs_the_one_attempt_it_took(self):
        fetch = self.fetcher(Upstream(GONE))
        self.assertIsNone(fetch.video("v0000000000"))
        self.assertEqual(1, fetch.fetches)

    def test_a_dead_video_is_free(self):
        upstream = Upstream(VIDEO)
        fetch = self.fetcher(upstream)
        fetch.dead.add("v0000000000")
        self.assertIsNone(fetch.video("v0000000000"))
        self.assertEqual([], upstream.asked)
        self.assertEqual(0, fetch.fetches)


class LaneShare(BudgetCase):
    """The per-lane cap counts what the run budget counts, retries included."""

    def test_a_plain_fetch_costs_the_lane_one(self):
        fetch = self.fetcher(Upstream(VIDEO), lane_cap=5)
        fetch.video("v0000000000")
        self.assertEqual(1, fetch.lane_used)

    def test_the_cap_refuses_the_call_after_it_is_reached(self):
        fetch = self.fetcher(Upstream(VIDEO, VIDEO), lane_cap=1)
        fetch.video("v0000000000")
        with self.assertRaises(self.mod.BudgetSpent) as raised:
            fetch.video("v0000000001")
        self.assertIn("lane fetch cap", str(raised.exception))

    def test_a_lane_with_no_cap_is_never_refused_for_its_share(self):
        fetch = self.fetcher(Upstream(VIDEO, VIDEO, VIDEO), lane_cap=None)
        for n in range(3):
            fetch.video("v000000000%d" % n)
        self.assertEqual(3, fetch.lane_used)

    def test_begin_lane_resets_the_share_for_the_next_lane(self):
        fetch = self.fetcher(Upstream(VIDEO, VIDEO), lane_cap=1)
        fetch.video("v0000000000")
        fetch.begin_lane(1)
        self.assertEqual(0, fetch.lane_used)
        fetch.video("v0000000001")

    def test_a_retry_costs_the_lane_what_it_costs_the_run(self):
        upstream = Upstream(OUTAGE, OUTAGE, OUTAGE)
        fetch = self.fetcher(upstream, lane_cap=5)
        fetch.video("v0000000000")
        self.assertEqual(3, fetch.fetches)
        self.assertEqual(fetch.fetches, fetch.lane_used,
                         "a retry the run budget paid for must come out of the "
                         "lane's share too, or the cap cannot hold")

    def test_a_lane_that_spent_its_cap_on_retries_is_refused_next(self):
        upstream = Upstream(OUTAGE, OUTAGE, OUTAGE, VIDEO)
        fetch = self.fetcher(upstream, lane_cap=2)
        fetch.video("v0000000000")
        with self.assertRaises(self.mod.BudgetSpent):
            fetch.video("v0000000001")

    def test_a_channel_listing_costs_the_lane_one(self):
        fetch = self.fetcher(Upstream({"videos": []}), lane_cap=5)
        fetch.channel_latest("UC-a")
        self.assertEqual(1, fetch.lane_used)
        self.assertEqual(1, fetch.fetches)

    def test_a_channel_listing_is_refused_once_the_cap_is_reached(self):
        fetch = self.fetcher(Upstream(LISTING, LISTING), lane_cap=1)
        fetch.channel_latest("UC-a")
        with self.assertRaises(self.mod.BudgetSpent):
            fetch.channel_latest("UC-b")


class ChannelListingRetries(BudgetCase):
    """A channel listing survives a wobble, the same way a video fetch does.

    It used to spend its fetch on the first error and return nothing, so a
    transient 429 cost the lane a channel it could have had.
    """

    def test_a_rate_limited_listing_is_retried_and_then_succeeds(self):
        upstream = Upstream(RATE_LIMITED, LISTING)
        fetch = self.fetcher(upstream)
        self.assertEqual(LISTING, fetch.channel_latest("UC-a"))
        self.assertEqual(2, len(upstream.asked))

    def test_a_listing_that_keeps_failing_gives_up_after_three_attempts(self):
        upstream = Upstream(RATE_LIMITED, RATE_LIMITED, RATE_LIMITED)
        fetch = self.fetcher(upstream)
        self.assertIsNone(fetch.channel_latest("UC-a"))
        self.assertEqual(3, len(upstream.asked))

    def test_every_attempt_at_a_listing_is_charged_to_the_lane_and_the_run(self):
        upstream = Upstream(RATE_LIMITED, RATE_LIMITED, LISTING)
        fetch = self.fetcher(upstream, lane_cap=5)
        fetch.channel_latest("UC-a")
        self.assertEqual(3, fetch.fetches)
        self.assertEqual(fetch.fetches, fetch.lane_used)

    def test_a_channel_upstream_has_lost_is_not_retried(self):
        upstream = Upstream(GONE, LISTING)
        fetch = self.fetcher(upstream)
        self.assertIsNone(fetch.channel_latest("UC-a"))
        self.assertEqual(1, len(upstream.asked))

    def test_one_bad_channel_on_a_healthy_instance_is_skipped_not_retried(self):
        upstream = Upstream(OUTAGE, LISTING, alive=True)
        fetch = self.fetcher(upstream)
        self.assertIsNone(fetch.channel_latest("UC-a"))
        self.assertEqual(1, len(upstream.asked),
                         "a 5xx from a healthy instance is that channel's "
                         "problem, and retrying it only spends the budget")

    def test_a_run_wide_outage_still_aborts_the_run(self):
        upstream = Upstream(*([RATE_LIMITED] * 9))
        fetch = self.fetcher(upstream, budget=20)
        with self.assertRaises(self.mod.Aborted):
            for ucid in ("UC-a", "UC-b", "UC-c"):
                fetch.channel_latest(ucid)


if __name__ == "__main__":
    unittest.main()

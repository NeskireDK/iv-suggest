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


class AbortsAreCountedPerLane(BudgetCase):
    """An abort must not leave the counter primed for whatever runs next.

    `fails` lives on the Fetcher, and the Fetcher lives for the whole run. It
    stayed at MAX_CONSECUTIVE_FAILS after an abort, so the next lane's first
    single failure aborted that lane too, and so on to the end of the night: a
    two-minute wobble in one lane cost every lane after it.
    """

    def abort(self, fetch):
        with self.assertRaises(self.mod.Aborted) as caught:
            for ucid in ("UC-a", "UC-b"):
                fetch.channel_latest(ucid)
        self.assertNotIsInstance(caught.exception, self.mod.BudgetSpent,
                                 "a refused fetch is not a lane giving up")
        return caught.exception

    def test_the_counter_is_clear_once_the_abort_is_raised(self):
        fetch = self.fetcher(Upstream(*([RATE_LIMITED] * 5)), budget=20)
        self.abort(fetch)
        self.assertEqual(0, fetch.fails)

    def test_the_abort_still_says_how_many_failures_it_took(self):
        """The clear runs first, so the count has to come from the constant, not the counter."""
        fetch = self.fetcher(Upstream(*([RATE_LIMITED] * 5)), budget=20)
        self.assertEqual("%d consecutive fetch failures"
                         % self.mod.MAX_CONSECUTIVE_FAILS,
                         str(self.abort(fetch)))

    def test_the_next_lane_survives_one_failure_of_its_own(self):
        fetch = self.fetcher(
            Upstream(*([RATE_LIMITED] * 6 + [LISTING])), budget=20)
        self.abort(fetch)
        fetch.begin_lane(None)
        self.assertEqual(LISTING, fetch.channel_latest("UC-c"))

    def test_a_video_fetch_after_an_abort_survives_one_failure_too(self):
        fetch = self.fetcher(
            Upstream(*([RATE_LIMITED] * 5 + [OUTAGE, VIDEO])), budget=20)
        self.abort(fetch)
        self.assertEqual(VIDEO, fetch.video("v0000000000"))

    def test_a_second_abort_needs_a_fresh_run_of_failures(self):
        upstream = Upstream(*([RATE_LIMITED] * 10))
        fetch = self.fetcher(upstream, budget=30)
        self.abort(fetch)
        fetch.begin_lane(None)
        self.assertIsNone(fetch.channel_latest("UC-c"))
        with self.assertRaises(self.mod.Aborted):
            fetch.channel_latest("UC-d")
        self.assertEqual(10, len(upstream.asked),
                         "5 failures to the first abort, then a whole fresh 5")


class FailuresAreCountedPerLane(BudgetCase):
    """A lane that ends WITHOUT aborting must not hand its unspent failures on.

    Only the abort cleared the counter, and `begin_lane` cleared the fetch cap
    and nothing else. So the ordinary case leaked: `fresh-uploads` meets a few
    dud channel listings, returns None for each without ever aborting, and the
    next fetching lane aborted on its first or second failure of its own --
    after its sweep had already issued the playlist DELETEs. A cache hit does
    not clear it either, so a zero-fetch lane in between changes nothing.
    """

    def dud_listings(self, fetch, how_many):
        for attempt in range(how_many):
            fetch.channel_latest("UC-dud")

    def test_a_lane_that_never_aborted_leaves_no_failures_behind(self):
        fetch = self.fetcher(Upstream(*([RATE_LIMITED] * 20)), budget=100)
        self.dud_listings(fetch, 1)
        self.assertEqual(3, fetch.fails, "three attempts, no abort")
        fetch.begin_lane(None)
        self.assertEqual(0, fetch.fails)

    def test_the_next_lane_gets_its_own_five_failures_not_what_was_left(self):
        fetch = self.fetcher(Upstream(*([RATE_LIMITED] * 6 + [LISTING])),
                             budget=100)
        self.dud_listings(fetch, 1)
        fetch.begin_lane(None)
        self.assertIsNone(fetch.channel_latest("UC-a"),
                          "three failures of its own are not five")
        self.assertEqual(3, fetch.fails)
        self.assertEqual(LISTING, fetch.channel_latest("UC-b"))

    def test_a_borrowed_failure_cannot_count_towards_the_outage_brake(self):
        """Otherwise three lanes losing other lanes' bad luck put the run on one strike."""
        fetch = self.fetcher(Upstream(*([RATE_LIMITED] * 40)), budget=200)
        for lane in range(4):
            fetch.begin_lane(None)
            self.dud_listings(fetch, 1)
        self.assertEqual(0, fetch.aborts)


class ADeadUpstreamGetsCheap(BudgetCase):
    """Clearing `fails` per lane must not cost the run its brake against a dead upstream.

    With the clear and nothing else, every lane paid a fresh run of five
    failures before giving up: 110 dead calls and 20 minutes of backoff over a
    night of 22 lanes, against 26 calls and 2 minutes before it. `aborts` counts
    the lanes that gave up with nothing answering in between, and past
    MAX_LANE_ABORTS a lane gives up on its first failure. Nothing is skipped and
    nothing is refused, so the first call that answers clears both counters and
    full patience comes back on its own.
    """

    def give_up_a_lane(self, fetch, ucids=("UC-a", "UC-b")):
        """One lane's worth of bad luck. Returns the abort, which must be the lane's own."""
        fetch.begin_lane(None)
        with self.assertRaises(self.mod.Aborted) as caught:
            for ucid in ucids:
                fetch.channel_latest(ucid)
        self.assertNotIsInstance(caught.exception, self.mod.BudgetSpent,
                                 "a refused fetch is not a lane giving up")
        return caught.exception

    def establish_the_outage(self, fetch):
        for lane in range(self.mod.MAX_LANE_ABORTS):
            self.give_up_a_lane(fetch)

    def dead(self, budget=10 ** 6):
        return self.fetcher(Upstream(*([RATE_LIMITED] * 400)), budget=budget)

    def test_a_lane_after_the_outage_is_established_gives_up_on_one_failure(self):
        fetch = self.dead()
        self.establish_the_outage(fetch)
        spent_before = fetch.fetches
        self.give_up_a_lane(fetch, ucids=("UC-c",))
        self.assertEqual(1, fetch.fetches - spent_before)

    def test_a_dead_night_costs_what_it_used_to_rather_than_four_times_it(self):
        fetch = self.dead()
        self.establish_the_outage(fetch)
        for lane in range(19):
            self.give_up_a_lane(fetch, ucids=("UC-c",))
        self.assertEqual(
            self.mod.MAX_LANE_ABORTS * self.mod.MAX_CONSECUTIVE_FAILS + 19,
            fetch.fetches,
            "five calls each to establish the outage, then one call a lane")

    def test_it_says_upstream_is_the_problem_rather_than_the_lane(self):
        fetch = self.dead()
        self.establish_the_outage(fetch)
        self.assertIn("upstream is the problem",
                      str(self.give_up_a_lane(fetch, ucids=("UC-c",))))

    def test_no_lane_is_ever_refused_a_fetch_over_this(self):
        """The brake makes lanes cheap, not skipped: a starved lane still rebuilds from cache."""
        fetch = self.dead()
        self.establish_the_outage(fetch)
        for lane in range(5):
            self.give_up_a_lane(fetch, ucids=("UC-c",))
        self.assertLess(fetch.fetches, fetch.budget)

    def test_a_lane_that_answers_gives_the_run_its_patience_back(self):
        answers = ([RATE_LIMITED] * (self.mod.MAX_CONSECUTIVE_FAILS
                                     * self.mod.MAX_LANE_ABORTS)
                   + [LISTING] + [RATE_LIMITED] * 20)
        fetch = self.fetcher(Upstream(*answers), budget=10 ** 6)
        self.establish_the_outage(fetch)
        fetch.begin_lane(None)
        self.assertEqual(LISTING, fetch.channel_latest("UC-ok"))
        self.assertEqual(0, fetch.aborts)
        spent_before = fetch.fetches
        self.give_up_a_lane(fetch)
        self.assertEqual(self.mod.MAX_CONSECUTIVE_FAILS,
                         fetch.fetches - spent_before,
                         "one answer and a lane gets its five failures back")

    def test_nothing_is_braked_while_upstream_is_answering(self):
        fetch = self.fetcher(Upstream(LISTING), budget=10)
        fetch.channel_latest("UC-a")
        self.assertEqual(0, fetch.aborts)


if __name__ == "__main__":
    unittest.main()

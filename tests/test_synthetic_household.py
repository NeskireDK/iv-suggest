"""The engine driven over a whole synthetic household, against a real database.

Every multi-account path was asserted only against hand built dicts before
this: the live instance has one account with thousands of watched videos and
one with three, so nobody had seen the engine fill lanes for several accounts
with histories of different shapes. This runs `iv-suggest init`, then several
nights of `iv-suggest run`, over seven generated accounts -- heavy, medium,
cold, one either side of the `min_watched` gate, one sharing nearly every
channel with the heavy account and one sharing none -- and then asks the
questions a unit test cannot: whose rows moved, what a lane cost, and what a
newly enrolled account changed.

Nothing here touches an Invidious instance and nothing is copied off one; see
tests/synthetic.py. Skipped, with a reason, when docker is unavailable.
"""

import hashlib
import unittest
import urllib.error

from support import load
from synthetic import (ALICE, BOB, COLD, FAST_RATE, HOUSEHOLD, JUST_OVER,
                       LANES_FILE, LONER, MIN_WATCHED, NEWCOMER, THIN, TWIN,
                       UNREACHABLE_OTHER_VID, UNREACHABLE_VID, Args, Instance,
                       require_docker)

HARNESS = {}
HISTORY_GATED = ("suggested", "subs-top48")
UNGATED = "household"


def setUpModule():
    require_docker()
    engine = load(IV_SUGGEST_ACCOUNT=ALICE.email, IV_SUGGEST_CONFIG=LANES_FILE)
    instance = Instance(engine).start()
    HARNESS.update(engine=engine, instance=instance)
    try:
        run_the_household(engine, instance)
    except BaseException:
        instance.stop()
        raise


def tearDownModule():
    if "instance" in HARNESS:
        HARNESS["instance"].stop()


def run_the_household(engine, instance):
    """Everything that writes, once, so every test below is a read."""
    for account in HOUSEHOLD:
        instance.enrol(account)
    engine.cmd_init(Args(all_users=True))
    instance.night()
    instance.night()
    HARNESS["before_one_account_ran"] = instance.snapshot()
    instance.night(account=ALICE.email)
    HARNESS["after_one_account_ran"] = instance.snapshot()
    HARNESS["config_before_enrolment"] = config_digest()
    enrol_a_newcomer(engine, instance)
    block_a_channel_for_one_account(engine, instance)
    HARNESS["refusals_the_engine_caused"] = list(instance.api.refused)


def enrol_a_newcomer(engine, instance):
    """A ninth account appears on the instance and nothing else changes."""
    instance.enrol(NEWCOMER)
    engine.cmd_init(Args(all_users=True))
    instance.night()
    instance.night()
    HARNESS["config_after_enrolment"] = config_digest()


def block_a_channel_for_one_account(engine, instance):
    """One account puts a channel it is shown into its own Blocked playlist."""
    vid = instance.lane_videos(BOB.email, "suggested")[0][0]
    HARNESS["blocked_ucid"] = instance.block(BOB, vid)
    instance.night()


def config_digest():
    with open(LANES_FILE, "rb") as fh:
        return hashlib.md5(fh.read()).hexdigest()


class Harnessed(unittest.TestCase):
    """Reads the one household every test in this file shares."""

    def setUp(self):
        self.engine = HARNESS["engine"]
        self.instance = HARNESS["instance"]

    def lanes_of(self, account):
        return self.instance.lanes_of(account.email)

    def videos_in(self, account, lane_id):
        return self.instance.lane_videos(account.email, lane_id)

    def count(self, sql):
        return int(self.instance.db.value(sql) or 0)

    def plid_of(self, account, lane_id):
        return self.instance.db.value(
            "SELECT plid FROM suggest.lanes WHERE account=%s AND lane=%s;"
            % (self.engine.lit(account.email), self.engine.lit(lane_id)))


class LaneEntitlement(Harnessed):
    """Which lanes each history shape actually got."""

    def test_the_boundary_accounts_differ_by_nothing_but_the_gate(self):
        """One watched video apart, so a different lane list is the gate itself."""
        self.assertEqual(MIN_WATCHED - 1, len(THIN.watched))
        self.assertEqual(MIN_WATCHED, len(JUST_OVER.watched))
        self.assertEqual(len(THIN.subscriptions), len(JUST_OVER.subscriptions))
        self.assertEqual([UNGATED], self.lanes_of(THIN))
        self.assertEqual(sorted(HISTORY_GATED + (UNGATED,)),
                         self.lanes_of(JUST_OVER))

    def test_every_account_with_history_gets_every_lane(self):
        for account in (ALICE, BOB, TWIN, LONER):
            self.assertEqual(sorted(HISTORY_GATED + (UNGATED,)),
                             self.lanes_of(account), account.name)

    def test_a_held_back_lane_has_no_playlist_anywhere_not_an_empty_one(self):
        """An empty playlist is worse than no playlist: it looks like a fault."""
        for account in (THIN, COLD):
            for lane_id in HISTORY_GATED:
                self.assertEqual("", self.plid_of(account, lane_id))
            self.assertEqual(
                0, self.count("SELECT count(*) FROM playlists WHERE author=%s "
                              "AND title IN ('Suggested',"
                              "'Subscriptions: biggest 48h');"
                              % self.engine.lit(account.email)))

    def test_the_run_names_the_lanes_it_held_back(self):
        held = "held back until there is more history: %s" % ", ".join(HISTORY_GATED)
        self.assertTrue(any(held in line for line in self.instance.log))

    def test_the_lanes_the_gate_allowed_are_full(self):
        for account in (ALICE, BOB, TWIN, LONER):
            self.assertEqual(8, len(self.videos_in(account, "suggested")),
                             account.name)
            self.assertEqual(5, len(self.videos_in(account, "subs-top48")),
                             account.name)


class ColdAccount(Harnessed):
    """An account with no history and no subscriptions."""

    def test_it_gets_no_history_driven_lane_at_all(self):
        self.assertEqual([UNGATED], self.lanes_of(COLD))

    def test_it_tracks_no_state_of_its_own(self):
        self.assertEqual(0, self.count(
            "SELECT count(*) FROM suggest.items WHERE account=%s;"
            % self.engine.lit(COLD.email)))

    def test_the_one_lane_it_does_get_is_full_of_other_people_s_videos(self):
        """The point of the household lane: it carries a new account meanwhile."""
        self.assertEqual(12, len(self.videos_in(COLD, UNGATED)))

    def test_no_lane_of_it_reports_never_having_run(self):
        """A 0 timestamp is not a small one; twelve of them paged somebody."""
        stamps = self.metric_values("lane_last_run_timestamp_seconds", COLD)
        self.assertTrue(stamps)
        self.assertEqual([], [v for v in stamps if v == 0])

    def test_a_held_back_lane_is_counted_and_not_exported_as_a_lane(self):
        self.assertEqual([2], self.metric_values("lanes_held_back", COLD))
        for lane_id in HISTORY_GATED:
            self.assertEqual([], [line for line in self.lines_for(COLD)
                                  if 'lane="%s"' % lane_id in line])

    def lines_for(self, account):
        return [line for line in HARNESS.setdefault(
            "metrics", self.instance.metrics())
            if 'account="%s"' % account.email in line]

    def metric_values(self, name, account):
        wanted = "iv_suggest_%s{" % name
        return [int(float(line.rsplit(" ", 1)[1]))
                for line in self.lines_for(account) if line.startswith(wanted)]


class Isolation(Harnessed):
    """Whether one account's fill can reach another account's rows."""

    def test_filling_one_account_leaves_every_other_account_identical(self):
        before = HARNESS["before_one_account_ran"]
        after = HARNESS["after_one_account_ran"]
        self.assertIn(ALICE.email, before)
        for email in sorted(set(before) | set(after)):
            if email == ALICE.email:
                continue
            self.assertEqual(before.get(email), after.get(email), email)

    def test_two_accounts_hold_the_same_video_in_the_same_lane(self):
        """Two of the accounts seed from the same history tail on purpose.

        Cross-account eviction would show up as this count never exceeding 1.
        """
        self.assertLess(1, self.count(
            "SELECT coalesce(max(n),0) FROM (SELECT count(*) AS n FROM "
            "suggest.items GROUP BY lane, vid) c;"))

    def test_a_blocklist_belongs_to_one_account_not_the_household(self):
        ucid = HARNESS["blocked_ucid"]
        self.engine.use_account(BOB.email)
        self.assertIn(ucid, self.engine.read_blocked())
        self.engine.use_account(ALICE.email)
        self.assertNotIn(ucid, self.engine.read_blocked())

    def test_the_blocked_channel_left_only_that_account_s_lanes(self):
        ucid = HARNESS["blocked_ucid"]
        for lane_id in HISTORY_GATED + (UNGATED,):
            self.assertEqual([], [v for v, u in self.videos_in(BOB, lane_id)
                                  if u == ucid], lane_id)
        self.assertLess(0, self.count(
            "SELECT count(*) FROM playlist_videos pv "
            "JOIN suggest.lanes l ON l.plid = pv.plid "
            "WHERE l.account <> %s AND pv.ucid = %s;"
            % (self.engine.lit(BOB.email), self.engine.lit(ucid))))

    def test_the_engine_never_asked_for_another_account_s_playlist(self):
        """It cannot: the stub answers 403, as Invidious does."""
        self.assertEqual([], HARNESS["refusals_the_engine_caused"])
        self.engine.use_account(ALICE.email)
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.engine.api("GET", "/api/v1/auth/playlists/"
                            + self.plid_of(BOB, "suggested"))
        self.assertEqual(403, caught.exception.code)


class SharedCaches(Harnessed):
    """video_meta and video_dead are deliberately global; that is the economy."""

    def test_the_cache_is_loaded_whole_for_whichever_account_is_being_filled(self):
        self.engine.use_account(ALICE.email)
        alice_sees = set(self.fetcher().meta)
        self.engine.use_account(LONER.email)
        self.assertTrue(alice_sees)
        self.assertLessEqual(alice_sees, set(self.fetcher().meta))

    def test_a_video_one_account_fetched_costs_another_account_nothing(self):
        self.engine.use_account(ALICE.email)
        self.assertTrue(self.fetcher().video(UNREACHABLE_OTHER_VID))
        self.engine.use_account(TWIN.email)
        twins = self.fetcher()
        mark = self.instance.api.since()
        self.assertTrue(twins.meta_of(UNREACHABLE_OTHER_VID))
        self.assertEqual((0, 0), self.instance.api.spent(mark))
        self.assertEqual(1, twins.cache_hits)

    def test_a_row_filled_in_from_a_listing_is_still_a_genre_miss(self):
        """Otherwise a genre lane rejects the video for ever, for everybody."""
        listed = self.instance.db.value(
            "SELECT vid FROM suggest.video_meta WHERE NOT genre_known "
            "ORDER BY vid LIMIT 1;")
        self.assertTrue(listed)
        self.engine.use_account(TWIN.email)
        mark = self.instance.api.since()
        self.fetcher().meta_of(listed)
        self.assertEqual(1, self.instance.api.spent(mark)[1])

    def test_a_video_one_account_buried_is_buried_for_every_account(self):
        self.engine.use_account(BOB.email)
        self.fetcher().bury(UNREACHABLE_VID, 30, "synthetic")
        self.engine.use_account(LONER.email)
        theirs = self.fetcher()
        self.assertIn(UNREACHABLE_VID, theirs.dead)
        mark = self.instance.api.since()
        self.assertIsNone(theirs.video(UNREACHABLE_VID))
        self.assertEqual((0, 0), self.instance.api.spent(mark))

    def fetcher(self):
        """A Fetcher that has just reloaded both caches out of the database."""
        return self.engine.Fetcher(rate_per_min=FAST_RATE, budget=20)


class MixCost(Harnessed):
    """What a mix lane costs. It is supposed to cost nothing but SQL."""

    def setUp(self):
        super().setUp()
        self.engine.use_account(BOB.email)
        self.lane = dict((l["id"], l) for l in self.engine.load_config())[UNGATED]

    def rebuild(self):
        return self.engine.run_lane_mix(
            self.lane, set(BOB.watched), self.engine.read_blocked(), dry=False)

    def test_a_mix_costs_no_youtube_fetch(self):
        mark = self.instance.api.since()
        self.rebuild()
        self.assertEqual(0, self.instance.api.spent(mark)[1])

    def test_a_mix_writes_no_items_rows_for_any_account(self):
        """Its videos live in the source lanes; a row here would take them twice."""
        self.rebuild()
        self.assertEqual(0, self.count(
            "SELECT count(*) FROM suggest.items WHERE lane=%s;"
            % self.engine.lit(UNGATED)))

    def test_a_mix_reads_the_other_accounts_sources_over_sql(self):
        elsewhere = {self.plid_of(a, "suggested") for a in HOUSEHOLD
                     if a is not BOB and self.plid_of(a, "suggested")}
        mark = len(self.instance.api.calls)
        self.rebuild()
        asked = [path for _, path in self.instance.api.calls[mark:]]
        self.assertTrue(elsewhere)
        self.assertEqual([], [p for p in asked
                              if any(plid in p for plid in elsewhere)])
        self.assertEqual(12, len(self.videos_in(BOB, UNGATED)))


class Enrolment(Harnessed):
    """An account registered after the config was written."""

    def test_the_household_mix_took_it_in_with_no_config_change(self):
        self.assertEqual(HARNESS["config_before_enrolment"],
                         HARNESS["config_after_enrolment"])
        self.engine.use_account(ALICE.email)
        sources = self.engine.mix_sources(
            dict((l["id"], l) for l in self.engine.load_config())[UNGATED]["mix"])
        self.assertIn(NEWCOMER.email, [s["user"] for s in sources])

    def test_its_videos_reached_the_other_accounts_feeds(self):
        theirs = {v for v, _ in self.videos_in(NEWCOMER, "suggested")}
        household = set()
        for account in HOUSEHOLD:
            household |= {v for v, _ in self.videos_in(account, UNGATED)}
        self.assertTrue(theirs)
        self.assertTrue(theirs & household)

    def test_it_got_its_own_gated_lanes_because_it_has_history(self):
        self.assertEqual(sorted(HISTORY_GATED + (UNGATED,)),
                         self.lanes_of(NEWCOMER))


if __name__ == "__main__":
    unittest.main()

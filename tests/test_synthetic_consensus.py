"""The public consensus feed, compiled over a real database from several mixes.

Ranking by agreement is meaningless with one account, so this is the only place
it can be watched happening: four generated accounts contribute a home-mix, two
of them share nearly every channel and two share none, and a fifth account owns
the compiled feed and contributes nothing. It runs the real `iv-suggest init`,
the real nightly `run` and the real hourly `shuffle` against a throwaway
PostgreSQL, then asks what a unit test cannot -- what the feed cost, whose rows
moved, and whether the videos the household agrees on actually come out on top.

Nothing here touches an Invidious instance; see tests/synthetic.py. Skipped,
with a reason, when docker is unavailable.
"""

import hashlib
import unittest

from support import load
from synthetic import (ALICE, BOB, BOT, CONSENSUS_LANES_FILE, CONTRIBUTORS,
                       LONER, NEWCOMER, TWIN, Args, Instance, require_docker)

HARNESS = {}
FEED_LANE = "public"
FEED_SIZE = 6
HOURS = 3


def setUpModule():
    require_docker()
    engine = load(IV_SUGGEST_ACCOUNT=BOT.email,
                  IV_SUGGEST_CONFIG=CONSENSUS_LANES_FILE)
    instance = Instance(engine).start()
    HARNESS.update(engine=engine, instance=instance)
    try:
        compile_the_public_feed(engine, instance)
    except BaseException:
        instance.stop()
        raise


def tearDownModule():
    if "instance" in HARNESS:
        HARNESS["instance"].stop()


def compile_the_public_feed(engine, instance):
    """Everything that writes, once, so every test below is a read."""
    for account in CONTRIBUTORS + (BOT,):
        instance.enrol(account)
    engine.cmd_init(Args(all_users=True))
    instance.night()
    instance.night()
    mark = instance.api.since()
    instance.night(account=BOT.email)
    HARNESS["spent_on_one_rebuild"] = instance.api.spent(mark)
    HARNESS["feed"] = feed_now(instance)
    redraw_the_feed_hourly(instance)
    rebuild_after_blocking_a_channel(instance)
    HARNESS["mixes"] = source_mixes(engine)
    HARNESS["weights"] = engine.consensus_weights(
        HARNESS["mixes"], engine.consensus_config({}))
    HARNESS["config_before_enrolment"] = config_digest()
    enrol_a_newcomer(engine, instance)


def feed_now(instance):
    return instance.lane_videos(BOT.email, FEED_LANE)


def source_mixes(engine):
    """The home-mixes the engine itself reads, straight off the database."""
    return engine.consensus_mixes(engine.consensus_config({}), {})


def redraw_the_feed_hourly(instance):
    """Three hours of the reorder timer, with nothing else running between them."""
    HARNESS["hourly"] = []
    for _ in range(HOURS):
        instance.hour()
        HARNESS["hourly"].append(feed_now(instance))


def rebuild_after_blocking_a_channel(instance):
    """One contributor blocks a channel the public feed is offering."""
    HARNESS["state_before_a_lone_rebuild"] = instance.snapshot()
    HARNESS["blocked_ucid"] = instance.block(ALICE, HARNESS["feed"][0][0])
    instance.night(account=BOT.email)
    HARNESS["state_after_a_lone_rebuild"] = instance.snapshot()
    HARNESS["feed_after_the_block"] = feed_now(instance)


def enrol_a_newcomer(engine, instance):
    """A fifth contributor appears on the instance and no config changes."""
    instance.enrol(NEWCOMER)
    engine.cmd_init(Args(all_users=True))
    instance.night()
    instance.night()
    HARNESS["mixes_after_enrolment"] = source_mixes(engine)
    HARNESS["config_after_enrolment"] = config_digest()


def config_digest():
    with open(CONSENSUS_LANES_FILE, "rb") as fh:
        return hashlib.md5(fh.read()).hexdigest()


class Harnessed(unittest.TestCase):
    """Reads the one compiled feed every test in this file shares."""

    def setUp(self):
        self.engine = HARNESS["engine"]
        self.instance = HARNESS["instance"]

    def vids(self, pairs):
        return [vid for vid, _ in pairs]

    def home_mix(self, account):
        return self.vids(self.instance.lane_videos(account.email, "home-mix"))

    def held_by(self, count, mixes=None):
        counts = self.engine.agreement_counts(mixes or HARNESS["mixes"])
        return {vid for vid, held in counts.items() if held == count}

    def value(self, sql):
        return self.instance.db.value(sql)


class TheFeed(Harnessed):
    """What the compiled playlist is, and what it cost."""

    def test_the_feed_filled_to_its_size(self):
        self.assertEqual(FEED_SIZE, len(HARNESS["feed"]))

    def test_a_rebuild_costs_no_youtube_fetch(self):
        """The property `mix` has: every source is already in the database."""
        calls, fetches = HARNESS["spent_on_one_rebuild"]
        self.assertEqual(0, fetches)
        self.assertGreater(calls, 0)

    def test_every_video_in_it_came_from_some_account_s_home_mix(self):
        pooled = set()
        for account in CONTRIBUTORS:
            pooled.update(self.home_mix(account))
        self.assertTrue(set(self.vids(HARNESS["feed"])) <= pooled)

    def test_the_playlist_is_public(self):
        self.assertEqual("Public", self.value(
            "SELECT privacy FROM playlists WHERE id=%s;"
            % self.engine.lit(self.plid())))

    def test_it_lives_on_the_bot_s_own_account(self):
        self.assertEqual(BOT.email, self.value(
            "SELECT author FROM playlists WHERE id=%s;"
            % self.engine.lit(self.plid())))
        self.assertEqual("1", self.value(
            "SELECT count(*) FROM suggest.lanes WHERE lane=%s;"
            % self.engine.lit(FEED_LANE)))

    def test_it_tracks_no_items_row_of_its_own(self):
        """A compiled lane writes none, so nothing counts its content as taken."""
        self.assertEqual("0", self.value(
            "SELECT count(*) FROM suggest.items WHERE account=%s;"
            % self.engine.lit(BOT.email)))

    def test_the_bot_contributes_nothing_and_is_not_a_contributor(self):
        self.assertEqual([FEED_LANE], self.instance.lanes_of(BOT.email))
        self.assertEqual([], self.home_mix(BOT))

    def test_reading_every_account_s_lane_never_writes_to_one(self):
        """Its sources are read over SQL, so no other account's session is needed."""
        before = HARNESS["state_before_a_lone_rebuild"]
        after = HARNESS["state_after_a_lone_rebuild"]
        for account in CONTRIBUTORS:
            self.assertEqual(before.get(account.email), after.get(account.email),
                             account.name)
        self.assertEqual([], self.instance.api.refused)

    def plid(self):
        return self.value("SELECT plid FROM suggest.lanes WHERE account=%s "
                          "AND lane=%s;" % (self.engine.lit(BOT.email),
                                            self.engine.lit(FEED_LANE)))


class Overlap(Harnessed):
    """The fixture's own spread, without which the ranking tests are vacuous."""

    def test_the_contributing_accounts_overlap_at_different_levels(self):
        alice, twin = set(self.home_mix(ALICE)), set(self.home_mix(TWIN))
        loner, bob = set(self.home_mix(LONER)), set(self.home_mix(BOB))
        self.assertTrue(alice & twin, "twin shares alice's channels and history")
        self.assertEqual(set(), alice & loner)
        self.assertEqual(set(), bob & loner)

    def test_every_contributor_supplied_a_mix_and_the_bot_did_not(self):
        self.assertEqual(len(CONTRIBUTORS), len(HARNESS["mixes"]))
        self.assertNotIn(BOT.email, [src["user"] for src in HARNESS["mixes"]])

    def test_some_videos_are_held_by_two_mixes_and_some_by_one(self):
        self.assertTrue(self.held_by(2))
        self.assertTrue(self.held_by(1))


class Ranking(Harnessed):
    """A video two mixes hold has to beat an equivalent video in one."""

    def test_agreement_outweighs_any_depth_a_single_mix_can_reach(self):
        weights = HARNESS["weights"]
        shared = min(weights[vid] for vid in self.held_by(2))
        alone = max(weights[vid] for vid in self.held_by(1))
        self.assertGreater(shared, alone)

    def test_the_agreed_on_videos_are_drawn_far_more_often(self):
        drawn = {}
        for _ in range(400):
            for vid in self.engine.consensus_draw(HARNESS["weights"], FEED_SIZE):
                drawn[vid] = drawn.get(vid, 0) + 1
        shared, alone = self.held_by(2), self.held_by(1)
        self.assertGreater(self.rate(drawn, shared), 2 * self.rate(drawn, alone))

    def test_a_video_only_one_mix_holds_is_unlikely_rather_than_excluded(self):
        reached = set()
        for _ in range(400):
            reached.update(self.engine.consensus_draw(HARNESS["weights"], FEED_SIZE))
        self.assertTrue(reached & self.held_by(1))

    def rate(self, drawn, group):
        return sum(drawn.get(vid, 0) for vid in group) / float(len(group))


class HourlyRedraw(Harnessed):
    """The reorder timer redraws the feed and may not change what is in it."""

    def test_the_membership_never_changes_between_nights(self):
        first = sorted(self.vids(HARNESS["feed"]))
        for hour in HARNESS["hourly"]:
            self.assertEqual(first, sorted(self.vids(hour)))

    def test_the_order_does_change(self):
        orders = [self.vids(HARNESS["feed"])] + [self.vids(h) for h in HARNESS["hourly"]]
        self.assertGreater(len({tuple(order) for order in orders}), 1)

    def test_the_redraw_is_recorded_as_a_reorder_of_the_bot_s_lane(self):
        self.assertEqual(str(HOURS), self.value(
            "SELECT count(*) FROM suggest.shuffles WHERE account=%s AND lane=%s "
            "AND coalesce(error,'') = '';"
            % (self.engine.lit(BOT.email), self.engine.lit(FEED_LANE))))


class Blocklists(Harnessed):
    """The union of the household's blocklists, since the feed has no viewer."""

    def test_a_channel_one_account_blocked_leaves_the_public_feed(self):
        self.assertTrue(HARNESS["blocked_ucid"])
        self.assertTrue(HARNESS["feed_after_the_block"])
        self.assertNotIn(HARNESS["blocked_ucid"],
                         [ucid for _, ucid in HARNESS["feed_after_the_block"]])

    def test_the_feed_stayed_full_after_the_block(self):
        self.assertEqual(FEED_SIZE, len(HARNESS["feed_after_the_block"]))


class Enrolment(Harnessed):
    """A member registered later joins the feed with no config change."""

    def test_the_newcomer_s_mix_became_a_source_on_its_own(self):
        self.assertIn(NEWCOMER.email,
                      [src["user"] for src in HARNESS["mixes_after_enrolment"]])
        self.assertEqual(len(CONTRIBUTORS) + 1,
                         len(HARNESS["mixes_after_enrolment"]))

    def test_nothing_in_the_config_changed_to_admit_them(self):
        self.assertEqual(HARNESS["config_before_enrolment"],
                         HARNESS["config_after_enrolment"])

    def test_the_run_never_reported_a_failed_lane(self):
        self.assertEqual([], [line for line in self.instance.log
                              if "ERROR" in line or "ABORTED" in line])


if __name__ == "__main__":
    unittest.main()

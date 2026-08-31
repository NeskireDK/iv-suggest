"""Auto-enrolment, the history gate, and the override merge that used to break.

Enrolling everybody is only safe if two things hold: a listed account still
gets what it was listed with, and an account with no history is not handed a
dozen playlists that come back empty.
"""

import os
import tempfile
import unittest

from support import load

ME = "andre@example.com"
OTHER = "sofie@example.com"
THIRD = "kim@example.com"

LANES = """
defaults:
  size: 30
  min_watched: 50
  shuffle: {fatigue: 0.95, jitter: 0.08}
lanes:
  - id: suggested
    title: Suggested
  - id: fresh-uploads
    title: Fresh
  - id: household
    title: Household
    policy: mix
    min_watched: 0
    mix:
      sources:
        - {users: all, lane: suggested, share: 1.0}
"""


class ConfigCase(unittest.TestCase):

    def loaded(self, text, account=ME, instance=()):
        fh = tempfile.NamedTemporaryFile("w", suffix=".yml", delete=False)
        fh.write(text)
        fh.close()
        self.addCleanup(os.unlink, fh.name)
        mod = load(IV_SUGGEST_CONFIG=fh.name, IV_SUGGEST_ACCOUNT=account)
        mod.instance_accounts = lambda: list(instance)
        return mod


class OverrideMerge(ConfigCase):

    def test_an_override_of_one_shuffle_key_keeps_the_others(self):
        """Replacing the block wholesale left rank_items without `fatigue`."""
        mod = self.loaded(LANES + """
users:
  - email: %s
    lanes: [suggested]
    overrides:
      suggested: {shuffle: {jitter: 0}}
""" % ME)
        user = mod.load_users()[0]
        lane = mod.lanes_for(user, mod.load_config())[0]
        self.assertEqual(0, lane["shuffle"]["jitter"])
        self.assertEqual(0.95, lane["shuffle"]["fatigue"])
        self.assertEqual(set(mod.SHUFFLE_DEFAULTS), set(lane["shuffle"]))

    def test_an_override_of_a_plain_key_still_replaces_it(self):
        mod = self.loaded(LANES + """
users:
  - email: %s
    lanes: [suggested]
    overrides:
      suggested: {size: 15}
""" % ME)
        user = mod.load_users()[0]
        self.assertEqual(15, mod.lanes_for(user, mod.load_config())[0]["size"])

    def test_an_override_does_not_leak_into_the_shared_lane(self):
        mod = self.loaded(LANES + """
users:
  - email: %s
    lanes: [suggested]
    overrides:
      suggested: {shuffle: {jitter: 0}}
  - email: %s
    lanes: [suggested]
""" % (ME, OTHER))
        lanes = mod.load_config()
        mine, theirs = [mod.lanes_for(u, lanes)[0] for u in mod.load_users()]
        self.assertEqual(0, mine["shuffle"]["jitter"])
        self.assertEqual(0.08, theirs["shuffle"]["jitter"])


class MergedBlocks(ConfigCase):
    """Which nested blocks a patch merges into, and which it replaces."""

    def patched(self, block, patch):
        mod = self.loaded(LANES, instance=(ME,))
        base = dict(mod.load_config()[0])
        base[block] = {"a": 1, "b": 2}
        return mod.merge_lane(base, {block: patch})[block]

    def test_a_partial_shuffle_block_keeps_the_other_keys(self):
        self.assertEqual({"a": 1, "b": 9}, self.patched("shuffle", {"b": 9}))

    def test_a_partial_subscription_block_keeps_the_other_keys(self):
        self.assertEqual({"a": 1, "b": 9}, self.patched("subscription", {"b": 9}))

    def test_a_partial_mix_block_keeps_the_sources(self):
        """`overrides: {home: {mix: {pure: 5}}}` used to drop mix.sources."""
        mod = self.loaded(LANES, instance=(ME,))
        household = [l for l in mod.load_config() if l["id"] == "household"][0]
        merged = mod.merge_lane(household, {"mix": {"pure": 5}})
        self.assertEqual(5, merged["mix"]["pure"])
        self.assertTrue(merged["mix"]["sources"])

    def test_a_seed_block_replaces_rather_than_merges(self):
        """CONFIG.md commits to this, so it is a claim and not an accident."""
        self.assertEqual({"b": 9}, self.patched("seed", {"b": 9}))


class OverrideInertKeys(ConfigCase):

    def overridden(self, policy, patch):
        text = ("lanes:\n  - id: played\n    title: Played\n    policy: %s\n"
                "users:\n  - email: %s\n    overrides:\n      played: %s\n"
                % (policy, ME, patch))
        mod = self.loaded(text, instance=(ME,))
        user = mod.load_users()[0]
        return mod.lanes_for(user, mod.load_config())[0]["ignored"]

    def test_an_override_setting_a_key_the_policy_never_reads_is_named(self):
        self.assertEqual(["ttl_days"],
                         self.overridden("last_played", "{ttl_days: 3}"))

    def test_an_override_setting_a_key_the_policy_reads_is_not_named(self):
        self.assertEqual([], self.overridden("last_played", "{played_decay: 0.5}"))


class AutoEnrol(ConfigCase):

    def test_no_block_still_means_only_the_listed_account(self):
        """The old promise: enrolment has to be asked for."""
        mod = self.loaded(LANES, instance=(ME, OTHER, THIRD))
        self.assertEqual([ME], [u["email"] for u in mod.load_users()])

    def test_every_account_on_the_instance_is_taken_in(self):
        mod = self.loaded(LANES + "auto_enrol: {}\n", instance=(ME, OTHER, THIRD))
        self.assertEqual(sorted([ME, OTHER, THIRD]),
                         sorted(u["email"] for u in mod.load_users()))

    def test_an_account_registered_later_is_picked_up(self):
        mod = self.loaded(LANES + "auto_enrol: {}\n", instance=(ME, OTHER))
        self.assertEqual(2, len(mod.load_users()))
        mod._CONFIG_CACHE.pop("users")
        mod.instance_accounts = lambda: [ME, OTHER, THIRD]
        self.assertIn(THIRD, [u["email"] for u in mod.load_users()])

    def test_a_listed_account_keeps_its_own_lanes(self):
        mod = self.loaded(LANES + """
auto_enrol: {lanes: [household]}
users:
  - email: %s
    lanes: all
""" % ME, instance=(ME, OTHER))
        got = {u["email"]: u["lanes"] for u in mod.load_users()}
        self.assertEqual("all", got[ME])
        self.assertEqual(["household"], got[OTHER])

    def test_a_listed_account_is_not_enrolled_twice(self):
        mod = self.loaded(LANES + """
auto_enrol: {}
users:
  - email: %s
""" % ME, instance=(ME, OTHER))
        self.assertEqual(sorted([ME, OTHER]), sorted(u["email"] for u in mod.load_users()))

    def test_a_bare_auto_enrol_key_still_enrols(self):
        """Writing the key at all is the opt-in; `auto_enrol:` parses as None."""
        mod = self.loaded(LANES + "auto_enrol:\n", instance=(ME, OTHER))
        self.assertEqual(sorted([ME, OTHER]),
                         sorted(u["email"] for u in mod.load_users()))

    def test_auto_enrol_true_enrols(self):
        mod = self.loaded(LANES + "auto_enrol: true\n", instance=(ME, OTHER))
        self.assertEqual(sorted([ME, OTHER]),
                         sorted(u["email"] for u in mod.load_users()))

    def test_exclude_keeps_an_account_out(self):
        mod = self.loaded(LANES + "auto_enrol: {exclude: [%s]}\n" % OTHER,
                          instance=(ME, OTHER, THIRD))
        self.assertNotIn(OTHER, [u["email"] for u in mod.load_users()])


class HistoryGate(ConfigCase):

    def test_a_new_account_gets_only_what_it_can_fill(self):
        mod = self.loaded(LANES + "auto_enrol: {}\n", instance=(ME, OTHER))
        user = mod.load_users()[0]
        self.assertEqual(["household"],
                         [l["id"] for l in mod.lanes_for(user, mod.load_config(), 3)])

    def test_history_opens_the_rest_with_no_state_to_flip(self):
        mod = self.loaded(LANES + "auto_enrol: {}\n", instance=(ME, OTHER))
        user = mod.load_users()[0]
        self.assertEqual(["suggested", "fresh-uploads", "household"],
                         [l["id"] for l in mod.lanes_for(user, mod.load_config(), 900)])

    def test_no_count_given_means_no_gate(self):
        """Callers reporting what is held back need the ungated list to subtract."""
        mod = self.loaded(LANES + "auto_enrol: {}\n", instance=(ME, OTHER))
        user = mod.load_users()[0]
        self.assertEqual(3, len(mod.lanes_for(user, mod.load_config())))


class EnrolledLanes(ConfigCase):
    """Every reporter has to see what the filler will run, not what is configured."""

    def gated(self, mod, watched):
        mod.watch_count = lambda email: watched
        return [l["id"] for l in
                mod.enrolled_lanes(mod.load_users()[0], mod.load_config())]

    def test_a_reporter_sees_only_the_lanes_the_filler_will_run(self):
        mod = self.loaded(LANES + "auto_enrol: {}\n", instance=(ME, OTHER))
        self.assertEqual(["household"], self.gated(mod, 3))

    def test_history_opens_the_rest_to_the_reporter_too(self):
        mod = self.loaded(LANES + "auto_enrol: {}\n", instance=(ME, OTHER))
        self.assertEqual(["suggested", "fresh-uploads", "household"],
                         self.gated(mod, 900))

    def test_a_named_lane_list_still_narrows_the_gated_result(self):
        listed = LANES + ("users:\n  - email: %s\n    lanes: [suggested, household]\n"
                          % ME)
        mod = self.loaded(listed, instance=(ME,))
        self.assertEqual(["suggested", "household"], self.gated(mod, 900))


class HouseholdSource(ConfigCase):

    def test_users_all_becomes_one_source_per_account(self):
        mod = self.loaded(LANES + "auto_enrol: {}\n", instance=(ME, OTHER, THIRD))
        got = mod.mix_sources({"sources": [{"users": "all", "lane": "suggested"}]})
        self.assertEqual(sorted([ME, OTHER, THIRD]), sorted(s["user"] for s in got))
        self.assertEqual(3, len({s["key"] for s in got}))

    def test_the_block_share_is_split_between_them(self):
        mod = self.loaded(LANES + "auto_enrol: {}\n", instance=(ME, OTHER))
        got = mod.mix_sources(
            {"sources": [{"users": "all", "lane": "suggested", "share": 0.6}]})
        self.assertEqual([0.3, 0.3], [s["share"] for s in got])

    def test_an_expanded_source_is_optional_and_a_named_one_is_not(self):
        """A member who has no such lane yet must not abort the whole mix."""
        mod = self.loaded(LANES + "auto_enrol: {}\n", instance=(ME, OTHER))
        expanded = mod.mix_sources({"sources": [{"users": "all", "lane": "suggested"}]})
        named = mod.mix_sources({"sources": [{"user": OTHER, "lane": "suggested"}]})
        self.assertTrue(all(s["optional"] for s in expanded))
        self.assertFalse(named[0]["optional"])



class InertKeys(ConfigCase):

    def test_a_key_the_policy_never_reads_is_named(self):
        mod = self.loaded("""
lanes:
  - id: played
    title: Played
    policy: last_played
    ttl_days: 0
    min_seconds: 0
""")
        self.assertEqual(["min_seconds", "ttl_days"],
                         mod.load_config()[0]["ignored"])

    def test_a_key_the_policy_does_read_is_not_named(self):
        mod = self.loaded("""
lanes:
  - id: played
    title: Played
    policy: last_played
    size: 100
    played_decay: 0.9
    seed: {genre: Music}
""")
        self.assertEqual([], mod.load_config()[0]["ignored"])

    def test_refill_reads_everything_so_nothing_is_inert(self):
        mod = self.loaded("""
lanes:
  - id: a
    title: A
    ttl_days: 3
    min_seconds: 0
    max_per_channel: 4
""")
        self.assertEqual([], mod.load_config()[0]["ignored"])

    def test_a_default_is_not_reported_only_what_the_lane_sets(self):
        """Otherwise every last_played lane would warn about the defaults block."""
        mod = self.loaded("""
defaults:
  ttl_days: 14
  min_seconds: 120
lanes:
  - id: played
    title: Played
    policy: last_played
""")
        self.assertEqual([], mod.load_config()[0]["ignored"])

    def test_the_warning_says_the_lane_and_the_policy(self):
        mod = self.loaded("""
lanes:
  - id: played
    title: Played
    policy: last_played
    ttl_days: 0
""")
        said = []
        mod.log = said.append
        mod.warn_ignored_keys(mod.load_config())
        self.assertIn("played", said[0])
        self.assertIn("last_played", said[0])
        self.assertIn("ttl_days", said[0])
if __name__ == "__main__":
    unittest.main()

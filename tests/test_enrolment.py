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
        """`iv-suggest status` and the config tests ask for the lanes as written."""
        mod = self.loaded(LANES + "auto_enrol: {}\n", instance=(ME, OTHER))
        user = mod.load_users()[0]
        self.assertEqual(3, len(mod.lanes_for(user, mod.load_config())))


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


if __name__ == "__main__":
    unittest.main()

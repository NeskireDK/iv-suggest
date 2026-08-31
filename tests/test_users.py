"""Who gets managed, which lanes they get, and how the budget is split.

The rule that matters most here is the one about not enrolling anybody: an
account absent from the users: block must be left alone, because finding a
dozen playlists the bot made in your account is a bad first impression.
"""

import os
import re
import tempfile
import unittest

from support import load

ME = "andre@example.com"
OTHER = "sofie@example.com"

LANES = """
defaults:
  size: 30
lanes:
  - id: suggested
    title: Suggested
  - id: music-discover
    title: Music
  - id: fresh-uploads
    title: Fresh
  - id: home
    title: Home
    policy: mix
"""


class ConfigCase(unittest.TestCase):

    def write(self, text):
        fh = tempfile.NamedTemporaryFile("w", suffix=".yml", delete=False)
        fh.write(text)
        fh.close()
        self.addCleanup(os.unlink, fh.name)
        return fh.name

    def loaded(self, text, account=ME):
        return load(IV_SUGGEST_CONFIG=self.write(text), IV_SUGGEST_ACCOUNT=account)


class Users(ConfigCase):

    def test_no_users_block_means_the_one_account_in_the_environment(self):
        """A config from before multi-user has to keep working unchanged."""
        mod = self.loaded(LANES)
        self.assertEqual([{"email": ME, "lanes": "all", "overrides": {}}],
                         mod.load_users())

    def test_an_account_not_listed_is_never_enrolled(self):
        mod = self.loaded(LANES + """
users:
  - email: %s
""" % ME)
        self.assertEqual([ME], [u["email"] for u in mod.load_users()])

    def test_an_entry_without_an_email_is_a_config_error(self):
        mod = self.loaded(LANES + """
users:
  - lanes: [suggested]
""")
        with self.assertRaises(SystemExit):
            mod.load_users()

    def test_lanes_all_gives_the_whole_library(self):
        mod = self.loaded(LANES + """
users:
  - email: %s
    lanes: all
""" % ME)
        user = mod.load_users()[0]
        self.assertEqual(["suggested", "music-discover", "fresh-uploads", "home"],
                         [l["id"] for l in mod.lanes_for(user, mod.load_config())])

    def test_a_short_list_gives_only_those_lanes(self):
        mod = self.loaded(LANES + """
users:
  - email: %s
    lanes: [suggested, fresh-uploads]
""" % OTHER)
        user = mod.load_users()[0]
        self.assertEqual(["suggested", "fresh-uploads"],
                         [l["id"] for l in mod.lanes_for(user, mod.load_config())])

    def test_the_order_is_the_config_s_not_the_user_s(self):
        """Mix lanes read the lanes before them, so lanes.yml decides."""
        mod = self.loaded(LANES + """
users:
  - email: %s
    lanes: [home, fresh-uploads, suggested]
""" % OTHER)
        user = mod.load_users()[0]
        self.assertEqual(["suggested", "fresh-uploads", "home"],
                         [l["id"] for l in mod.lanes_for(user, mod.load_config())])

    def test_a_lane_that_does_not_exist_is_reported_not_fatal(self):
        mod = self.loaded(LANES + """
users:
  - email: %s
    lanes: [suggested, typo-lane]
""" % OTHER)
        said = []
        mod.log = said.append
        user = mod.load_users()[0]
        self.assertEqual(["suggested"],
                         [l["id"] for l in mod.lanes_for(user, mod.load_config())])
        self.assertIn("typo-lane", " ".join(said))

    def test_an_override_applies_to_that_account_only(self):
        mod = self.loaded(LANES + """
users:
  - email: %s
    lanes: all
  - email: %s
    lanes: [fresh-uploads]
    overrides:
      fresh-uploads: {size: 15}
""" % (ME, OTHER))
        lanes = mod.load_config()
        mine, theirs = mod.load_users()
        self.assertEqual(15, mod.lanes_for(theirs, lanes)[0]["size"])
        self.assertEqual(
            30, [l for l in mod.lanes_for(mine, lanes)
                 if l["id"] == "fresh-uploads"][0]["size"],
            "an override must not leak into the shared lane definition")


class Runs:
    """A suggest.runs table that answers account_order the way Postgres would."""

    def __init__(self, rows):
        self.rows = rows

    def query(self, sql):
        rows = self.rows
        if "coalesce(error,'')=''" in re.sub(r"\s+", "", sql):
            rows = [r for r in rows if not r[2]]
        newest = {}
        for account, started, _ in rows:
            newest[account] = max(newest.get(account, 0.0), started)
        return [[account, str(started)] for account, started in newest.items()]


class EmptyLaneList(ConfigCase):

    def test_an_empty_lane_list_means_no_lanes_not_every_lane(self):
        """`lanes: []` is how somebody is managed but given nothing yet."""
        mod = self.loaded(LANES + ("users:\n  - email: %s\n    lanes: []\n" % ME))
        user = mod.load_users()[0]
        self.assertEqual([], mod.lanes_for(user, mod.load_config()))
        self.assertEqual([], mod.lanes_for(user, mod.load_config(), 900))


class Order(unittest.TestCase):
    """Least-recently-succeeded first, so the same person is not always last."""

    def setUp(self):
        self.mod = load(IV_SUGGEST_ACCOUNT=ME)

    def order(self, runs, users):
        self.mod.query = Runs(runs).query
        return [u["email"] for u in
                self.mod.account_order([{"email": e} for e in users])]

    def test_the_stalest_account_is_served_first(self):
        runs = [(ME, 200.0, ""), (OTHER, 100.0, "")]
        self.assertEqual([OTHER, ME], self.order(runs, [ME, OTHER]))

    def test_an_account_that_never_ran_leads(self):
        self.assertEqual([OTHER, ME], self.order([(ME, 200.0, "")], [ME, OTHER]))

    def test_a_failed_run_does_not_count_as_a_turn(self):
        runs = [(ME, 100.0, ""), (OTHER, 900.0, "budget spent")]
        self.assertEqual([OTHER, ME], self.order(runs, [ME, OTHER]))


class Budget(unittest.TestCase):
    """The fetch budget is divided between accounts, never multiplied."""

    def setUp(self):
        self.mod = load(IV_SUGGEST_ACCOUNT=ME)

    def test_two_accounts_halve_the_run_budget(self):
        self.assertEqual(160, self.mod.budget_slice(320, 0, 2))

    def test_what_the_first_account_leaves_rolls_forward(self):
        # 320 total, two accounts, the first used 10 of its 160.
        self.assertEqual(310, self.mod.budget_slice(320, 10, 1))

    def test_the_last_account_gets_everything_still_unspent(self):
        self.assertEqual(20, self.mod.budget_slice(320, 300, 1))

    def test_an_overspent_budget_gives_nothing_rather_than_a_negative(self):
        self.assertEqual(0, self.mod.budget_slice(320, 400, 2))

    def test_one_account_is_the_whole_budget(self):
        """The single-account install must be unchanged by any of this."""
        self.assertEqual(320, self.mod.budget_slice(320, 0, 1))


if __name__ == "__main__":
    unittest.main()

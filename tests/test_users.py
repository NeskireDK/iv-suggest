"""Who gets managed, which lanes they get, and how the budget is split.

The rule that matters most here is the one about not enrolling anybody: an
account absent from the users: block must be left alone, because finding a
dozen playlists the bot made in your account is a bad first impression.
"""

import os
import re
import sys
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

    def loaded(self, text, account=ME, instance=()):
        mod = load(IV_SUGGEST_CONFIG=self.write(text), IV_SUGGEST_ACCOUNT=account)
        mod.instance_accounts = lambda: list(instance)
        mod.watch_count = lambda email: 900
        return mod


class Users(ConfigCase):

    def test_no_users_block_means_the_one_account_in_the_environment(self):
        """A config from before multi-user has to keep working unchanged."""
        mod = self.loaded(LANES)
        self.assertEqual([{"email": ME, "named": True, "lanes": "all",
                           "overrides": {}}], mod.load_users())

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
        self.assertEqual([], said, "lanes_for is a reader, not a reporter")
        mod.warn_about_lanes_nobody_asked_for(mod.load_config())
        self.assertIn("typo-lane", " ".join(said))

    def test_a_typo_is_reported_once_however_many_times_the_lanes_are_read(self):
        """run_account reads them twice and the warnings once, so a log there triples."""
        mod = self.loaded(LANES + """
users:
  - email: %s
    lanes: [typo-lane]
""" % OTHER)
        said = []
        mod.log = said.append
        mod.warn_about_lanes_nobody_asked_for(mod.load_config())
        for _ in range(3):
            mod.lanes_for(mod.load_users()[0], mod.load_config())
        self.assertEqual(1, len(said), said)

    def test_a_typo_in_the_auto_enrol_list_is_reported_against_that_key(self):
        mod = self.loaded(LANES + "auto_enrol: {lanes: [suggested, typo-lane]}\n",
                          instance=(ME, OTHER))
        said = []
        mod.log = said.append
        mod.warn_about_lanes_nobody_asked_for(mod.load_config())
        self.assertEqual(1, len(said), said)
        self.assertIn("auto_enrol.lanes", said[0])

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


class Spent:
    """A fetcher with nothing left, so only the log line reads it."""

    budget = 320
    fetches = 320


class Args:

    def __init__(self, **over):
        self.dry_run = False
        self.lane = None
        self.__dict__.update(over)


class ASpentBudget(unittest.TestCase):
    """What a run still does for an account whose fetch budget is gone.

    It used to stop at the lane that overspent. The lanes after it that cost
    nothing -- a mix, and now the public feeds -- were skipped with it, so a
    heavy night left what visitors see un-rebuilt and nothing looked wrong.
    """

    LANES = [{"id": "suggested", "policy": "refill", "min_watched": 0},
             {"id": "fresh-uploads", "policy": "refill", "min_watched": 0},
             {"id": "popular", "policy": "consensus", "min_watched": 0},
             {"id": "home-mix", "policy": "mix", "min_watched": 0}]

    def setUp(self):
        self.mod = load(IV_SUGGEST_ACCOUNT=ME)
        self.mod.log = lambda line: None
        self.mod.load_users = lambda: [self.user()]
        self.mod.serve_account = lambda email: None
        self.mod.read_user = lambda: ([], set())
        self.mod.read_blocked = lambda: {}
        self.mod.watch_count = lambda email: 900
        self.mod.execute = lambda sql: self.written.append(sql)
        self.written = []
        self.ran = []
        self.mod.run_one_lane = self.run_one_lane

    def run_one_lane(self, lane, run):
        self.ran.append(lane["id"])
        if lane["id"] == "suggested":
            return (0, 0, 0, 0), "BudgetSpent('run budget 320 spent')"
        return (0, 1, 0, 0), None

    def user(self):
        return {"email": ME, "named": True, "lanes": "all", "overrides": {}}

    def fill(self):
        return self.mod.run_account(
            self.user(), self.LANES, Spent(), None, Args())

    def test_a_lane_that_would_spend_a_fetch_is_skipped(self):
        self.fill()
        self.assertNotIn("fresh-uploads", self.ran)

    def test_a_lane_compiled_from_other_lanes_still_runs(self):
        self.fill()
        self.assertEqual(["suggested", "popular", "home-mix"], self.ran)

    def test_only_the_lanes_that_ran_record_a_run_row(self):
        self.fill()
        rows = [sql for sql in self.written if "INSERT INTO suggest.runs" in sql]
        self.assertEqual(3, len(rows))

    def test_the_overspending_lane_is_still_counted_as_a_failure(self):
        self.assertEqual(1, self.fill())


class Size(ConfigCase):
    """`size` is what caps a lane, so a value that is not a number is a config error.

    A `size:` with nothing after it parses as None. The consensus draw sliced
    `[:None]`, which is the whole weighted union rather than a lane's worth, and
    the guard that refuses to publish an empty rebuild read None as "asked for
    empty" and stopped guarding. Both were reachable on a public playlist.
    """

    LANES = """
lanes:
  - id: popular
    title: Popular
    policy: consensus
    size:%s
"""

    def refused(self, size):
        mod = self.loaded(self.LANES % size)
        with self.assertRaises(SystemExit) as caught:
            mod.load_config()
        return str(caught.exception)

    def test_a_size_with_no_value_is_refused(self):
        self.assertIn("size must be a whole number", self.refused(""))

    def test_the_message_names_the_lane(self):
        self.assertIn("popular", self.refused(""))

    def test_a_negative_size_is_refused(self):
        self.assertIn("size must be a whole number", self.refused(" -1"))

    def test_a_size_that_is_not_a_number_is_refused(self):
        self.assertIn("size must be a whole number", self.refused(" lots"))

    def test_true_is_not_a_size_however_much_python_thinks_it_is_one(self):
        self.assertIn("size must be a whole number", self.refused(" true"))

    def test_a_lane_asked_to_hold_nothing_is_still_allowed(self):
        mod = self.loaded(self.LANES % " 0")
        self.assertEqual(0, mod.load_config()[0]["size"])

    def test_a_size_inherited_from_the_defaults_block_is_checked_too(self):
        mod = self.loaded("""
defaults:
  size:
lanes:
  - id: suggested
    title: Suggested
""")
        with self.assertRaises(SystemExit) as caught:
            mod.load_config()
        self.assertIn("size must be a whole number", str(caught.exception))

    def test_an_override_with_no_value_is_refused_at_config_load(self):
        mod = self.loaded("""
lanes:
  - id: suggested
    title: Suggested
    size: 30
users:
  - email: %s
    overrides:
      suggested:
        size:
""" % ME)
        with self.assertRaises(SystemExit) as caught:
            mod.load_users()
        message = str(caught.exception)
        self.assertIn("size must be a whole number", message)
        self.assertIn("suggested", message)
        self.assertIn(ME, message)


class OneBadAccount(unittest.TestCase):
    """A `users:` entry Invidious does not know must cost only itself.

    `open_session` used to sys.exit, so one typo'd email took down every
    account ordered after it, and the run said nothing about the ones it never
    reached.
    """

    LANES = [{"id": "suggested", "title": "Suggested", "policy": "refill",
              "size": 30, "min_watched": 0}]
    TYPO = "andr@example.com"

    def setUp(self):
        self.lines = []
        self.filled = []
        self.mod = load(IV_SUGGEST_ACCOUNT=ME)
        self.mod.log = self.lines.append
        self.mod.load_config = lambda: self.LANES
        self.mod.read_config = lambda: {"lanes": self.LANES}
        self.mod.load_users = lambda: [self.user(self.TYPO), self.user(ME)]
        self.mod.account_order = lambda users: users
        self.mod.session_of = lambda email: ""
        self.mod.one = lambda sql: "" if self.mod.lit(self.TYPO) in sql else "1"
        self.mod.query = lambda sql: []
        self.mod.execute = lambda sql: None
        self.mod.read_user = lambda: ([], set())
        self.mod.read_blocked = lambda: {}
        self.mod.watch_count = lambda email: 900
        self.mod.run_one_lane = self.run_one_lane

    def run_one_lane(self, lane, run):
        self.filled.append(self.mod.ACCOUNT)
        return (0, 1, 0, 0), None

    def user(self, email):
        return {"email": email, "named": True, "lanes": "all", "overrides": {}}

    def fill(self):
        return self.mod.cmd_run(Args(account=None, seeds=0, rate=600,
                                     budget=320))

    def test_the_accounts_after_the_bad_one_are_still_filled(self):
        self.fill()
        self.assertEqual([ME], self.filled)

    def test_the_run_names_the_account_it_could_not_serve(self):
        self.fill()
        named = [line for line in self.lines if self.TYPO in line]
        self.assertTrue(named, "the skipped account has to be named: %r" % self.lines)

    def test_the_run_still_reports_failure(self):
        self.assertEqual(1, self.fill())

    def test_a_command_without_a_per_account_handler_exits_rather_than_traces(self):
        """Only `run` recovers per account; main() turns the rest into a clean exit."""
        self.mod.accounts_wanted = lambda args: [self.user(self.TYPO)]
        argv = sys.argv
        sys.argv = ["iv-suggest", "dedupe"]
        self.addCleanup(setattr, sys, "argv", argv)
        with self.assertRaises(SystemExit) as caught:
            self.mod.main()
        self.assertIn(self.TYPO, str(caught.exception))


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

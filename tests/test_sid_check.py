"""Whether the bot's logins survived the nightly restart, and how it knows.

The question is settled by construction -- a session is a row in `session_ids`,
the nightly recreates only the `invidious` container, and `invidious-db` is
never restarted -- so this is a regression guard. What matters is that it cannot
report a survival it did not observe: a session issued AFTER the nightly has not
faced one, and calling that a pass would be a green check over no evidence.

It reads the finish time out of the database rather than asking systemd, which
is what lets it run in a container with no Docker socket.
"""

import unittest

from support import load

ME = "andre@example.com"
OTHER = "sofie@example.com"
NIGHTLY = "2026-09-04 05:02:11+02"
BEFORE = "2026-09-01 12:00:00+02"
AFTER = "2026-09-04 05:33:00+02"


class Recorded:
    """suggest.accounts joined to session_ids, as the check reads it."""

    def __init__(self, rows=(), nightly=NIGHTLY):
        self.rows = list(rows)
        self.nightly = nightly

    def install(self, mod):
        mod.one = self.one
        mod.query = self.query
        mod.execute = lambda sql: None
        return self

    def one(self, sql):
        return self.nightly if "suggest.host_events" in sql else ""

    def query(self, sql):
        if "suggest.accounts" not in sql:
            return []
        return [[account, fingerprint, "t" if joins else "f",
                 "t" if issued and issued > self.nightly else "f"]
                for account, fingerprint, joins, issued in self.rows]


class Args:
    pass


class SidCheck(unittest.TestCase):

    def setUp(self):
        self.said = []
        self.mod = load(IV_SUGGEST_ACCOUNT=ME)
        self.mod.log = self.said.append

    def check(self, rows=(), nightly=NIGHTLY):
        Recorded(rows, nightly).install(self.mod)
        return self.mod.cmd_sid_check(Args())

    def logged(self, text):
        return [line for line in self.said if text in line]

    def test_a_session_older_than_the_nightly_survived_it(self):
        self.assertEqual(0, self.check([(ME, "a1b2c3d4", True, BEFORE)]))
        self.assertTrue(self.logged("survived the nightly"))

    def test_a_sid_that_no_longer_resolves_is_a_failure(self):
        self.assertEqual(1, self.check([(ME, "a1b2c3d4", False, BEFORE)]))
        self.assertTrue(self.logged("LOST"))

    def test_it_names_the_account_that_lost_one_and_what_to_do(self):
        self.check([(ME, "a1b2c3d4", True, BEFORE),
                    (OTHER, "e5f6a7b8", False, BEFORE)])
        named = self.logged("did not survive")
        self.assertTrue(named)
        self.assertIn(OTHER, named[0])
        self.assertNotIn(ME, named[0])
        self.assertIn("iv-suggest init", named[0])

    def test_a_session_issued_after_the_nightly_is_untested_not_a_pass(self):
        """It resolves, but nothing has restarted since it was minted."""
        self.assertEqual(0, self.check([(ME, "a1b2c3d4", True, AFTER)]))
        self.assertTrue(self.logged("untested"))
        self.assertEqual([], self.logged("survived the nightly"))

    def test_an_untested_session_is_not_counted_as_lost(self):
        self.assertEqual(0, self.check([(ME, "a1b2c3d4", True, AFTER)]))
        self.assertEqual([], self.logged("did not survive"))

    def test_no_recorded_nightly_refuses_to_judge_at_all(self):
        """Before the host script writes the row, the honest answer is `I cannot tell`."""
        self.assertEqual(2, self.check([(ME, "a1b2c3d4", True, BEFORE)],
                                       nightly=""))
        self.assertTrue(self.logged("never recorded a nightly finish"))
        self.assertEqual([], self.logged("sid#"),
                         "it must not report on an account it cannot judge")

    def test_it_prints_a_fingerprint_and_never_the_sid(self):
        self.check([(ME, "a1b2c3d4", True, BEFORE)])
        printed = "\n".join(self.said)
        self.assertIn("sid#a1b2c3d4", printed)
        self.assertNotIn("SELECT", printed)

    def test_it_asks_postgres_for_the_fingerprint_rather_than_the_sid(self):
        """The sid must not cross the process boundary at all, not even to be hashed here."""
        rows = []
        self.mod.query = lambda sql: rows.append(sql) or []
        self.mod.one = lambda sql: NIGHTLY
        self.mod.cmd_sid_check(Args())
        joined = " ".join(rows)
        self.assertIn("left(md5(a.sid),8)", joined)
        self.assertNotIn("a.sid,", joined)

    def test_an_account_with_no_recorded_session_says_so(self):
        self.assertEqual(0, self.check([]))
        self.assertTrue(self.logged("no managed account has a recorded session"))


if __name__ == "__main__":
    unittest.main()

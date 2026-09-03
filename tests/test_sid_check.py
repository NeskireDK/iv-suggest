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
    """suggest.accounts joined to session_ids, as the check reads it.

    `table` false stands for the state before `init` has run, where the
    relation does not exist at all rather than holding no row.
    """

    def __init__(self, rows=(), nightly=NIGHTLY, hours_ago=2.0, table=True):
        self.rows = list(rows)
        self.nightly = nightly
        self.hours_ago = hours_ago
        self.table = table

    def install(self, mod):
        mod.one = self.one
        mod.query = self.query
        mod.execute = lambda sql: None
        return self

    def one(self, sql):
        if "to_regclass" in sql:
            return "t" if self.table else "f"
        return ""

    def query(self, sql):
        if "suggest.accounts" in sql:
            return [[account, fingerprint, "t" if joins else "f",
                     "t" if issued and issued > self.nightly else "f"]
                    for account, fingerprint, joins, issued in self.rows]
        if "suggest.host_events" in sql:
            return [[self.nightly, str(self.hours_ago)]] if self.nightly else []
        return []


class Args:
    pass


class SidCheck(unittest.TestCase):

    def setUp(self):
        self.said = []
        self.mod = load(IV_SUGGEST_ACCOUNT=ME)
        self.mod.log = self.said.append

    def check(self, rows=(), nightly=NIGHTLY, hours_ago=2.0, table=True):
        Recorded(rows, nightly, hours_ago, table).install(self.mod)
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
        """It is a hash so the log can tell one session from another without holding either."""
        asked = []

        def record(sql):
            asked.append(sql)
            if "suggest.accounts" in sql:
                return []
            return [[NIGHTLY, "2.0"]] if "host_events" in sql else []

        self.mod.query = record
        self.mod.one = lambda sql: "t"
        self.mod.cmd_sid_check(Args())
        joined = " ".join(asked)
        self.assertIn("left(md5(a.sid),8)", joined)
        self.assertNotIn("a.sid,", joined)

    def test_a_boundary_older_than_a_day_is_no_evidence_at_all(self):
        """Fail open, and every account passes while the check watches nothing.

        The row only updates if the nightly's last command succeeds. A nightly
        that has been failing for a week leaves last week's timestamp, every
        session predates it, and all of them read as survivors.
        """
        self.assertEqual(2, self.check([(ME, "a1b2c3d4", True, BEFORE)],
                                       hours_ago=170.0))
        self.assertTrue(self.logged("170 hours old"))
        self.assertEqual([], self.logged("sid#"))

    def test_a_boundary_from_last_night_is_evidence(self):
        self.assertEqual(0, self.check([(ME, "a1b2c3d4", True, BEFORE)],
                                       hours_ago=25.0))
        self.assertTrue(self.logged("survived the nightly"))

    def test_the_table_not_existing_yet_is_never_not_an_error(self):
        """Before `init` runs there is no relation, and psql would raise on the read."""
        self.assertEqual(2, self.check([(ME, "a1b2c3d4", True, BEFORE)],
                                       table=False))
        self.assertTrue(self.logged("never recorded a nightly finish"))

    def test_it_says_how_old_the_boundary_it_trusted_was(self):
        self.check([(ME, "a1b2c3d4", True, BEFORE)], hours_ago=3.0)
        self.assertTrue(self.logged("3 hours ago"))

    def test_an_account_with_no_recorded_session_says_so(self):
        self.assertEqual(0, self.check([]))
        self.assertTrue(self.logged("no managed account has a recorded session"))


if __name__ == "__main__":
    unittest.main()

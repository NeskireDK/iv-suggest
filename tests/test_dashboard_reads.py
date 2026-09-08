"""The grant that lets Grafana read the log tables, and the one it must not make.

The grant this replaces was run once by hand, before suggest.upstream_samples
existed. Two panels were written for that table and both answered `permission
denied` at every time range -- which reads as a broken datasource rather than a
missing grant, so it went a day unexplained. Applying it from `init` fixes the
timing; the list below is what stops the next table repeating it, because a
table in neither list fails this suite until somebody decides which it is.
"""

import re
import unittest

from support import load, source

ME = "andre@example.com"


def engine(**env):
    return load(IV_SUGGEST_ACCOUNT=ME, **env)


def tables_in(sql):
    """Every suggest table the schema creates, by name without the schema."""
    return set(re.findall(r"CREATE TABLE IF NOT EXISTS suggest\.(\w+)", sql))


class EveryTableIsDecidedAbout(unittest.TestCase):
    def setUp(self):
        self.mod = engine()

    def test_the_schema_and_the_two_lists_name_the_same_tables(self):
        listed = set(self.mod.READABLE_BY_DASHBOARD) | set(
            self.mod.PRIVATE_FROM_DASHBOARD)
        self.assertEqual(
            tables_in(self.mod.SCHEMA_SQL), listed,
            "a suggest table is in neither READABLE_BY_DASHBOARD nor "
            "PRIVATE_FROM_DASHBOARD: decide whether the dashboard may read it")

    def test_no_table_is_on_both_lists(self):
        self.assertEqual(set(), set(self.mod.READABLE_BY_DASHBOARD)
                         & set(self.mod.PRIVATE_FROM_DASHBOARD))

    def test_the_session_ids_stay_private(self):
        """suggest.accounts holds one live Invidious login per account."""
        self.assertIn("accounts", self.mod.PRIVATE_FROM_DASHBOARD)
        self.assertNotIn("accounts", self.mod.READABLE_BY_DASHBOARD)

    def test_the_table_the_panels_needed_is_readable(self):
        self.assertIn("upstream_samples", self.mod.READABLE_BY_DASHBOARD)


class WhatTheGrantDoes(unittest.TestCase):
    def granted(self, exists=True, **env):
        mod = engine(**env)
        written = []
        mod.execute = written.append
        mod.log = lambda msg: None
        mod.one = lambda sql: "t" if exists else "f"
        mod.grant_dashboard_reads()
        return mod, written

    def test_it_grants_select_on_the_readable_tables_and_revokes_the_rest(self):
        mod, written = self.granted()
        self.assertEqual(1, len(written))
        sql = written[0]
        self.assertIn("GRANT SELECT ON suggest.%I", sql)
        self.assertIn("REVOKE ALL ON suggest.%I", sql)
        for name in mod.READABLE_BY_DASHBOARD:
            self.assertIn("'%s'" % name, sql)

    def test_the_private_tables_are_never_named_as_readable(self):
        mod, written = self.granted()
        readable = re.search(r"ARRAY\[(.*?)\]", written[0], re.S).group(1)
        for name in mod.PRIVATE_FROM_DASHBOARD:
            self.assertNotIn("'%s'" % name, readable)

    def test_it_names_the_role_from_the_environment(self):
        _, written = self.granted(IV_SUGGEST_DASHBOARD_ROLE="dash_ro")
        self.assertIn("'dash_ro'", written[0])

    def test_a_database_with_no_dashboard_role_is_left_alone(self):
        """A test database and a fresh install have no Grafana; init must not fail."""
        _, written = self.granted(exists=False)
        self.assertEqual([], written)

    def test_it_refuses_to_regrant_the_account_it_connects_as(self):
        """Pointed at DB_USER the revoke sweep would strip the engine's own rights."""
        mod = engine()
        _, written = self.granted(IV_SUGGEST_DASHBOARD_ROLE=mod.DB_USER)
        self.assertEqual([], written)

    def test_an_empty_role_grants_nothing(self):
        _, written = self.granted(IV_SUGGEST_DASHBOARD_ROLE=" ")
        self.assertEqual([], written)


class WhenItRuns(unittest.TestCase):
    def test_init_grants_after_it_has_made_the_tables(self):
        """A grant before CREATE TABLE would name tables that do not exist yet."""
        text = source()
        body = text[text.index("def cmd_init("):]
        body = body[:body.index("\ndef ", 1)]
        self.assertLess(body.index("execute(SCHEMA_SQL)"),
                        body.index("grant_dashboard_reads()"))


if __name__ == "__main__":
    unittest.main()

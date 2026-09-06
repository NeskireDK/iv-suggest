"""Every query against an account-scoped table must name the account.

This is the failure the design called out first: miss one statement and two
people's lanes bleed into each other -- one person's Gaming pick suppresses
another's, or a sweep deletes rows that belong to someone else. It is not
something a run would report; the lanes would just quietly be wrong.

The check is static, over the SQL string literals in the source, because that
is the only form that catches a statement no test happens to execute.
"""

import ast
import re
import unittest

from support import SCRIPT, source

VERB_RE = re.compile(r"\b(SELECT|INSERT|UPDATE|DELETE|JOIN)\b")

SCOPED = ("suggest.lanes", "suggest.items", "suggest.cooldown",
          "suggest.runs", "suggest.shuffles", "suggest.fetches")

# Retention is instance-wide, so these go through prune() and lose every
# account's rows at once. That is right for a log and wrong for lane state.
PRUNABLE = ("suggest.plays", "suggest.fetches", "suggest.bot_touches")

# The two places a bare table name is right.
ALLOWED = (
    # DDL: the schema and the migration define the account column, so of course
    # they mention the tables without filtering on it.
    "CREATE TABLE",
    "ALTER TABLE",
    # read_blocked() excludes lane playlists by plid. Playlist ids are unique
    # across the whole instance, so this set is deliberately every account's:
    # scoping it would let one account's Blocked playlist eat another's lane.
    "AND p.id NOT IN (SELECT plid FROM suggest.lanes)",
    # The instance-wide metrics. The fetch budget, the metadata cache and the
    # timers are all shared, so these are meant to aggregate the whole
    # household -- and the alerts written against them keep working unchanged.
    # Listed one by one rather than by rule, so a new unscoped statement still
    # fails.
    "SELECT coalesce(extract(epoch FROM max(started))::bigint,0) FROM suggest.runs;",
    "SELECT coalesce(extract(epoch FROM max(ran))::bigint,0) FROM suggest.shuffles;",
    "SELECT coalesce(sum(fetches),0) FROM suggest.runs ",
    "SELECT coalesce(sum(cache_hits),0), ",
    # The call log's two household-wide rollups. Requests to YouTube are paced
    # and budgeted for the instance, not per person, so these count everybody --
    # `account` is a column on the table for the SQL that asks per person.
    "SELECT %s, count(*) FROM suggest.fetches WHERE %s GROUP BY 1;",
    "SELECT status, coalesce(error,''), count(*) FROM "
)


def sql_strings(path):
    """Every string constant in the file, with implicit concatenation joined.

    The statements are written as adjacent literals across several lines, so a
    per-literal check would see fragments; ast gives the joined value.
    """
    with open(path) as fh:
        tree = ast.parse(fh.read())
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            # A docstring naming a table in prose is not a statement.
            if VERB_RE.search(node.value):
                yield node.lineno, node.value


class AccountScope(unittest.TestCase):

    def test_every_scoped_statement_filters_on_account(self):
        offenders = []
        for lineno, text in sql_strings(SCRIPT):
            if not any(t in text for t in SCOPED):
                continue
            if any(a in text for a in ALLOWED):
                continue
            if "account" not in text:
                offenders.append("%s:%d %s" % (SCRIPT, lineno,
                                               " ".join(text.split())[:90]))
        self.assertEqual([], offenders,
                         "SQL touching a per-account table without an account "
                         "filter:\n" + "\n".join(offenders))

    def test_only_a_log_is_pruned_wholesale(self):
        """`prune` interpolates its table name, so the scan above cannot see
        it. Deleting every account's rows at once is right for a log and wrong
        for anything a lane is built from, so the tables are named here."""
        pruned = {call.args[0].value
                  for call in ast.walk(ast.parse(open(SCRIPT).read()))
                  if isinstance(call, ast.Call)
                  and isinstance(call.func, ast.Name)
                  and call.func.id == "prune"
                  and call.args and isinstance(call.args[0], ast.Constant)}
        self.assertTrue(pruned, "the scan found no prune call at all")
        self.assertEqual(set(), pruned - set(PRUNABLE),
                         "retention deletes every account's rows at once, so "
                         "only a log may go through prune()")

    def test_upsert_conflict_targets_include_account(self):
        """ON CONFLICT (lane,vid) would collide across accounts."""
        bad = [" ".join(t.split())
               for _, t in sql_strings(SCRIPT)
               if any(tbl in t for tbl in SCOPED) and "ON CONFLICT" in t
               and re.search(r"ON CONFLICT \((?!account)", t)]
        self.assertEqual([], bad)


if __name__ == "__main__":
    unittest.main()

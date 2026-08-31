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
          "suggest.runs", "suggest.shuffles")

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

    def test_upsert_conflict_targets_include_account(self):
        """ON CONFLICT (lane,vid) would collide across accounts."""
        bad = [" ".join(t.split())
               for _, t in sql_strings(SCRIPT)
               if any(tbl in t for tbl in SCOPED) and "ON CONFLICT" in t
               and re.search(r"ON CONFLICT \((?!account)", t)]
        self.assertEqual([], bad)


if __name__ == "__main__":
    unittest.main()

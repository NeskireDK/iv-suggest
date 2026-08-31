"""The schema migration, against a real PostgreSQL.

Step 1 of the migration order is the one that has to be invisible: the rows and
the playlists that exist today must survive being scoped by account, because a
lane that loses its plid gets recreated and the household loses a playlist.
That is not something a mock can answer, so this runs the DDL against a
throwaway postgres container -- synthetic rows, nothing from the real instance.

Skipped when docker is not available, so the rest of the suite still runs.
"""

import os
import subprocess
import unittest
import uuid

from support import load

IMAGE = os.environ.get("IV_SUGGEST_TEST_PG_IMAGE", "postgres:16-alpine")
ACCOUNT = "andre@example.com"
OTHER = "sofie@example.com"

# The schema exactly as it stood before this change, plus one row in every
# table, so the migration is exercised on the shape it will really meet.
LEGACY_SQL = """
CREATE SCHEMA IF NOT EXISTS suggest;
CREATE TABLE suggest.lanes (
  lane text PRIMARY KEY, plid text NOT NULL, title text,
  created timestamptz DEFAULT now());
CREATE TABLE suggest.items (
  lane text, vid text, added timestamptz DEFAULT now(), score real,
  song_key text, top_hours int DEFAULT 0, last_top timestamptz,
  PRIMARY KEY (lane, vid));
CREATE TABLE suggest.cooldown (
  lane text, vid text, until timestamptz, reason text,
  PRIMARY KEY (lane, vid));
CREATE TABLE suggest.runs (
  started timestamptz DEFAULT now(), lane text, added int, removed int,
  kept int, fetches int, error text);
CREATE TABLE suggest.video_meta (
  vid text PRIMARY KEY, genre text, seconds int, author_id text, title text,
  fetched timestamptz DEFAULT now(), author text, views bigint,
  genre_known boolean DEFAULT true);
CREATE TABLE suggest.video_dead (
  vid text PRIMARY KEY, until timestamptz, reason text,
  seen timestamptz DEFAULT now(), hits int DEFAULT 1);
CREATE TABLE suggest.shuffles (
  ran timestamptz DEFAULT now(), lane text, videos int, moved int, error text);
INSERT INTO suggest.lanes(lane,plid,title) VALUES ('suggested','IVPL_a','Suggested');
INSERT INTO suggest.items(lane,vid,score) VALUES ('suggested','aaaaaaaaaaa',1.5);
INSERT INTO suggest.cooldown(lane,vid,until,reason)
  VALUES ('suggested','bbbbbbbbbbb',now(),'watched');
INSERT INTO suggest.runs(lane,added,removed,kept,fetches)
  VALUES ('suggested',1,0,29,12);
INSERT INTO suggest.shuffles(lane,videos,moved) VALUES ('suggested',30,7);
INSERT INTO suggest.video_meta(vid,title) VALUES ('aaaaaaaaaaa','a video');
"""


def docker_works():
    try:
        return subprocess.run(["docker", "info"], capture_output=True,
                              timeout=30).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


@unittest.skipUnless(docker_works(), "docker is not available")
class Migration(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.name = "ivs-test-" + uuid.uuid4().hex[:8]
        run = subprocess.run(
            ["docker", "run", "-d", "--rm", "--name", cls.name,
             "-e", "POSTGRES_PASSWORD=test", "-e", "POSTGRES_DB=t", IMAGE],
            capture_output=True, text=True)
        if run.returncode:
            raise unittest.SkipTest("cannot start %s: %s" % (IMAGE, run.stderr))
        for _ in range(60):
            ready = subprocess.run(
                ["docker", "exec", cls.name, "pg_isready", "-U", "postgres",
                 "-d", "t"], capture_output=True)
            if ready.returncode == 0:
                break
            __import__("time").sleep(0.5)
        else:
            cls.tearDownClass()
            raise unittest.SkipTest("postgres never became ready")
        cls.mod = load(IV_SUGGEST_ACCOUNT=ACCOUNT)

    @classmethod
    def tearDownClass(cls):
        subprocess.run(["docker", "rm", "-f", cls.name], capture_output=True)

    def psql(self, sql, tuples_only=True):
        flags = ["-At"] if tuples_only else []
        r = subprocess.run(
            ["docker", "exec", "-i", self.name, "psql", "-U", "postgres",
             "-d", "t", "-v", "ON_ERROR_STOP=1"] + flags + ["-c", sql],
            capture_output=True, text=True)
        self.assertEqual(0, r.returncode, r.stderr)
        return [line for line in r.stdout.splitlines() if line]

    def migrate(self):
        mod = self.mod
        self.psql(mod.SCHEMA_SQL)
        self.psql(mod.MIGRATE_SQL.replace("__ACCOUNT__", mod.lit(ACCOUNT)))

    def setUp(self):
        self.psql("DROP SCHEMA IF EXISTS suggest CASCADE;")
        self.psql(LEGACY_SQL)

    def test_existing_rows_are_backfilled_not_dropped(self):
        self.migrate()
        self.assertEqual(["andre@example.com|suggested|IVPL_a"],
                         self.psql("SELECT account, lane, plid FROM suggest.lanes;"))
        for table in ("items", "cooldown", "runs", "shuffles"):
            self.assertEqual(
                ["1"],
                self.psql("SELECT count(*) FROM suggest.%s WHERE account=%s;"
                          % (table, self.mod.lit(ACCOUNT))),
                "%s lost its rows" % table)

    def test_primary_keys_lead_with_account(self):
        self.migrate()
        keys = dict(row.split("|", 1) for row in self.psql(
            "SELECT c.conrelid::regclass::text, pg_get_constraintdef(c.oid) "
            "FROM pg_constraint c WHERE c.contype='p' "
            "AND connamespace='suggest'::regnamespace;"))
        self.assertEqual("PRIMARY KEY (account, lane)", keys["suggest.lanes"])
        self.assertEqual("PRIMARY KEY (account, lane, vid)", keys["suggest.items"])
        self.assertEqual("PRIMARY KEY (account, lane, vid)", keys["suggest.cooldown"])

    def test_metadata_cache_stays_global(self):
        """Scoping it per account would multiply the YouTube fetch bill."""
        self.migrate()
        cols = self.psql(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema='suggest' AND table_name='video_meta';")
        self.assertNotIn("account", cols)

    def test_running_it_twice_changes_nothing(self):
        self.migrate()
        before = self.psql("SELECT account, lane, plid, created FROM suggest.lanes;")
        self.migrate()
        self.assertEqual(before,
                         self.psql("SELECT account, lane, plid, created FROM suggest.lanes;"))

    def test_two_accounts_can_hold_the_same_lane_name(self):
        """The point of the whole change: same lane id, different playlist."""
        self.migrate()
        self.psql(
            "INSERT INTO suggest.lanes(account,lane,plid,title) "
            "VALUES (%s,'suggested','IVPL_b','Suggested');" % self.mod.lit(OTHER))
        self.psql(
            "INSERT INTO suggest.items(account,lane,vid,score) "
            "VALUES (%s,'suggested','aaaaaaaaaaa',9.0);" % self.mod.lit(OTHER))
        self.assertEqual(["2"], self.psql("SELECT count(*) FROM suggest.lanes;"))
        self.assertEqual(["2"], self.psql(
            "SELECT count(*) FROM suggest.items WHERE vid='aaaaaaaaaaa';"))

    def test_a_fresh_database_needs_no_migration_step(self):
        self.psql("DROP SCHEMA suggest CASCADE;")
        self.migrate()
        self.assertEqual(["0"], self.psql("SELECT count(*) FROM suggest.lanes;"))


if __name__ == "__main__":
    unittest.main()

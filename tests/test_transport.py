"""How the engine reaches Postgres, and what must never appear on a command line.

The transport moved from `docker compose exec` to a direct connection, which
traded one privilege for another: the engine no longer needs to run Docker on
the host, and does need to know the Postgres password. So the password has to
travel in the environment. A command line is readable by every process on the
box, and the engine's own error path puts psql's stderr into a log line.

The synthetic suites replace `_psql` wholesale, so nothing else in the suite
ever sees the command it builds.
"""

import unittest

from support import load

PASSWORD = "synthetic-not-a-real-password"


class Ran:
    """Records the psql invocation in place of running it."""

    def __init__(self, stdout="", returncode=0, stderr=""):
        self.stdout, self.returncode, self.stderr = stdout, returncode, stderr
        self.cmd = None
        self.env = {}

    def run(self, cmd, capture_output=None, text=None, env=None):
        self.cmd, self.env = cmd, env or {}
        return self


def value_after(cmd, flag):
    """What psql is handed after `flag`, or None when the flag is not there at all.
    By flag rather than by position, so adding one does not move every assertion."""
    return cmd[cmd.index(flag) + 1] if flag in cmd else None


class Transport(unittest.TestCase):

    def engine(self, password=PASSWORD):
        mod = load(IV_SUGGEST_DB_HOST="db.example", IV_SUGGEST_DB_PORT="6543",
                   IV_SUGGEST_DB_USER="kemal", IV_SUGGEST_DB_NAME="invidious")
        mod._ENV["IV_SUGGEST_DB_PASSWORD"] = password
        return mod

    def ran(self, call, password=PASSWORD, **over):
        mod = self.engine(password=password)
        recorder = Ran(**over)
        mod.subprocess = type(
            "OneCall", (), {"run": staticmethod(recorder.run)})
        call(mod)
        return recorder

    def query(self, mod):
        return mod.query("SELECT 1;")

    def test_it_connects_to_the_configured_host_and_port(self):
        cmd = self.ran(self.query).cmd
        self.assertEqual("psql", cmd[0])
        self.assertEqual("db.example", value_after(cmd, "-h"))
        self.assertEqual("6543", value_after(cmd, "-p"))

    def test_it_names_the_role_and_the_database(self):
        cmd = self.ran(self.query).cmd
        self.assertEqual("kemal", value_after(cmd, "-U"))
        self.assertEqual("invidious", value_after(cmd, "-d"))

    def test_it_reaches_no_docker_at_all(self):
        """The point of the change: no Docker socket, no compose directory."""
        self.assertNotIn("docker", " ".join(self.ran(self.query).cmd))

    def test_the_password_is_never_on_the_command_line(self):
        self.assertNotIn(PASSWORD, " ".join(self.ran(self.query).cmd))

    def test_the_password_travels_in_the_environment(self):
        self.assertEqual(PASSWORD, self.ran(self.query).env.get("PGPASSWORD"))

    def test_no_password_configured_leaves_pgpassword_absent(self):
        """An empty PGPASSWORD is a different thing from an absent one to libpq."""
        self.assertNotIn("PGPASSWORD", self.ran(self.query, password="").env)

    def test_it_never_waits_for_a_password_prompt(self):
        """Without -w a missing password makes psql prompt, and a nightly run hangs for good."""
        self.assertIn("-w", self.ran(self.query).cmd)

    def test_it_stops_on_the_first_error_rather_than_carrying_on(self):
        self.assertEqual("ON_ERROR_STOP=1",
                         value_after(self.ran(self.query).cmd, "-v"))

    def test_it_reads_no_psqlrc(self):
        """A startup file is read AFTER --csv -t and can undo them.

        `\\timing on` echoes a line to stdout that becomes row 0, so `one()`
        returns the echo -- `session_of` would hand "Timing is on." to
        `open_session` as a session id. `\\pset null` is worse: it makes an
        unknown genre read as a real one, silently, with exit 0. The transport
        used to run inside the database container, where there was no home
        directory to read a file from.
        """
        self.assertIn("-X", self.ran(self.query).cmd)

    def test_a_read_asks_for_the_same_csv_the_parser_expects(self):
        """`query` splits psql's CSV, so changing these flags changes what it parses."""
        cmd = self.ran(self.query).cmd
        self.assertIn("--csv", cmd)
        self.assertIn("-t", cmd)

    def test_a_write_is_quiet_and_carries_the_sql_last(self):
        cmd = self.ran(lambda mod: mod.execute("DELETE FROM x;")).cmd
        self.assertIn("-q", cmd)
        self.assertEqual(["-c", "DELETE FROM x;"], cmd[-2:])

    def test_a_read_carries_the_sql_last_too(self):
        self.assertEqual(["-c", "SELECT 1;"], self.ran(self.query).cmd[-2:])

    def test_the_ambient_environment_cannot_steer_the_connection(self):
        """PGOPTIONS can set a search_path, and then unqualified reads return other rows.

        libpq reads a dozen PG* variables. Copying the whole environment meant
        anything that set one -- a shell, a unit, a compose file -- could point
        the engine at another schema, and it would answer with exit 0.
        """
        import os
        os.environ["PGDATABASE"] = "decoy"
        self.addCleanup(os.environ.pop, "PGDATABASE", None)
        env = self.ran(self.query).env
        self.assertNotIn("PGDATABASE", env)
        self.assertEqual("-c statement_timeout=300000", env.get("PGOPTIONS"),
                         "the engine sets this one itself, so nothing else can")

    def test_a_hung_database_cannot_hold_the_run_open_for_ever(self):
        env = self.ran(self.query).env
        self.assertEqual("10", env.get("PGCONNECT_TIMEOUT"))
        self.assertIn("statement_timeout", env.get("PGOPTIONS", ""))

    def test_only_the_first_line_of_a_complaint_is_kept(self):
        """The lines after it echo the statement, values and all.

        `open_session` interpolates a live session id into an INSERT. When that
        INSERT fails, psql's LINE echo carries the id, and `run_one_lane` puts
        the message in the log and in suggest.runs.error.
        """
        sid = "SYNTHETIC-SID-that-must-not-be-logged"
        with self.assertRaises(RuntimeError) as caught:
            self.ran(self.query, returncode=1, stderr=(
                "ERROR:  invalid input syntax for type integer\n"
                "LINE 1: INSERT INTO session_ids(id) VALUES ('%s')\n"
                "        ^\n" % sid))
        self.assertIn("invalid input syntax", str(caught.exception))
        self.assertNotIn(sid, str(caught.exception))

    def test_a_complaint_with_nothing_in_it_still_says_something(self):
        with self.assertRaises(RuntimeError) as caught:
            self.ran(self.query, returncode=1, stderr="")
        self.assertIn("said nothing", str(caught.exception))

    def test_a_failure_raises_with_what_psql_said(self):
        with self.assertRaises(RuntimeError) as caught:
            self.ran(self.query, returncode=2,
                     stderr="could not connect to server\n")
        self.assertIn("could not connect to server", str(caught.exception))
        self.assertNotIn(PASSWORD, str(caught.exception))


if __name__ == "__main__":
    unittest.main()

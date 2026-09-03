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
        self.assertEqual(["-h", "db.example", "-p", "6543"], cmd[1:5])

    def test_it_names_the_role_and_the_database(self):
        cmd = self.ran(self.query).cmd
        self.assertEqual(["-U", "kemal", "-d", "invidious"], cmd[5:9])

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
        self.assertEqual(["-v", "ON_ERROR_STOP=1"],
                         self.ran(self.query).cmd[10:12])

    def test_a_read_asks_for_the_same_csv_the_parser_expects(self):
        """`query` splits psql's CSV, so changing these flags changes what it parses."""
        cmd = self.ran(self.query).cmd
        self.assertEqual(["--csv", "-t"], cmd[12:14])

    def test_a_write_is_quiet_and_carries_the_sql_last(self):
        recorder = self.ran(lambda mod: mod.execute("DELETE FROM x;"))
        self.assertEqual(["-q", "-c", "DELETE FROM x;"], recorder.cmd[12:])

    def test_a_failure_raises_with_what_psql_said(self):
        with self.assertRaises(RuntimeError) as caught:
            self.ran(self.query, returncode=2,
                     stderr="could not connect to server\n")
        self.assertIn("could not connect to server", str(caught.exception))
        self.assertNotIn(PASSWORD, str(caught.exception))


if __name__ == "__main__":
    unittest.main()

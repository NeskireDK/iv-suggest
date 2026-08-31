"""The bot's own Invidious login, one per account.

The point of this over a signed token per person: there is nothing to store,
nothing to prompt for, and it works for any account with one INSERT. The risk
is the opposite one -- issuing a fresh login on every run, which would fill the
account's session list with the bot.
"""

import re
import unittest
import urllib.error

from support import load

ME = "andre@example.com"
OTHER = "sofie@example.com"


class Fake:
    """Stands in for the psql helpers, and records what was written."""

    def __init__(self, sessions=None, users=(ME, OTHER), owners=None):
        self.sessions = dict(sessions or {})
        self.users = set(users)
        self.owners = dict(owners or {})
        self.written = []

    def install(self, mod):
        mod.one = self.one
        mod.execute = self.execute
        mod.query = self.query
        mod.log = lambda msg: None
        return self

    def one(self, sql):
        joined = "s.email=a.account" in re.sub(r"\s+", "", sql)
        if "FROM suggest.accounts" in sql:
            for account, sid in self.sessions.items():
                if mod_lit(account) not in sql:
                    continue
                if joined and self.owners.get(sid, account) != account:
                    return ""
                return sid
            return ""
        if "FROM users WHERE email" in sql:
            return "1" if any(mod_lit(u) in sql for u in self.users) else ""
        return ""

    def execute(self, sql):
        self.written.append(sql)

    def query(self, sql):
        return []


def mod_lit(value):
    return "'" + str(value).replace("'", "''") + "'"


class Sessions(unittest.TestCase):

    def setUp(self):
        self.mod = load(IV_SUGGEST_ACCOUNT=ME)

    def test_an_existing_session_is_reused(self):
        fake = Fake(sessions={ME: "already-here"}).install(self.mod)
        self.assertEqual("already-here", self.mod.open_session(ME))
        self.assertEqual([], fake.written, "reusing a session must write nothing")

    def test_a_new_account_gets_one_login_in_both_tables(self):
        fake = Fake().install(self.mod)
        sid = self.mod.open_session(OTHER)
        self.assertEqual(1, len(fake.written), "one statement, one transaction")
        written = fake.written[0]
        self.assertIn("INSERT INTO session_ids", written)
        self.assertIn("INSERT INTO suggest.accounts", written)
        self.assertIn(mod_lit(sid), written)
        self.assertIn(mod_lit(OTHER), written)

    def test_the_session_id_is_32_random_bytes_and_not_a_token(self):
        import base64
        Fake().install(self.mod)
        sid = self.mod.open_session(OTHER)
        self.assertEqual(32, len(base64.urlsafe_b64decode(sid)))
        # Invidious rejects a SID cookie beginning "v1:" as a misplaced token.
        self.assertFalse(sid.startswith("v1:"))
        self.assertNotEqual(sid, self.mod.open_session(OTHER))

    def test_a_second_account_is_never_enrolled_by_accident(self):
        """An account Invidious does not know must not get a login."""
        Fake(users=(ME,)).install(self.mod)
        with self.assertRaises(SystemExit):
            self.mod.open_session("stranger@example.com")

    def test_a_signed_out_session_is_replaced_not_reused(self):
        """session_of() joins session_ids, so a wiped row reads as absent."""
        fake = Fake().install(self.mod)          # suggest.accounts row, no join
        sid = self.mod.open_session(ME)
        self.assertTrue(sid)
        self.assertIn("ON CONFLICT (account) DO UPDATE", fake.written[0],
                      "the stale row has to be overwritten, not duplicated")

    def test_a_session_row_owned_by_someone_else_is_not_reused(self):
        """Acting as one account with another's login would be the worst bug here."""
        fake = Fake(sessions={ME: "belongs-to-other"},
                    owners={"belongs-to-other": OTHER}).install(self.mod)
        self.assertEqual("", self.mod.session_of(ME))
        self.assertNotEqual("belongs-to-other", self.mod.open_session(ME))
        self.assertEqual(1, len(fake.written), "a fresh login has to be issued")


class Credentials(unittest.TestCase):
    """Which credential api() puts on the wire."""

    def setUp(self):
        self.mod = load(IV_SUGGEST_ACCOUNT=ME)
        self.sent = []

        class Resp:
            def read(self):
                return b"{}"

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def urlopen(req, data=None, timeout=None):
            self.sent.append(dict(req.header_items()))
            return Resp()

        self.mod.urllib.request.urlopen = urlopen

    def test_a_session_is_sent_as_the_sid_cookie(self):
        self.mod.SESSION = "the-session"
        self.mod.api("GET", "/api/v1/auth/playlists")
        self.assertEqual("SID=the-session", self.sent[0].get("Cookie"))

    def test_the_session_wins_over_a_leftover_token(self):
        self.mod.SESSION = "the-session"
        self.mod.TOKEN = "{}"
        self.mod.api("GET", "/api/v1/auth/playlists")
        self.assertNotIn("Authorization", self.sent[0])

    def test_the_old_token_still_works_while_it_is_the_only_credential(self):
        self.mod.SESSION = None
        self.mod.TOKEN = "{}"
        self.mod.api("GET", "/api/v1/auth/playlists")
        self.assertEqual("Bearer {}", self.sent[0].get("Authorization"))

    def test_the_two_pure_readers_need_no_credential(self):
        """metrics and shuffle read the database and call no API at all."""
        self.assertFalse(self.mod.needs_credential("metrics"))
        self.assertFalse(self.mod.needs_credential("shuffle"))
        for cmd in ("init", "run", "status", "dedupe", "views"):
            self.assertTrue(self.mod.needs_credential(cmd), cmd)

    def test_no_credential_names_the_account_it_is_missing_for(self):
        self.mod.SESSION = None
        self.mod.TOKEN = None
        self.mod.ACCOUNT = OTHER
        with self.assertRaises(self.mod.Aborted) as caught:
            self.mod.api("GET", "/api/v1/auth/playlists")
        self.assertIn(OTHER, str(caught.exception))


if __name__ == "__main__":
    unittest.main()

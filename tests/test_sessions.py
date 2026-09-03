"""The bot's own Invidious login, one per account.

The point of this over a signed token per person: there is nothing to store,
nothing to prompt for, and it works for any account with one INSERT. The risk
is the opposite one -- issuing a fresh login on every run, which would fill the
account's session list with the bot.

The worse risk, and what most of this file is about: a lost session must never
be papered over by some other account's credential. An account-agnostic bearer
token used to be reachable that way, so the commands that call the API mint
through `serve_account()`, and the pure readers still only `use_account()`.
"""

import ast
import re
import unittest
import urllib.error

from support import load, source

ME = "andre@example.com"
OTHER = "sofie@example.com"
LEFTOVER_TOKEN = "a-bearer-token-that-covers-one-account-only"


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


MINTED_RE = re.compile(r"INSERT INTO session_ids\(id,email,issued\) "
                       r"VALUES \('([^']+)','([^']+)'")


class Household:
    """Two accounts, their sessions in the database, and the wire api() reaches.

    Unlike Fake, a mint here lands in `sessions`, so a second open_session()
    sees the row the first one wrote.
    """

    def __init__(self, sessions=None, accounts=(ME, OTHER)):
        self.sessions = dict(sessions or {})
        self.accounts = list(accounts)
        self.minted = []
        self.requests = []

    def install(self, mod):
        self.mod = mod
        mod.one = self.one
        mod.execute = self.execute
        mod.query = self.query
        mod.log = lambda msg: None
        mod.accounts_wanted = lambda args: [{"email": e} for e in self.accounts]
        mod.urllib.request.urlopen = self.urlopen
        return self

    def _subject(self, sql):
        for account in self.accounts:
            if mod_lit(account) in sql:
                return account
        raise AssertionError("no account named in %r" % sql)

    def one(self, sql):
        if "FROM suggest.accounts" in sql:
            return self.sessions.get(self._subject(sql), "")
        if "FROM users WHERE email" in sql:
            return "1"
        return ""

    def execute(self, sql):
        found = MINTED_RE.search(sql)
        if not found:
            return
        sid, account = found.group(1), found.group(2)
        self.minted.append(account)
        self.sessions[account] = sid

    def query(self, sql):
        return [("gaming", plid_of(self._subject(sql)))]

    def urlopen(self, req, data=None, timeout=None):
        self.requests.append((req.full_url, dict(req.header_items())))
        return Reply()

    def credential_for(self, account):
        """The Cookie header sent on the call to that account's own playlist."""
        for url, headers in self.requests:
            if url.endswith(plid_of(account)):
                return headers.get("Cookie")
        raise AssertionError("no call for %s" % account)

    def authorizations(self):
        return [h.get("Authorization") for _, h in self.requests
                if h.get("Authorization")]


class Reply:
    def read(self):
        return b'{"videos": []}'

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class Args:
    account = None


def plid_of(account):
    return "IVPL" + account.split("@")[0]


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
        """An account Invidious does not know must not get a login.
        It raises rather than exits: the run loop skips that account and keeps going."""
        fake = Fake(users=(ME,)).install(self.mod)
        with self.assertRaises(self.mod.Aborted):
            self.mod.open_session("stranger@example.com")
        self.assertEqual([], fake.written)

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

    def test_opening_a_session_twice_reuses_the_row_it_wrote(self):
        household = Household().install(self.mod)
        first = self.mod.open_session(OTHER)
        self.assertEqual(first, self.mod.open_session(OTHER))
        self.assertEqual([OTHER], household.minted)


class ServingAnAccount(unittest.TestCase):
    """Whose credential goes on the wire when the loop moves between accounts."""

    def setUp(self):
        self.mod = load(IV_SUGGEST_ACCOUNT=ME)

    def test_a_lost_session_is_replaced_for_the_account_being_served(self):
        """Not borrowed from whoever a leftover bearer token happens to belong to."""
        household = Household(sessions={ME: "sid-for-me"}).install(self.mod)
        self.mod.TOKEN = LEFTOVER_TOKEN
        self.mod.videos_across_every_lane(Args())
        self.assertEqual([OTHER], household.minted)
        self.assertEqual("SID=sid-for-me", household.credential_for(ME))
        self.assertEqual("SID=" + household.sessions[OTHER],
                         household.credential_for(OTHER))
        self.assertNotEqual(household.credential_for(ME),
                            household.credential_for(OTHER))
        self.assertEqual([], household.authorizations())

    def test_serving_an_account_points_the_queries_at_it_too(self):
        household = Household().install(self.mod)
        self.mod.serve_account(OTHER)
        self.assertEqual(OTHER, self.mod.ACCOUNT)
        self.assertEqual(household.sessions[OTHER], self.mod.SESSION)

    def test_a_pure_reader_sees_the_lost_session_and_mints_nothing(self):
        household = Household(sessions={ME: "sid-for-me"}).install(self.mod)
        self.mod.use_account(OTHER)
        self.assertEqual(OTHER, self.mod.ACCOUNT)
        self.assertEqual("", self.mod.SESSION)
        self.assertEqual([], household.minted)
        self.assertEqual([], household.requests)


class Credentials(unittest.TestCase):
    """Which credential api() puts on the wire."""

    def setUp(self):
        self.mod = load(IV_SUGGEST_ACCOUNT=ME)
        self.sent = []

        def urlopen(req, data=None, timeout=None):
            self.sent.append(dict(req.header_items()))
            return Reply()

        self.mod.urllib.request.urlopen = urlopen

    def test_a_session_is_sent_as_the_sid_cookie(self):
        self.mod.SESSION = "the-session"
        self.mod.api("GET", "/api/v1/auth/playlists")
        self.assertEqual("SID=the-session", self.sent[0].get("Cookie"))

    def test_no_credential_names_the_account_it_is_missing_for(self):
        self.mod.SESSION = None
        self.mod.ACCOUNT = OTHER
        with self.assertRaises(self.mod.Aborted) as caught:
            self.mod.api("GET", "/api/v1/auth/playlists")
        self.assertIn(OTHER, str(caught.exception))
        self.assertIn("iv-suggest init", str(caught.exception))
        self.assertEqual([], self.sent)

    def test_a_leftover_bearer_token_is_not_a_credential(self):
        self.mod.SESSION = None
        self.mod.TOKEN = LEFTOVER_TOKEN
        with self.assertRaises(self.mod.Aborted):
            self.mod.api("GET", "/api/v1/auth/playlists")
        self.assertEqual([], self.sent)


def named_functions(text):
    return {node.name: node for node in ast.walk(ast.parse(text))
            if isinstance(node, ast.FunctionDef)}


def plain_calls(function):
    """The names this function calls, ignoring method and attribute calls."""
    return {node.func.id for node in ast.walk(function)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}


def callers_of(text, target):
    """Every function that reaches `target`, directly or through another function."""
    edges = {name: plain_calls(node)
             for name, node in named_functions(text).items()}
    reaching = set()
    while True:
        grown = {name for name, called in edges.items()
                 if target in called or called & reaching}
        if grown == reaching:
            return reaching
        reaching = grown


class WhoMints(unittest.TestCase):
    """The invariant statically, so a new command cannot reintroduce the hazard.

    Behavioural tests only cover the call sites somebody thought to drive; this
    covers the one added next year.
    """

    def setUp(self):
        self.source = source()

    def test_an_api_caller_never_sets_the_account_without_a_session(self):
        api_callers = callers_of(self.source, "api")
        for expected in ("run_account", "dedupe_account",
                         "videos_across_every_lane", "retire_item"):
            self.assertIn(expected, api_callers)
        self.assertEqual(set(),
                         api_callers & callers_of(self.source, "use_account"),
                         "a function that calls the API must serve the account, "
                         "not merely point at it")

    def test_the_pure_readers_mint_nothing(self):
        minters = callers_of(self.source, "open_session")
        self.assertIn("serve_account", minters)
        for reader in ("cmd_status", "cmd_metrics", "cmd_shuffle",
                       "cmd_sid_check", "recorded_sessions",
                       "shuffle_account", "status_account", "use_account"):
            self.assertNotIn(reader, minters)

    def test_the_engine_carries_no_bearer_token_path(self):
        self.assertNotIn("IV_SUGGEST_TOKEN", self.source)
        self.assertNotIn("Authorization", self.source)


if __name__ == "__main__":
    unittest.main()

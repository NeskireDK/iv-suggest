# Multi-user: why it is shaped this way

Status: **live and multi-account since 2026-08-31 18:16.** Auto-enrolment took
in every account on the instance, so the `users:` path, per-account session
minting and the cross-account mix source are all exercised nightly rather than
merely deployed. Two accounts run: one with a full history, one held back from
most lanes by `min_watched`.

Everything the earlier version of this note said was untested has since run in
anger. Session minting per account was re-proved on 2026-09-01, when every row
in `session_ids` was deleted and the engine re-minted for both accounts. The
cross-account mix source backs the `household` lane and now the `consensus`
policy as well.

How to configure it is in [CONFIG.md](CONFIG.md). This is the reasoning, the
findings that are not obvious from the code, and the decisions that were taken
rather than left open.

## It was plumbing, not algorithm work

`read_user()` already read one email's `users.watched` and `users.subscriptions`,
and everything downstream was already "this person's taste". Nothing in the
scoring is account-specific. The engine was always personal; it only had one
person.

## The bot mints its own credential

`helpers/handlers.cr` — `/api/v1/auth/*` accepts a bare `SID` cookie and grants
`scopes = [":*"]`. Full scope, no HMAC, no password. A SID is nothing but
`Base64.urlsafe_encode(Random::Secure.random_bytes(32))` in `session_ids`.

→ **one INSERT is a login for any account.** No new secret, no password prompt,
no fork change, no second copy of the signed token scheme.

These are real logins, so there is exactly one per account and it is reused.
The plan wanted them tagged `iv-suggest-<email>`, but `session_ids` has no
column for it (`id`, `email`, `issued` only); ownership is recorded in
`suggest.accounts`, which is also what enforces one per account.

## The shared cache is the whole economy

Every `suggest.*` table became account-scoped — `lanes`, `items` and `cooldown`
by primary key, `runs` and `shuffles` by column — **except `video_meta` and
`video_dead`, which stay global.** `video_meta` is the expensive thing: every
miss is a YouTube fetch, and overlapping taste makes the second account nearly
free. Scoping it per account would multiply the fetch bill by N for no gain.

**The one that would have bitten:** `dedupe_across_lanes` did `WHERE lane <> %s`.
Unscoped, one person's Gaming pick suppresses another's. Same for the `song_key`
dedupe. Both needed `AND account = %s`.

## Divide the fetch budget, do not multiply it

`MAX_FETCHES = 320` per run, `RATE_PER_MIN = 20`. The pacing exists to keep
YouTube calm, which is a property of the instance, not of the account: one
global run budget, a per-account slice, unspent slices rolling forward. Accounts
are served **least-recently-succeeded first**, so an exhausted budget does not
starve the same person every night. The shared mix costs nothing, so a light
account stays close to free.

One timer, one hourly shuffle, both looping internally. No per-account units.

## Decisions taken, not left open

- **Contributing to the shared mix is always on and not configurable.** A shared
  feed does publish what each person watches to everyone else in the household —
  understood and accepted; it is the household's own instance. An opt-out would
  only make the mix quietly incomplete and give it a second failure mode to
  debug.
- **Unlisted everywhere, new accounts and existing.** `public` bought nothing —
  see the README design note. The twelve lanes that already existed were flipped
  in place with the SQL below, and the fork needed a matching change so a feed
  could still be backed by an unlisted playlist.
- **A copy of the mix in each account, not one on a service account.** A mix
  lane is an ordinary lane an account opts into, whose sources happen to name
  other people. No service account, simpler to consume.
- **No cold-start ladder.** A new account's lanes start thin, and the engine
  needs watch depth rather than tuning. No history-depth gates, no forced
  subscription-only diet. The one concession is a log line: `run_account()`
  prints `account X: N watched`, so a thin lane has a visible cause.
- ~~**Never auto-enrol.**~~ **Reversed 2026-08-31.** The rule was written for a
  stranger finding a dozen unexplained playlists in their account; on a family
  instance its only effect was that the second account got nothing for three
  weeks. Every account is enrolled automatically, and the account with no
  history gets the compiled mix — both in [PUBLIC-FEED.md](PUBLIC-FEED.md).

## Verification still owed, after the fact

- **`playlist_of()` only PATCHes privacy at create time**, so changing a live
  lane's privacy is a database edit, not something a run repeats:

  ```sql
  UPDATE playlists SET privacy = 'Unlisted'
  WHERE id IN (SELECT plid FROM suggest.lanes) AND privacy <> 'Unlisted';
  ```

  Scoped to registered lanes on purpose — playlists the account made by hand are
  none of the bot's business. It is also why the code's `privacy` default only
  governs newly created playlists.
- **The first timer-driven full `run` has not happened on this code.** The
  2026-08-31 fill was a manual foreground run straight after the deploy; only the
  hourly shuffle has run from a timer since.
- **A session is minted per account by whoever calls the API.** There are two
  entry points and the difference is the whole safety property:

  | | sets `ACCOUNT` | session |
  |---|---|---|
  | `serve_account()` | yes | `open_session()` — reuses the row or mints one |
  | `use_account()` | yes | `session_of()` — `""` when the sid no longer joins `session_ids` |

  `init`, `run`, `views` and `dedupe` call the API, so they serve. `status`,
  `metrics` and `shuffle` read the database and call no API, so they only point,
  and reporting never writes a session row as a side effect. `api()` has no
  fallback: no session is `Aborted("no credential ... run iv-suggest init")`.

  This replaced `IV_SUGGEST_TOKEN`, one account-agnostic bearer token that
  `api()` fell back to when `SESSION` was empty. Because only `cmd_init` minted,
  a lost session did not stop a fill — it authenticated as the token's owner
  while the loop sat on a *different* account, and the playlist writes landed in
  the wrong account. Removing the token alone would only have converted that
  into an abort, which is why the minting came first. `tests/test_sessions.py`
  holds the invariant statically, so a new command cannot reintroduce it.

- **Does an issued SID survive the nightly 05:00 job?** The question was
  written assuming that job restores the database. It does not: `iv-nightly.sh`
  takes a `pg_dump --clean` *backup*, recomputes `IV_CHANNEL_REFRESH`, and
  force-recreates the `invidious` container only. `invidious-db` is never
  restarted and `session_ids` is never rewritten, so a recreate of the app
  container cannot invalidate a session that lives as a database row. Confirmed
  empirically rather than argued: `iv-sid-check.timer` on LXC 109 runs at 05:34,
  re-joins `suggest.accounts.sid` to `session_ids`, and compares `issued`
  against `iv-nightly.service`'s real exit timestamp. Result lands in
  `/var/lib/iv-suggest/sid-check.json`; a non-zero exit means a session was
  actually lost.

## Checks that passed before the deploy

- **Migration against a copy of the real database**, 2026-08-31. `pg_dump
  --schema=suggest` off LXC 109 restored into a throwaway postgres, migrated,
  then driven with the real `init`, `status` and `metrics` against the live
  `lanes.yml`. All 12 lanes kept their playlist ids, 2745 rows backfilled, and a
  second `init` opened no second session. Only the `suggest` schema was copied,
  so no watch history left the instance.
- **The SID cookie really authenticates**, 2026-08-31. One temporary session,
  `GET /api/v1/auth/playlists` with `Cookie: SID=` returned **200** and the real
  playlist list; the same call with no cookie returned **403**; the session was
  deleted and the count returned to what it was. The bot's 44-character urlsafe
  base64 value is accepted as is.
- The migration is also exercised on every test run against a throwaway postgres
  (`tests/test_migration.py`), and skips itself when docker is unavailable.

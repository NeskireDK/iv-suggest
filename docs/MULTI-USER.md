# Multi-user: why it is shaped this way

Status: **live since 2026-08-31 12:49, running single-account.** The schema, the
per-account session and the account scoping are deployed and in use. What is not
exercised is multi-account operation itself: with no `users:` block,
`load_users()` falls through to the single `IV_SUGGEST_ACCOUNT` path, so the
`users:` code, session minting for a *new* account and the cross-account mix
source have never run. Auto-enrolment, which changes that, is in
[PUBLIC-FEED.md](PUBLIC-FEED.md).

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
- **`IV_SUGGEST_TOKEN` is a reachable fallback that can write to the wrong
  account.** Not dead code, which is what an earlier note here claimed. `api()`
  is `if SESSION: ... elif TOKEN: ...`, and `SESSION` can legitimately be empty:
  `use_account()` sets it from `session_of()`, which returns `""` when the
  account's sid no longer joins `session_ids`. Only `cmd_init` calls
  `open_session()`. `run`, `views`, `dedupe`, `status` and `shuffle` all call
  `use_account()` alone. So a lost session does not stop a fill — it falls
  through to a single account-agnostic bearer token while the loop is positioned
  on some *other* account, and the writes land wherever that token's account is.
  Single-account, that was a harmless safety net; multi-user, it is a
  cross-account write.

  The fix is not just deleting the token. `run`, `views` and `dedupe` should
  mint through `open_session()` like `init` does, so a lost session self-heals
  for the *correct* account; the token fallback then becomes genuinely
  unreachable and can go with it, leaving `Aborted("no credential ... run
  iv-suggest init")` as the loud failure. Until then the token stays, because
  removing it alone converts a silent misfire into an abort without giving the
  fill any way to recover.

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

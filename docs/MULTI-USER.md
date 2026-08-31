# Multi-user: why it is shaped this way

Status: **built and merged, not yet deployed.** Written and implemented
2026-08-31. What remains is a `run --dry-run` against the real instance, which
needs the API and so cannot happen anywhere else.

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
no fork change, no second copy of the signed token scheme. `IV_SUGGEST_TOKEN`
survives only as the fallback for an install that has not run `init` since.

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
  see the README design note. The lanes that already existed were flipped in
  place with the SQL below.
- **A copy of the mix in each account, not one on a service account.** A mix
  lane is an ordinary lane an account opts into, whose sources happen to name
  other people. No service account, simpler to consume.
- **No cold-start ladder.** A new account's lanes start thin, and the engine
  needs watch depth rather than tuning. No history-depth gates, no forced
  subscription-only diet. The one concession is a log line: `run_account()`
  prints `account X: N watched`, so a thin lane has a visible cause. What to
  serve in the meantime is [PUBLIC-FEED.md](PUBLIC-FEED.md).

## Gotchas for the deploy

- **`playlist_of()` only PATCHes privacy at create time**, so changing a live
  lane's privacy is a database edit, not something a run repeats:

  ```sql
  UPDATE playlists SET privacy = 'Unlisted'
  WHERE id IN (SELECT plid FROM suggest.lanes) AND privacy <> 'Unlisted';
  ```

  Scoped to registered lanes on purpose — playlists the account made by hand are
  none of the bot's business.
- **No `users:` block is a valid deploy** and means exactly today's behaviour,
  so the first deploy can skip writing one and add the second account after.
- **Does an issued SID survive the nightly `pg_dump` restore?** `session_ids` is
  in the dump, so it should. Confirm rather than assume — this is the one thing
  that would silently log the bot out of every account at once.

## Pre-deploy checks that have passed

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

# TODO: multi-user iv-suggest

Status: **plan only, nothing implemented.** Written 2026-08-31.

One instance, one set of lanes, one account. This is the plan for turning that
into per-account lanes plus a shared feed, without multiplying the fetch bill.

## Where we are

- **One account, hardcoded.** `IV_SUGGEST_ACCOUNT`, one `IV_SUGGEST_TOKEN`, one
  set of playlists.
- **The personalisation already exists.** `read_user()` reads `users.watched`
  and `users.subscriptions` for that one email, and everything downstream is
  already "this person's taste". Nothing in the scoring is account-specific.
- **So this is plumbing, not algorithm work.** The engine is already personal;
  it only has one person.

## Credentials: mint a SID, no token per user

`helpers/handlers.cr` — `/api/v1/auth/*` accepts a bare `SID` cookie and grants
`scopes = [":*"]`. Full scope, no HMAC, no password. A SID is nothing but
`Base64.urlsafe_encode(Random::Secure.random_bytes(32))` in
`session_ids(id, email, issued)`.

→ **The bot mints its own credential for any account with one INSERT.** No new
secret, no password prompt, no fork change, no reimplementation of the signed
token scheme.

Design in: these are real logins. Tag them (`iv-suggest-<email>`), keep exactly
one per account, reuse it. Do not leak a row per run.

## Schema: add an account key

Every `suggest.*` table becomes account-scoped. Mechanical — but **miss one and
accounts bleed into each other**:

| Table | Change |
|---|---|
| `lanes` | PK `(account, lane)` |
| `items` | PK `(account, lane, vid)` |
| `cooldown` | PK `(account, lane, vid)` |
| `runs`, `shuffles` | add `account` |
| `video_meta`, `video_dead` | **stays global — do not scope** |

**The shared cache is the whole economy.** `video_meta` is the expensive thing:
every miss is a YouTube fetch. Overlapping taste means the second account is
nearly free. Scoping it per-account multiplies the fetch bill by N for no gain.

**The one that will bite:** `dedupe_across_lanes` does `WHERE lane <> %s`.
Unscoped, one person's Gaming pick suppresses another's. Same for the
`song_key` dedupe. Both need `AND account = %s`.

## Config: who gets which lanes

`lanes.yml` stays the shared library. Opt-in sits on top:

```yaml
users:
  - email: primary
    lanes: all
  - email: second
    lanes: [suggested, music-discover, fresh-uploads]
    overrides:
      fresh-uploads: {size: 15}
```

- **`lanes: all` for the power user, an explicit short list for everyone else.**
  A three-lane household member does not need Shorts, Live streams and two mixes.
- Per-account `blocklist:` comes free — the `Blocked` playlist keys off
  `playlists.author`, so it is already per-account once `ACCOUNT` is a loop
  variable.
- Absent from `users:` means unmanaged. **Never auto-enrol.** A surprise dozen
  playlists in someone's account is a bad first impression.

## Cold start: not a problem worth solving

A new account has no history, so its own lanes start thin. Two things fix that
on their own:

- **A Takeout import lands the real history** and the lanes become good
  immediately. The engine needs watch depth, not tuning.
- **Until then the shared mix carries the home feed** (below), so an empty
  account still opens onto something worth watching.

→ **Do not build a cold-start ladder.** No history-depth gates, no forced
subscription-only diet for new accounts. It is scaffolding for a state that
lasts until the first import.

Worth keeping anyway, because it is one line and it is honest: log
`account has N watched entries` at run start, so a thin lane has a visible cause.

## The shared mix — everyone's best, one feed

The home feed becomes a blend of the highest-ranked content across every
account, not any single person's lane.

- **`policy: mix` already does the hard part.** `run_lane_mix()` rebuilds from
  its sources every run, never writes `suggest.items`, and costs **zero YouTube
  fetches**. A cross-account mix is the same machine with a wider source list.
- **Extend the source form** from `{lane, share}` to `{user, lane, share}`.
  `mix_sources()` and `weighted_mix()` need no change beyond the lookup:
  `SELECT plid FROM suggest.lanes WHERE account=%s AND lane=%s`.
- **Read the sources over SQL, not the API** — `playlists` joined to
  `playlist_videos` needs no auth at all, so the mix never touches another
  account's session.
- **Shares are the fairness knob.** Equal shares means an even split; weight the
  active account higher if the feed should still feel like theirs.
- **Filter per viewer, not per source:** drop what this viewer has already
  watched, and drop their own blocklist. Same video, different verdict per
  person — that is the point.

**Privacy is the real decision here.** A shared feed publishes what each person
watches to everyone else in the household. Make contributing **opt-in per
account**, not a default, and say so in the config comment.

## Privacy of the playlists themselves

Lanes are currently `privacy: public`, deliberately, so `/feed/playlist/<plid>`
gives RSS. Fine for your own account. **Not fine to provision for someone else
without asking.**

- Default new accounts to **unlisted**; opt in to public per account.
- Existing gotcha, already handled in `playlist_of()`: Invidious ignores
  `privacy` on create, so it must be forced with the PATCH call. Do not let the
  default flip.

## Fetch budget

- Today: `MAX_FETCHES = 320` per run, `RATE_PER_MIN = 20`.
- **Divide, do not multiply.** The pacing exists to keep YouTube calm; that is a
  property of the instance, not of the account.
- Global run budget, per-account slice, unspent slices roll forward.
- Order accounts **least-recently-succeeded first**, so an exhausted budget does
  not starve the same person every night.
- The shared mix costs nothing, so a light account stays close to free.

## Ops

- **`iv-suggest init --all-users`** — idempotent. Creates missing playlists,
  mints missing SIDs, never recreates. It will get re-run; make that safe.
- **`iv-suggest run`** loops accounts. One account's failure must not kill the
  rest — the existing per-lane try/except, one level up.
- **Metrics:** add an `account` label. Cardinality is a household, not a tenant
  list.
- **`iv-suggest status`** grouped by account.
- **Timers unchanged.** One nightly run, one hourly shuffle, both looping
  internally. Do not fan out to per-account units.

## Migration order

1. Schema migration with `account` defaulting to the current one → **existing
   rows and playlists survive untouched.** No playlist is recreated.
2. Land SID minting, switch the primary account's calls to it, retire
   `IV_SUGGEST_TOKEN` once proven.
3. Add the `users:` block with one entry. **Verify a run is identical to today.**
4. Only then add a second account, unlisted.
5. Shared mix last — it needs two populated accounts to be worth looking at.

**Steps 1-3 are the risky ones and all invisible from outside.** If step 3 does
not reproduce today's behaviour, stop.

## Open questions

- **Does a minted SID survive `pg_dump` restore?** `session_ids` is in the dump,
  so it should. Confirm rather than assume.
- **Where does the shared mix live** — one playlist on a service account, or a
  copy in each account? A copy each is simpler to consume and costs only API
  writes.
- **Rough size:** schema + SID + account loop ≈ half a day. Shared mix and
  privacy defaults ≈ another half.

Land the blocklist branch first; it touches the same `run_lane` signature.

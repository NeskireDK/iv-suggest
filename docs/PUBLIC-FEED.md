# TODO: the public feed, and what a brand new account sees

Status: **section 2 built and live; section 1 not started.** Written
2026-08-31.

Two gaps, one moment: somebody opens the instance and the engine has nothing
personal to offer them. A logged-out visitor has no account at all; a new account
has one but no history yet. Today both land on whatever `popular_playlists`
points at, which is one specific person's Home mix.

## Decided: how public is public?

**Public to everyone, no limitations** (Andre, 2026-08-31). No Invidious account
is needed to see the compiled feed; reaching the instance is enough. Cloudflare
is the only thing in front of it, and a visitor who is a Cloudflare user but not
an Invidious user is explicitly in scope.

So option 3 of the three that were on the table: accept the aggregate as
public, because it is a family instance. The privacy filter idea (only include
a video *n* accounts agree on) stays on the table as a **ranking** device, which
is what the scoring below already wants — not as an access control.

This unblocks section 1: the compiled playlist can be `privacy: public` and can
be what `popular_playlists` points at.

## 1. A public mix, compiled from everybody's

Wanted: **one playlist compiled from every account's home mix, where a video
several mixes agree on ranks higher.**

- **Not `policy: mix`.** `weighted_mix` divides slots by share — each source
  gets its cut, and a video in two sources is placed once, at its first claim.
  Ranking agreement higher is a scoring merge: score a video by how many mixes
  hold it and how high, then sort. Same inputs, different question. It wants its
  own policy (`consensus`?), not another flag on `mix`.
- **Scoring sketch, to argue with rather than implement as written:** sum
  `1 / (rank + k)` across the mixes holding it, so a video near the top of two
  mixes beats one at the top of a single mix, and a video everybody has near the
  bottom does not win on count alone. `k` decides how much depth matters.
- **Filtering has no viewer.** Every other lane filters against somebody's watch
  history and blocklist; this one has neither. Probably the union of the
  household's blocklists — a channel any member blocked is a bad thing to greet
  a stranger with.
- **It lives on the bot's own account**, because nobody in particular owns it.

## 2. Auto-enrol every account, and carry the ones with no history — BUILT

**Built 2026-08-31, not yet deployed.** `auto_enrol:` in `lanes.yml` takes in
every account on the instance including ones registered later; `min_watched`
holds back a lane an account cannot fill yet; and a `mix` source of
`{users: all, lane: X}` expands to one source per account, so the `household`
lane needs no names and survives enrolment. The `users:` block stays as the way
to give somebody *different* lanes; absent from it now means "the defaults", not
"untouched".

This reverses [MULTI-USER.md](MULTI-USER.md)'s "never auto-enrol". That rule was
written for a stranger finding a dozen unexplained playlists in their account.
On a family instance the effect was that the second account got nothing at all
for three weeks.

Auto-enrolment only works if a fresh account opens onto something. `read_user()`
returns an empty `watched`, so its seeds are empty and every `refill` lane comes
back near zero — the second account on this instance has 3 watched videos and no
subscriptions. So the two halves ship together:

**An account that cannot fill its lanes from its own history gets the compiled
mix from part 1 as its home feed.**

- **A fallback, not a phase.** Nothing to switch off later: the moment the
  account has history its own lanes fill and the fallback stops applying by
  itself. No "graduation" state to track.
- Cheapest form: a `mix` lane whose only source is the compiled feed, given to
  any account under a watch-count threshold. Zero fetches, since a mix reads its
  sources over SQL.
- **Enrolment is idempotent.** It is recomputed every run; `playlist_of()`
  creates only what is missing, and `min_watched` is a filter rather than stored
  state, so nothing has to be migrated when an account warms up.
- **A Takeout import remains the real fix** and lands the whole history at once.
  The fallback covers the days before somebody gets round to it.

## Order

The "how public" answer, then the compiled mix, then auto-enrolment with the
fallback — which is a mix lane pointing at that feed, so it is nearly free once
the mix exists.

## Defects found while writing the config reference, 2026-08-31

Each verified against the code, not inferred. The `docs` ones are fixed on the
branch that added [CONFIG.md](CONFIG.md); the `code` ones are open and land here
because auto-enrolment touches most of them.

### ~~code — per-account `overrides` can silently kill a lane's shuffle~~ FIXED

`lanes_for()` applied overrides with a shallow `lane.update(over)` *after*
`load_config()` had deep-merged `shuffle:` and `subscription:` key by key, so
`overrides: {gaming: {shuffle: {jitter: 0}}}` left a `shuffle` dict holding only
`jitter`, `rank_items()` raised `KeyError: 'fatigue'`, the per-lane try/except
swallowed it, and that lane never reordered again. Both paths now go through
`merge_lane()`.

### ~~code — `matches()` is dead, `min_seconds` ignores unknown lengths~~ RESOLVED

`matches()` was called from nowhere. The live filter is
`0 < secs < min_seconds`, so a video of unknown or zero length passes — which is
the right behaviour and the same fail-open rule the feed-kinds patch uses, so
the dead stricter copy was deleted rather than wired in. `CONFIG.md` says what
the filter actually does.

### ~~code — a lane silently accepts keys its policy never reads~~ FIXED

`init` and `run` now name them: `lane music-watched: policy last_played never
reads exclude_watched, expand, max_per_channel, min_seconds, ttl_days`. Those
five were live in this repo's own `lanes.yml` and have been removed.

### ~~code — `iv-suggest init` creates no playlists without `--all-users`~~ FIXED

`all_lanes` is loaded unconditionally, so a plain `init` creates the primary
account's missing playlists as the help always claimed. `--all-users` now only
decides *which accounts*.

### ~~code — `seed.from` is read by nothing~~ FIXED

Removed from `DEFAULTS`, from `lanes.yml` and from the reference.

### ~~code — `iv-suggest status` reports 0 videos for a mix lane~~ FIXED

Display only — the lane filled correctly the whole time. `status` counted
`suggest.items`, which a mix lane never writes. `cmd_metrics` already knew
better, so the two now share `lane_video_count()` and cannot drift again.

### docs — corrected in CONFIG.md

| Claim as written | What the code does |
|---|---|
| `seed.limit` defaults to the lane's `size` | defaults to `30`; the `size` fallback only fires when a lane supplies a `seed:` block that omits `limit` |
| `seed.scan` defaults to `limit × 20` with a genre | true for `refill`; `last_played` has its own reader defaulting to `size × 6` and never reads `seed.limit` |
| `seed.recent` pins the N newest entries | no effect on its own — `seed_order()` returns plain history unless `shuffle_window >= 2` |
| `sample_pool: 0` = strict | `1` is also strict, and it is forced to `0` whenever `expand: none` |
| `filter` costs one fetch per candidate | only on a cache miss, and the loop stops after `max(20, room × 3)` checks, which can end a refill early |
| `exclude_subscribed: false` re-admits subscribed channels | not under `channel_latest`, which filters the channel list unconditionally |
| `fetch_cap` has no "off" | `0` disables the per-lane cap |
| `round_robin_top`: every video leads before any repeats | only the half that has waited longest is eligible |
| a `mix` lane costs zero fetches | zero from the bot's budget, but a rebuild is a DELETE + POST per video and Invidious resolves each one server-side, outside the pacing |
| CONFIG.md is the complete env reference | `kickstart.py` also reads `IV_SUGGEST_DB_CONTAINER`, and reads the environment only — not `/etc/iv-suggest/env` |
| the `privacy` default applies to lanes | only to playlists at create time; an already-registered lane is never re-PATCHed |

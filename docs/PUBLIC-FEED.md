# TODO: the public feed, and what a brand new account sees

Status: **not started.** Written 2026-08-31.

Two gaps, one moment: somebody opens the instance and the engine has nothing
personal to offer them. A logged-out visitor has no account at all; a new account
has one but no history yet. Today both land on whatever `popular_playlists`
points at, which is one specific person's Home mix.

## Decide first: how public is public?

`iv.ariksen.dk` has no Authelia. A playlist feeding Popular is readable by
anybody who can reach the host, so a compiled feed publishes an aggregate of the
household's viewing to the open internet — past "the household sees each other's
taste", which is what [MULTI-USER.md](MULTI-USER.md) accepted.

Aggregation is not anonymisation: a two-person household with one obvious
enthusiasm is not hard to read. Options, cheapest first:

1. leave the public feed a fixed hand-curated playlist, keep the compiled one
   behind login;
2. only include a video *n* accounts share — agreement as the privacy filter,
   which the ranking below already computes;
3. accept it as-is, because it is a family instance.

This decides where the playlist lives and possibly what goes in it, so it comes
before either piece of work below.

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

### code — `matches()` is dead, and `min_seconds` does not do what it says

`matches()` is defined and called from nowhere. The live candidate filter is
`if lane["min_seconds"] and 0 < secs < lane["min_seconds"]`, so a video whose
length is unknown or zero **passes** the Shorts guard. `channel_videos` rows
carry `length_seconds = 0` for anything absent from the channel's Videos tab, so
this is the common case, not the edge one. Decide which behaviour is wanted,
then delete `matches()` or call it.

### code — a lane silently accepts keys its policy never reads

`last_played` reads only `id`, `title`, `size`, `fetch_cap`, `seed.genre`,
`seed.scan`, `dedupe_songs`, `played_decay` and `privacy`. `mix` reads only
`id`, `title`, `size`, `exclude_watched`, `mix.*` and `privacy`. Everything else
is `refill`-only. In this instance's own `lanes.yml`, `music-watched` sets
`ttl_days: 0`, `exclude_watched: false`, `min_seconds: 0`, `max_per_channel: 0`
and `expand: none` — all five inert. Nothing warns. Fix: a config check at load
that logs keys a lane's policy will ignore. Cheap, and it is the only thing that
stops this recurring every time a lane is copied.

### code — `iv-suggest init` creates no playlists without `--all-users`

`all_lanes = load_config() if args.all_users else []`, so a plain `init` runs
`lanes_for(user, [])` and creates nothing; the playlists appear on the first
`run`. Either the flag should stop gating this or the argparse help and the
README walkthrough should stop promising it. Auto-enrolment makes `--all-users`
the normal case anyway, which probably answers it.

### code — `seed.from` is read by nothing

Present in `DEFAULTS`, never consulted. Setting it to anything is silently
inert. Either implement a second source or drop the key.

### docs — corrected in the CONFIG.md branch

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

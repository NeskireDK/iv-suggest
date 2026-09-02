# The public feed, and what a brand new account sees

Why the two compiled feeds are shaped the way they are. The keys themselves are
in [CONFIG.md](CONFIG.md#consensus), and whether an instance has them switched
on is a question for its own `lanes.yml`, not for this file.

Two gaps, one moment: somebody opens the instance and the engine has nothing
personal to offer them. A logged-out visitor has no account at all; a new
account has one but no history yet. Before either section, both landed on
whatever `popular_playlists` pointed at, which was one specific person's Home
mix.

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

Decided 2026-08-31 (Andre): **the public feed is every account's home mix,
resampled at random every hour, with a video ranked up the more mixes hold it.**

That settles the three things the sketch below left open — the source set, the
cadence, and what agreement buys a video.

### Source: `home-mix`, not every lane

One lane per account, the `home-mix` lane, expanded through the existing
`{users: all, lane: home-mix}` form that `household` already uses. So the
compiled feed inherits enrolment for free: an account added next month appears
in it without the config being touched, and an account whose `home-mix` is empty
contributes nothing rather than breaking the merge.

Reading `home-mix` rather than the raw lanes also means the per-account taste
filtering has already happened once. The compiled feed is a merge of opinions,
not a merge of catalogues.

### Cadence: hourly, and random within the hour

The membership question and the ordering question separate the same way they
already do for a normal lane: the nightly run decides what is eligible, the
hourly reorder decides what sits at the top. Here both are cheap, because a
`home-mix` is already in the database — so the whole rebuild rides the existing
`iv-suggest-shuffle.timer` and costs **zero fetches**, exactly as `mix` does.

Random every hour, not deterministic-best every hour: a strict score ordering
would pin the same dozen videos to the top of a public playlist for as long as
those mixes hold them. The random draw is what makes the feed feel alive to a
visitor who opens it twice in a day. So the score sets each video's *weight in a
weighted sample*, not its rank.

### What duplication buys

Agreement raises a video's weight; it does not gate its inclusion. Sketch to
argue with:

- Base weight per holding mix, `1 / (rank + k)`, so depth in one mix counts for
  something and a video near the top of a mix counts for more.
- Sum across the mixes holding it, then apply an agreement multiplier so two
  mixes agreeing beats the same total accumulated from one deep placement.
- Draw the playlist as a weighted sample without replacement, so a
  low-agreement video is unlikely rather than excluded.

With one account enrolled and any history, every weight collapses to the single
mix's `1 / (rank + k)` and the feed degrades to "that account's home mix,
shuffled hourly" — which is what the instance shows today, so the first version
cannot regress the status quo.

### The rest, unchanged from the sketch

- **Not `policy: mix`.** `weighted_mix` divides slots by share — each source
  gets its cut, and a video in two sources is placed once, at its first claim.
  Ranking agreement higher is a scoring merge, a different question of the same
  inputs. It wants its own policy, `consensus`.
- **Filtering has no viewer.** Every other lane filters against somebody's watch
  history and blocklist; this one has neither. Use the union of the household's
  blocklists — a channel any member blocked is a bad thing to greet a stranger
  with. Do *not* filter on watch history: there is no viewer whose history it
  could be.
- **It lives on the bot's own account**, because nobody in particular owns it,
  with `privacy: public`, and `popular_playlists` is pointed at it. That last
  step is a compose change on the instance, so it is deliberately not something
  merging the lane does.

### Settled while building

`policy: consensus`, its keys and their defaults are in
[CONFIG.md](CONFIG.md#consensus); this is only why each number is what it is.

- **`k` = 4** (`consensus.rank_offset`). It sets how much of the feed is the
  mixes' own order and how much is agreement. At `k=1` the top of a mix
  outweighs its tenth entry ten to one, so each mix's head is effectively
  pinned; at `k=20` the mix's order barely survives and only agreement counts.
  At 4 the ratio over the first ten entries is 3.5 to 1, and a video two mixes
  hold at rank 4 outweighs another account's top pick — which is the editorial
  stance this feed is meant to take.
- **The agreement multiplier is `holders ** 1.0`** (`consensus.agreement_power`),
  so two accounts agreeing doubles the weight and three treble it. Linear is the
  simplest shape that makes agreement beat any depth a single mix can reach, and
  the exponent is there because that is the knob worth turning: `0.0` is "no
  consensus at all" and reads as a sanity switch.
- **The sample size is the lane's own `size`.** No new key: `size` already means
  "videos the lane holds", and a second name for it would be one more thing to
  keep in step. The live value is a config change, not a code one.
- **The hourly redraw is fully fresh.** No fatigue counter, no memory of what
  was on screen last hour. A compiled lane writes no `suggest.items` row, so
  there is nowhere to age a counter anyway, and there is no one viewer to
  disorient. Membership still changes only nightly: the reorder permutes, which
  is the same division `mix` already lives under.

Two things the spec left ambiguous, resolved the same way:

- "Redrawn every hour" and "the nightly run decides what is eligible" pull in
  different directions once the pool is bigger than the lane. Membership is
  nightly and the **order** is hourly, because the hourly reorder can only
  permute a playlist without spending upstream writes on it, and because a
  visitor opening the feed twice in a day sees a different top either way.
- A source naming an account that has no such lane still aborts the lane, as it
  does for `mix`: that is a typo, not enrolment. Only the `users: all` form is
  allowed to skip a member silently, which is what makes an empty `home-mix`
  contribute nothing.

## 2. Auto-enrol every account, and carry the ones with no history

`auto_enrol:` in `lanes.yml` takes in
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
- The lane that carries it is `household`, a `mix` over `{users: all}` rather
  than over the compiled feed: it filters per viewer, which the compiled feed
  cannot do, and it costs no fetch either way because a mix reads its sources
  over SQL.
- **Enrolment is idempotent.** It is recomputed every run; `playlist_of()`
  creates only what is missing, and `min_watched` is a filter rather than stored
  state, so nothing has to be migrated when an account warms up.
- **Every reader of a lane list applies the gate, not just the filler.**
  Reporting the ungated list names lanes the run will skip — on the night the
  second account was auto-enrolled that raised twelve "lane has not run" alerts
  for lanes that were never going to run. `run` applies it inline through
  `lanes_for()`, since it already has the watch history in hand; `shuffle`,
  `init` and `metrics` call `enrolled_lanes()`, which looks the count up for
  them. `status` needs no gate at all: it reads `suggest.lanes`, and a held-back
  lane has no row there to report.
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

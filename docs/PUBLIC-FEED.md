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

## 2. Fall back to popular when an account has no history

`read_user()` returns an empty `watched` for a new account, so the seeds are
empty and every `refill` lane comes back near zero. MULTI-USER.md rules out a
cold-start ladder and that still holds. This is the one exception, and it is one
rule, not a ladder:

**When an account's lanes cannot be filled from its own history, serve the public
compiled mix instead of an empty lane.**

- **A fallback, not a phase.** Nothing to switch off later: the moment the
  account has history its own lanes fill and the fallback stops applying by
  itself. No "graduation" state to track.
- Cheapest form: a `mix` lane whose only source is the public compiled feed,
  given to any account under some watch-count threshold. Zero fetches, since a
  mix reads its sources over SQL.
- **A Takeout import remains the real fix** and lands the whole history at once.
  The fallback covers the days before somebody gets round to it.

## Order

The "how public" answer, then the compiled mix, then the fallback — which is a
mix lane pointing at that feed, so it is nearly free once the mix exists.

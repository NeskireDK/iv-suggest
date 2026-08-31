# TODO: the public feed, and what a brand new account sees

Status: **not started.** Written 2026-08-31.

Two gaps, both about the same moment: somebody opens the instance and the
engine has nothing personal to offer them. A logged-out visitor has no account
at all; a new account has one but no history yet. Today both land on whatever
`popular_playlists` points at, which is one specific person's Home mix.

## 1. A public mix, compiled from everybody's

The front page for a logged-out visitor should be the household's, not one
member's. `popular_playlists` currently names `home-mix`, so an anonymous
visitor to `iv.ariksen.dk` is looking at exactly what one account watches.

The wanted shape: **one playlist compiled from every account's home mix, where
a video several people's mixes agree on ranks higher.**

- **This is a different algorithm from `policy: mix`.** The existing
  `weighted_mix` divides slots by share — each source gets its cut and a video
  in two sources is placed once, at its first claim. Ranking agreement higher
  is a scoring merge: score a video by how many mixes hold it and how high, then
  sort. Same inputs, different question. It wants its own policy
  (`policy: consensus`?), not another flag on `mix`.
- **Scoring sketch, to argue with rather than implement as written:** sum
  `1 / (rank + k)` across the mixes holding it, so a video near the top of two
  mixes beats one at the top of a single mix, and a video everybody has near the
  bottom does not win on count alone. `k` decides how much depth matters.
- **Filtering has no viewer.** Every other lane filters against somebody's watch
  history and blocklist; this one has neither. Decide what applies — probably
  the union of the household's blocklists, since a channel any member blocked is
  a bad thing to greet a stranger with.
- **It lives on the bot's own account**, not in a copy per person, because
  nobody in particular owns it.

### Decide before building: how public is public?

`iv.ariksen.dk` has no Authelia. A playlist feeding Popular is readable by
anybody who can reach the host, so this publishes an aggregate of the
household's viewing to the open internet — a real step past "the household sees
each other's taste", which is what was accepted in `MULTI-USER.md`.

Aggregation is not anonymisation: a two-person household with one obvious
enthusiasm is not hard to read. Worth a deliberate answer, not an assumption.
Options, cheapest first: leave the public feed as a fixed hand-curated playlist
and keep the compiled one behind login; only include a video *n* accounts share
(agreement as the privacy filter, which the ranking already computes); or accept
it as-is because it is a family instance.

## 2. Fall back to popular when an account has no history

A new account's lanes are thin — `read_user()` returns an empty `watched`, so
the seeds are empty and every `refill` lane comes back near zero. `MULTI-USER.md`
says not to build a cold-start ladder, and that still holds: no history-depth
gates, no forced subscription-only diet. This is the one exception, and it is
one rule, not a ladder:

**When an account's lanes cannot be filled from its own history, serve the
public compiled mix instead of an empty lane.**

- **A fallback, not a phase.** Nothing to switch off later: the moment the
  account has history its own lanes fill and the fallback stops applying by
  itself. No "graduation" state to track.
- Cheapest form: a mix lane whose only source is the public compiled feed,
  given to any account whose watch count is under some threshold. Costs zero
  fetches, since a mix reads its sources over SQL.
- Related and already logged: `run_account()` prints `account X: N watched`, so
  a thin lane already has a visible cause.
- **A Takeout import remains the real fix**, and lands the whole history at
  once. The fallback is what covers the days before somebody gets round to it.

## Order

Build the compiled mix first — the fallback is a mix lane pointing at it, so it
is nearly free once the mix exists. Answer the "how public" question before
either, because it decides where the playlist lives and possibly what goes in it.

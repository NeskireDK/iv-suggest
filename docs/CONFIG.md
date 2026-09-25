# Configuration reference

Every setting, once. Two places hold configuration:

- **`/etc/iv-suggest/env`** — where this install is: database, API, paths.
  Also read from the process environment. See [env.example](../env.example).
- **`/etc/iv-suggest/lanes.yml`** — what the bot makes: lanes, who gets them,
  the blocklist. See [lanes.yml](../lanes.yml) for a working file.

The engine ships as a container and takes its settings from the environment
compose gives it, so a default install sets `IV_SUGGEST_ENVFILE=/dev/null` and
uses no file at all. The file is still read when you run the script directly:
`env` is read off disk as well as from the environment, so a value exported only
in your shell is missing from a run started by a timer — test with `env -i`.

## Environment

| Variable | Default | Meaning |
|---|---|---|
| `IV_SUGGEST_ACCOUNT` | — | **Required by `init`.** `users.email` of the account the bot belongs to. Owns the blocklist playlist; every pre-multi-user row migrates to it. With a `users:` block the other commands do not need it |
| `IV_SUGGEST_API` | `http://localhost:3000` | Invidious base URL |
| `IV_SUGGEST_DB_HOST` | `invidious-db` | Postgres host. The default is the compose service name, which is what resolves from a container on the same network |
| `IV_SUGGEST_DB_PORT` | `5432` | Postgres port |
| `IV_SUGGEST_DB_USER` | `kemal` | Postgres role |
| `IV_SUGGEST_DB_NAME` | `invidious` | Postgres database |
| `IV_SUGGEST_DB_PASSWORD` | — | **Required.** Reuse the one Invidious's own compose already holds. It travels in the environment and never on a command line, which every process on the box can read |
| `IV_SUGGEST_DB_CONNECT_TIMEOUT` | `10` | seconds before giving up on the connection, so a dead database fails the run instead of holding it open |
| `IV_SUGGEST_DB_STATEMENT_TIMEOUT_MS` | `300000` | milliseconds any one statement may take. The engine sets this itself rather than inheriting `PGOPTIONS`, because that variable can also redirect a connection |
| `IV_SUGGEST_DASHBOARD_ROLE` | `grafana_ro` | Postgres role `init` grants SELECT on the log tables to, so a dashboard can read them. Only the tables in `READABLE_BY_DASHBOARD`: everything else in the schema is revoked from it, including `suggest.accounts`, which holds a live session id per account. Missing role = nothing granted; set it to a space to turn the grant off entirely |
| `IV_SUGGEST_REVISION` | `unknown` | set by the image build. `run` and `shuffle` print it, so the journal says which code produced a night's fill |
| `IV_SUGGEST_CONFIG` | `/etc/iv-suggest/lanes.yml` | lane config path |
| `IV_SUGGEST_ENVFILE` | `/etc/iv-suggest/env` | file the above are also read from |
| `IV_SUGGEST_DB_CONTAINER` | `youtube-invidious-db-1` | `kickstart.py` only, which talks to the container directly and reads the environment alone — not this file |

## `lanes.yml`

Top-level keys: `blocklist`, `auto_enrol`, `users`, `defaults`, `lanes`.

### `blocklist`

| Key | Default | Meaning |
|---|---|---|
| `playlist` | `Blocked` | title of a playlist on each account; `""` disables the mechanism |
| `channels` | `[]` | extra channel ids, for a channel with nothing convenient to tap |

Per account, because the playlist keys off its owner.

### `auto_enrol`

Take in every account on the instance, including ones registered later. Omit
the block and only the accounts written out in `users:` are managed.

| Key | Default | Meaning |
|---|---|---|
| `lanes` | `all` | `all`, or a list of lane ids — what an unlisted account gets |
| `exclude` | `[]` | emails to leave alone |

Enrolment is re-checked on every run, so a new account is picked up the same
night. It is safe because of `min_watched`: a lane an account cannot fill yet is
held back rather than created empty, and appears on its own once the history is
there. Give the account something in the meantime — a `mix` lane sourced from
`users: all` costs nothing and needs no history.

⚠️ **A `consensus` lane reaches exactly one account, whatever this block says.**
That policy compiles one playlist for the whole instance, so a copy per account
would hold the same videos in every one of them. Neither the `all` form nor the
list form hands one out. A `users:` entry naming the lane id claims it; if none
does, it lands on `IV_SUGGEST_ACCOUNT`. `run` and `init` say so when a compiled
lane reaches nobody at all.

### `users`

Per-account settings. With `auto_enrol` this is how you give somebody
*different* lanes, not how you opt them in; without it, it is the whole list of
managed accounts and **an account not listed is never touched**. Omit both
blocks and the bot manages the single `IV_SUGGEST_ACCOUNT` with every lane,
which is what it did before multi-user.

| Key | Default | Meaning |
|---|---|---|
| `email` | — | **Required.** the `users.email` value |
| `lanes` | `all` | `all`, or a list of lane ids. Order is always the file's, so mix lanes still run last |
| `overrides` | `{}` | `{lane-id: {key: value}}` — lane keys changed for this account only. **`policy` is refused**: it decides which keys the lane reads and how many copies of it exist, so it is what a lane *is*, not a per-account setting. Give the account a lane of its own instead |

An override patch merges into `shuffle`, `subscription`, `mix` and `consensus`
key by key, so setting one of their keys keeps the rest. Every other block,
`seed` included, is replaced whole.

### `defaults`

Any lane key, applied to every lane. A lane overrides any of them.

### `lanes`

A list. Every lane needs `id` and `title`; everything else falls back to
`defaults`, then to the built-in below.

⚠️ **A lane's `policy` decides which keys are read at all.** The universals are
`id`, `title`, `size`, `fetch_cap`, `privacy`, `min_watched` and `shuffle`.
`last_played` reads those plus `seed`, `dedupe_songs` and `played_decay`; `mix`
reads them plus `exclude_watched` and `mix`; `consensus` reads them plus
`consensus` and nothing else. Everything under Turnover and Candidate rules
below, plus `expand`, `filter` and the `expand`-specific keys, is **`refill`
only**. `init` and `run` name any inert key a lane sets:
`lane music-watched: policy last_played never reads ttl_days`.

| Key | Default | Meaning |
|---|---|---|
| `id` | — | **Required.** stable key for state, overrides and `--lane` |
| `title` | — | **Required.** the playlist title Invidious shows |
| `policy` | `refill` | `refill` \| `last_played` \| `mix` \| `consensus` — see [README](../README.md#what-a-lane-is) |
| `expand` | `recommended` | where candidates come from: `recommended` \| `channel_latest` \| `channel_popular` \| `topic_burst` \| `subscription_feed` \| `none` |
| `size` | `30` | videos the lane holds |
| `min_watched` | `0` | skip this lane for an account with fewer watched videos than this. The gate for auto-enrolment — no state to flip, the lane appears once the history exists |
| `privacy` | `unlisted` | `unlisted` \| `public` \| `private`. Only `private` breaks `/feed/playlist/<plid>`. **Applied when the playlist is created and never again** — changing a live lane is a database edit |
| `filter` | `{}` | `{genre: X}` — the only filter key, and `refill` only. One fetch per candidate whose genre is not already cached; the loop gives up after `max(20, room × 3)` checks, which can leave a refill short |
| **Turnover** | | |
| `ttl_days` | `14` | drop an unwatched entry older than this, in calendar days. `0` = never |
| `stale_after_active_days` | `0` | drop an unwatched entry once this many days **the account actually watched something** have passed since it was added. Days of use, not calendar days, so a fortnight away does not empty the lane. `0` = off, and `ttl_days` is the calendar backstop underneath it. Reads `suggest.plays`, so an account the harvest records no plays for never accrues a day and keeps everything until `ttl_days` |
| `refresh_per_day` | `0` | retire this many of the oldest every run, whatever the TTL says |
| `keep_min` | `0` | never let `refresh_per_day` rotate the lane below this many videos |
| `grow_per_day` | `0` | most videos one run may add. `0` = fill every empty slot at once. What lets a lane have a big `size` without a night that tries to reach it |
| `displace_when_full` | `false` | turnover as displacement instead of expiry: retire nothing on a clock, let the lane grow into `size`, then let each new video take the place of the lowest **standing** one — its stored score at the same two decays `display_score` applies, minus the fatigue and jitter that mean only this hour. Refused with `grow_per_day: 0`, which would fill the lane once and freeze it |
| `sample_pool` | `0` | pick from a weighted random draw over the top N candidates instead of the strict top. `0` or `1` = strict, as is any value when `expand: none` |
| `cooldown_days` | `60` | a dropped video is not offered again for this long |
| `watched_cooldown_days` | `365` | same, for a video that was dropped because it was watched |
| `rotate_cooldown_days` | `21` | same, for one dropped by `refresh_per_day`, `stale_after_active_days` or `displace_when_full` |
| **Candidate rules** | | |
| `exclude_watched` | `true` | drop what this account already watched |
| `exclude_subscribed` | `true` | skip what the subscription feed already offers. What that means is `subs_feed_window`'s to say. `false` turns it off entirely, which is what the subscription lanes do |
| `subs_feed_window` | `50` | how many of the feed's newest videos count as "already offered". `0` restores the old meaning — the whole channel, banned on sight |
| `dedupe_across_lanes` | `true` | a video sits in one lane at a time, per account |
| `dedupe_songs` | `true` | one upload per song, across every lane a person holds. `dedupe` skips a lane that sets it `false`, and skips a compiled lane whatever it says. See [README](../README.md#song-identity) |
| `min_seconds` | `120` | drop anything shorter, **when the length is known** — a candidate reporting `0` seconds passes. `0` = off |
| `max_seconds` | `0` | drop anything longer. `0` = no bound; use it against compilations |
| `max_per_channel` | `2` | most entries one channel may hold. `0` = no limit |
| **Fetch budget** | | |
| `fetch_cap` | `80` | most fetches this lane may take from the run budget. `0` = uncapped |

Per-`expand` keys, ignored by the other modes:

| Key | Default | Applies to | Meaning |
|---|---|---|---|
| `seed` | `{limit: 30}` | `recommended`, `channel_latest`, `none` | see below |
| `recommend_max_age_days` | `0` | `recommended` | drop a recommendation older than this. `0` = off. Free — `recommendedVideos` carries `published`, as an RFC3339 string derived from its relative "21 hours ago" text, so it is approximate. Fine at day scale |
| `recommend_age_halflife_days` | `0` | `recommended` | halve a candidate's score per N days since it was uploaded. `0` = off. `recommend_max_age_days` decides what may enter at all; this decides how much of what enters is old, and costs nothing extra for the same reason |
| `recommend_age_floor` | `0.15` | `recommended` | the discount an ancient recommendation bottoms out at |
| `affinity` | see below | `recommended`, `channel_latest` | see below |
| `max_channels` | `12` | `channel_latest`, `channel_popular` | channels to poll per run |
| `max_age_days` | `21` | `channel_latest`, `topic_burst` | how new an upload must be |

⚠️ **A `published` in the future is read as no date at all, not as a very new
upload.** Anything past five minutes ahead of now — past ordinary clock drift —
is refused where the listing is parsed, so every age cutoff above rejects it
exactly as it rejects a listing carrying no date, and every ranking that prefers
recent scores it as 30 days old. One such video was live on 2026-09-25 dated
**2027-02-02**; it would have led any date-ranked lane for as long as the lane
held it, and nothing was going to age it out.

| `burst` | see below | `topic_burst` | see below |
| `subscription` | see below | `subscription_feed` | see below |
| `played_decay` | `0.99` | *policy* `last_played` | score falloff per rank in play order |

#### `expand: channel_popular` — the back catalogue

`channel_latest` asks `/api/v1/channels/<ucid>/latest`, the newest ~60 with no
paging. So a channel's **older** uploads were unreachable however much somebody
watched it — and its newest ~60 is exactly what the subscription feed already
shows. That is why the dedupe above re-admits almost nothing on its own: the
thing worth having was never being offered.

`channel_popular` asks `/videos?sort_by=popular` instead: same shape, ordered by
views, at any age. One fetch per channel for about 60 videos.

- It picks channels from the `affinity` weights through the same weighted draw
  `channel_latest` uses, capped by `max_channels` — so it follows what somebody
  actually returns to rather than a list anybody maintains.
- **Subscribed channels included**, unlike `channel_latest`. Their back
  catalogue is the half the feed never shows.
- **No age cutoff.** `max_age_days` is not read here. A five year old video with
  two million views is the point of the lane, not a failure of it.

`exclude_watched` does most of the filtering, since these are by construction
the channels the account has watched most.

#### `subs_feed_window`, and why the ban became a dedupe

`exclude_subscribed` was justified by *"their feed already shows them"*. That is
only true of the videos the subscription view is **currently** showing — a
channel's older uploads fall out of it and stop being duplicates. So the rule
now skips those videos rather than the channel, and it maintains itself: as a
video falls out of the feed it becomes eligible here.

**Expect the direct effect to be small.** Over five nights and 52 lane-runs the
old rule rejected **13 videos in total**, about 2.6 a night. The reason to
change it is the loop behind that number: a banned channel never entered a lane,
so it never became a *seed*, so the recommendation graph was never asked what
sits near it.

⚠️ **It changes which candidates survive, not whom the bot polls.**
`expand: channel_latest` still chooses only unsubscribed channels. A subscribed
channel's newest ~60 uploads *are* the feed, so polling it would spend a fetch
to rediscover what `subscription_feed` already has for free. Reaching a
subscribed channel's **older** uploads needs a source that can ask for them.

Set `0` to get the old whole-channel ban back on a lane that wants it.

#### `affinity`

How much the account has been watching a channel lately, as a multiplier on
every candidate from it. Read by `expand: recommended` and
`expand: channel_latest`; the other expanders never see it.

| Key | Default | Meaning |
|---|---|---|
| `scan` | `1500` | how deep into `users.watched` to look |
| `halflife_entries` | `200` | a watch this many entries further back counts half |
| `boost` | `2.0` | the multiplier the most-watched channel takes |
| `floor` | `0.7` | the multiplier everything else bottoms out at |

**Entries, not days.** `users.watched` is the complete watch record and carries
no clock, so these count positions.

⚠️ **Convert with the play rate, not with how fast the array grows.** Invidious
does `array_append(array_remove(watched, id), id)`, so a re-watch does not
lengthen the array — it *moves* the id to the end and pushes everything behind
it back one. Position therefore falls away once per **play**, re-watches
included, while the array's length only counts videos seen for the first time.
Measured here, the length grew ~22 a day over 2026-09-17→09-24 while
`suggest.plays` recorded **30.1 watched a day over the same week and 30.4 over
the 18 days before it** — so `200` is about a week, not the ten days the growth
rate suggests. Both figures are upper bounds on the age, because the harvest
cannot see a play of anything Invidious fetched in the last ten minutes.

**Log, not linear.** Watch counts are heavily skewed. On one real history the
top channel stood at 15.4 weighted watches against a median of 0.1, so scaling
against the top linearly left **574 of 586 channels within 0.08 of the floor** —
a two-channel promotion rather than an affinity. The log curve puts the top at
`boost` and the dozen behind it between 1.4 and 1.9.

⚠️ **`floor` applies to a channel the history has never named**, not just to one
watched long ago — most of the history has no cached channel, so there is no
way to tell those apart. It is a floor and not a zero for that reason: a zero
would blank a candidate on missing data rather than on dislike.

An account whose history names **no** channel at all gets a multiplier of
exactly `1.0` rather than the floor, so a fresh account is scored as it was
before this existed.

⚠️ **`max_per_channel` still caps what a promoted channel may contribute.**
Raising `boost` without raising that cap promotes a channel into a limit it
already hits.

#### `burst`

`expand: topic_burst` fills a lane with a topic the account was not watching a
month ago and is now. It reads the titles already in `suggest.video_meta` for
the newest `recent` entries of `users.watched` and the `baseline` before them,
takes the words and adjacent word pairs of each, and keeps the terms that are
both frequent now and rare then. Then one search per term.

| Key | Default | Meaning |
|---|---|---|
| `recent` | `200` | newest watch-history entries that count as "now" |
| `baseline` | `1000` | the entries before those, which "now" is rare against |
| `terms` | `2` | how many topics to search for, so also how many fetches the lane costs |
| `min_recent` | `3` | a term must appear in at least this many recent titles |
| `min_lift` | `20.0` | and be at least this many times commoner now than before |

**Both floors, because either alone is noise.** Lift on its own promotes a word
watched twice; a raw count promotes whatever the account always watches. Between
them they do the work a long stopword list would, which is why the shipped one
is short.

**One term per topic.** Longer terms are preferred — a word pair makes a better
search than either word alone — and a term is dropped when it shares a word with
one already taken, or turns up in more than 60% of the same videos. The two
tests catch different things: *"wow forever"* and *"warcraft forever"* sit in
different titles and only the shared word separates them, while *"wow"* and
*"warcraft forever"* share no word and only the co-occurrence does.

⚠️ **Synonyms with no shared word still get two slots.** *"wow forever"* and
*"world warcraft"* are one topic to a reader and two to this, because they
neither share a word nor sit in the same titles. The cost is one wasted search
and a lane fuller of the hottest topic; the results themselves de-duplicate.

⚠️ **The lane feeds itself.** Promoting a topic makes more of it get watched,
which strengthens the burst. Keep the lane small, keep `ttl_days` short, and
leave `stale_after_active_days: 0` so it empties when the terms stop clearing
the floor rather than holding a topic that has passed.

#### `seed`

Which watch-history entries the expansion starts from — the only source there is.

| Key | Default | Meaning |
|---|---|---|
| `limit` | `30` | how many seeds to use. The lane's `size` is the fallback only when the lane supplies a `seed:` block that omits `limit` |
| `scan` | `limit × 20` with a genre, else `limit` | how deep to walk the history looking for them. Policy `last_played` reads this key separately and defaults it to `size × 6` |
| `genre` | — | only seed from watched videos of this genre |
| `recent` | `0` | pin the N newest entries in front of the shuffle. **Does nothing unless `shuffle_window` is 2 or more** — there is no shuffle to pin them in front of |
| `shuffle_window` | `0` | draw seeds in random order from the N most recent entries, so the candidate set differs nightly even when nothing new was watched. `0` = plain history order |

#### `subscription`

`expand: subscription_feed` reads the `channel_videos` table over SQL — **zero
YouTube fetches**. `kind` is written by the Invidious patch in
[`patches/`](../patches/README.md); without it these lanes have nothing to
filter on.

| Key | Default | Meaning |
|---|---|---|
| `kinds` | `[video]` | any of `video`, `short`, `live` |
| `max_age_hours` | `0` | only entries this new. `0` = no bound |
| `rank` | `views` | `views`, or `published` for anything else — an unknown value is not rejected |

#### `mix`

`policy: mix` interleaves other lanes, under the lane's `mix:` key. It rebuilds
from its sources every run and reads them over SQL, so it spends **nothing from
the fetch budget** and never needs another account's session. It is not free
upstream, though:
a rebuild is one DELETE and one POST per video, and Invidious resolves each
added video server-side, outside the bot's pacing. See
[the shared rules](#rules-the-two-compiled-policies-share) for the two it
follows along with `consensus`.

| Key | Default | Meaning |
|---|---|---|
| `sources` | — | **Required.** list of `{lane, share}`, optionally `{user, lane, share}` or `{users: all, lane, share}` |
| `sources[].lane` | — | lane id to draw from |
| `sources[].share` | `1.0` | share of the **output**, checked at every slot, so a 10% source lands about every tenth position rather than in a block |
| `sources[].user` | the account being filled | draw from another account's copy of that lane. A named account that has no such lane aborts the mix |
| `sources[].users` | — | `all` expands to one source per managed account, splitting `share` evenly. Names nobody, so it survives enrolment; a member without that lane yet is skipped rather than aborting |
| `pure` | `0` | first N slots come from the first source alone, in its own order |

The older `{base, blend, ratio}` form is still read so a pre-2026-08-17 config
keeps working: `ratio: N` (default `2`) means N `base` per 1 `blend`. It was
replaced because a fixed interleave cannot absorb a source running out: with a
30-video base and a 30-video blend at `ratio: 2` the base was exhausted at
position 34, the entire rest of the blend was appended, and a lane meant to be
mostly base came out 50/50 with a 26-long blend block at the end. Under `share`
a spent source simply stops being eligible and the others take its share.

Filtering is per **viewer**, not per source: whatever anyone contributed is
dropped if this viewer already watched it or blocked its channel.

#### `consensus`

`policy: consensus` compiles **one** playlist out of every account's mix and
weights a video by how many of those mixes hold it, under the lane's
`consensus:` key. Like `mix` it rebuilds from its sources every run, reads them
over SQL, spends **nothing from the fetch budget** and never needs another
account's session; and like `mix` it is one DELETE and one POST per video
upstream.

| Key | Default | Meaning |
|---|---|---|
| `sources` | `[{users: all, lane: home-mix}]` | list of `{lane}`, `{user, lane}` or `{users: all, lane}`, read exactly as `mix.sources` is. `share` is not read: this policy scores, it does not divide slots |
| `rank_offset` | `4.0` | the *k* in `1 / (rank + k)`, one video's weight from one mix. Must be above zero. Lower pins the head of each mix in place; higher flattens the mix's own order until only agreement counts |
| `agreement_power` | `1.0` | exponent on the number of mixes holding a video. `1.0` doubles what two accounts agree on, `0.0` turns agreement off and leaves the depth sum |
| `published_halflife_days` | `0` | upload age halves a video's weight every N days. `0` = off, which is the plain depth-and-agreement weight |
| `published_floor` | `0.15` | the least that discount may leave, so an old video is unlikely to lead rather than excluded |

The weight of a video is `1 / (rank + k)` summed over every mix holding it,
times `holders ** agreement_power`, times the upload-age discount; the playlist
is then a weighted sample without replacement of `size` videos. So agreement **raises** a video's weight
rather than gating its inclusion — with the defaults, a video two mixes hold at
rank 4 outweighs another account's top pick, and a video only one account holds
is unlikely rather than excluded.

**Upload age, and why the feed decides it rather than the source.** A source
lane may rank an old video highly on purpose — `music-discover` turns its own
`published_halflife_days` off, because a song is not stale for being from 1998,
and `channel_popular` reads no `max_age_days` at all, because a five year old
video with two million views is the point of that lane. Both of those hold
inside a playlist somebody opened for the genre and stop holding in a feed a
logged-out visitor reads as what is happening now. So the discount is the
consensus lane's own setting, applied to every source alike; it changes nothing
about how the source lanes rank themselves.

The dates come from `playlist_videos`, in one query per draw, so this costs no
fetch and no extra table. A video the playlist carries no usable date for — none
at all, or one naming the future — is treated as 30 days old rather than dropped.

⚠️ **With more than one mix in play this also thins what gets in, not only where
it sits.** One account holding a `home-mix` means the draw takes every video it
has and the weights decide order alone. Two mixes make the pool bigger than
`size`, and then a floored weight is a video that often does not appear at all.

Three things follow from the feed having no viewer:

- **No watch-history filter.** There is no viewer whose history it could be, so
  `exclude_watched` is not read.
- **The blocklist is the union of every managed account's.** A channel anyone
  blocked is a bad thing to greet a stranger with.
- **The draw is fresh every hour.** The reorder redraws the whole order from the
  same weights and ages no fatigue counter — nothing is pinned in place, because
  a public playlist has no one viewer to disorient. Membership stays the nightly
  run's call, exactly as for `mix`.

Give the lane `privacy: public` if visitors are to see it. Scoping it to one
account is not your job — see the note under [`auto_enrol`](#auto_enrol).
Pointing `popular_playlists` at its plid is a compose change, not a setting
here.

See [the shared rules](#rules-the-two-compiled-policies-share) for the two
things `mix` and `consensus` both do differently from a `refill` lane.

#### Rules the two compiled policies share

`mix` and `consensus` both build their content out of other lanes rather than
choosing it, and two rules follow from that.

**A rebuild that would empty the lane is refused.** It keeps what it holds and
records the reason on the run row instead, so the lane counts as failed and
`run` exits non-zero. What reaches this point is a draw that came back empty
from sources that exist: every source empty, or everything they offered dropped
by the blocklist, or — for a `mix`, which also filters per viewer — every
candidate already watched. A source lane with no playlist at all aborts earlier
and by name. A lane with `size: 0` is exempt, because that asks for empty.

**`dedupe` leaves them alone.** A compiled lane holds copies of its sources on
purpose, so its copy is not a duplicate. Dropping it took the video out of the
visible feed while the source kept it, and for a `public` lane that is a hole in
the page.

#### `shuffle`

The reorder (`iv-suggest shuffle`). It decides what sits at the top, and may
take out a video the viewer has since watched; what comes IN is the nightly
run's job. Settable in `defaults` and per lane.

Every weight below is wall-clock, read off the gap between one reorder and the
next, so **the timer's interval is a free knob**: running it four times an hour
refreshes the page four times as often and costs a video exactly the same
fatigue per hour it sat on screen. Tune `fatigue` for how fast a lane should
tire, not for how often the timer fires.

| Key | Default | Meaning |
|---|---|---|
| `enabled` | `true` | reorder this lane at all |
| `visible` | `10` | slots that count as "on screen" for the fatigue counter |
| `fatigue` | `0.75` | score multiplier per hour already spent on screen. Toward `1.0` = calmer lane |
| `fatigue_cap` | `12` | never penalise beyond this many hours |
| `recency_boost` | `1.0` | extra weight for a freshly added video… |
| `recency_halflife` | `12.0` | …halving every N hours |
| `published_halflife_days` | `0` | halve the weight per N days since the video was **uploaded**, so an old upload takes less of the page. `0` = off |
| `published_floor` | `0.15` | the discount an ancient upload bottoms out at, so a good one can still surface |
| `unwatched_halflife_days` | `0` | halve the weight per N days the video has sat in the lane unwatched. `0` = off |
| `unwatched_floor` | `0.25` | the discount a long-ignored video bottoms out at |
| `jitter` | `0.15` | ± random factor, so equal scores order differently |
| `diversity` | `true` | no two adjacent videos from one channel |
| `rank_order` | `""` | `""` \| `score` \| `published`. Hold the lane in one fixed order instead of reordering it. For a lane whose point *is* a rank — biggest first, newest first — the weighted ranking destroys it: the stored score falls 1% per rank while fatigue alone takes 34%. A hold is not `enabled: false`: the lane is still swept for watched videos and still reports an order to a mix that sources it |
| `round_robin_top` | `true` | slot 1 is a rota, not a ranking: only the half of the lane that has waited longest is eligible, so at least half of it leads before any video returns to the top |

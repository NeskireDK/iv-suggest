# Configuration reference

Every setting, once. Two places hold configuration:

- **`/etc/iv-suggest/env`** — where this install is: database, API, paths.
  Also read from the process environment. See [env.example](../env.example).
- **`/etc/iv-suggest/lanes.yml`** — what the bot makes: lanes, who gets them,
  the blocklist. See [lanes.yml](../lanes.yml) for a working file.

The systemd units call the script directly with **no `EnvironmentFile`**, which
is why `env` is read off disk as well as from the environment. A value exported
only in your shell is missing from the nightly run — test with `env -i`.

## Environment

| Variable | Default | Meaning |
|---|---|---|
| `IV_SUGGEST_ACCOUNT` | — | **Required by `init`.** `users.email` of the account the bot belongs to. Owns the blocklist playlist; every pre-multi-user row migrates to it. With a `users:` block the other commands do not need it |
| `IV_SUGGEST_API` | `http://localhost:3000` | Invidious base URL |
| `IV_SUGGEST_COMPOSE_DIR` | `/root/docker/youtube` | directory holding Invidious's `docker-compose.yml` |
| `IV_SUGGEST_DB_SERVICE` | `invidious-db` | compose service name of Postgres |
| `IV_SUGGEST_DB_USER` | `kemal` | Postgres role |
| `IV_SUGGEST_DB_NAME` | `invidious` | Postgres database |
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
| `overrides` | `{}` | `{lane-id: {key: value}}` — lane keys changed for this account only |

An override patch merges into `shuffle`, `subscription` and `mix` key by key, so
setting one of their keys keeps the rest. Every other block, `seed` included, is
replaced whole.

### `defaults`

Any lane key, applied to every lane. A lane overrides any of them.

### `lanes`

A list. Every lane needs `id` and `title`; everything else falls back to
`defaults`, then to the built-in below.

⚠️ **A lane's `policy` decides which keys are read at all.** `last_played` reads
only `id`, `title`, `size`, `fetch_cap`, `privacy`, `min_watched`, `shuffle`,
`seed`, `dedupe_songs` and `played_decay`. `mix` reads those universals plus
`exclude_watched` and `mix`. Everything under Turnover and Candidate rules
below, plus `expand`, `filter` and the `expand`-specific keys, is **`refill`
only**. `init` and `run` name any inert key a lane sets:
`lane music-watched: policy last_played never reads ttl_days`.

| Key | Default | Meaning |
|---|---|---|
| `id` | — | **Required.** stable key for state, overrides and `--lane` |
| `title` | — | **Required.** the playlist title Invidious shows |
| `policy` | `refill` | `refill` \| `last_played` \| `mix` — see [README](../README.md#what-a-lane-is) |
| `expand` | `recommended` | where candidates come from: `recommended` \| `channel_latest` \| `subscription_feed` \| `none` |
| `size` | `30` | videos the lane holds |
| `min_watched` | `0` | skip this lane for an account with fewer watched videos than this. The gate for auto-enrolment — no state to flip, the lane appears once the history exists |
| `privacy` | `unlisted` | `unlisted` \| `public` \| `private`. Only `private` breaks `/feed/playlist/<plid>`. **Applied when the playlist is created and never again** — changing a live lane is a database edit |
| `filter` | `{}` | `{genre: X}` — the only filter key, and `refill` only. One fetch per candidate whose genre is not already cached; the loop gives up after `max(20, room × 3)` checks, which can leave a refill short |
| **Turnover** | | |
| `ttl_days` | `14` | drop an unwatched entry older than this. `0` = never |
| `refresh_per_day` | `0` | retire this many of the oldest every run, whatever the TTL says |
| `keep_min` | `0` | never let `refresh_per_day` rotate the lane below this many videos |
| `sample_pool` | `0` | pick from a weighted random draw over the top N candidates instead of the strict top. `0` or `1` = strict, as is any value when `expand: none` |
| `cooldown_days` | `60` | a dropped video is not offered again for this long |
| `watched_cooldown_days` | `365` | same, for a video that was dropped because it was watched |
| `rotate_cooldown_days` | `21` | same, for one dropped by `refresh_per_day` |
| **Candidate rules** | | |
| `exclude_watched` | `true` | drop what this account already watched |
| `exclude_subscribed` | `true` | drop channels this account subscribes to — their feed already shows them. `false` does not re-admit them under `channel_latest`, which skips subscribed channels when choosing whom to poll |
| `dedupe_across_lanes` | `true` | a video sits in one lane at a time, per account |
| `dedupe_songs` | `true` | one upload per song, across every lane. See [README](../README.md#song-identity) |
| `min_seconds` | `120` | drop anything shorter, **when the length is known** — a candidate reporting `0` seconds passes. `0` = off |
| `max_seconds` | `0` | drop anything longer. `0` = no bound; use it against compilations |
| `max_per_channel` | `2` | most entries one channel may hold. `0` = no limit |
| **Fetch budget** | | |
| `fetch_cap` | `80` | most fetches this lane may take from the run budget. `0` = uncapped |

Per-`expand` keys, ignored by the other modes:

| Key | Default | Applies to | Meaning |
|---|---|---|---|
| `seed` | `{limit: 30}` | `recommended`, `channel_latest`, `none` | see below |
| `recommend_max_age_days` | `0` | `recommended` | drop a recommendation older than this. `0` = off. Free — `recommendedVideos` carries `published` |
| `max_channels` | `12` | `channel_latest` | channels to poll per run |
| `max_age_days` | `21` | `channel_latest` | how new an upload must be |
| `subscription` | see below | `subscription_feed` | see below |
| `played_decay` | `0.99` | *policy* `last_played` | score falloff per rank in play order |

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
upstream, though: a rebuild is one DELETE and one POST per video, and Invidious
resolves each added video server-side, outside the bot's pacing.

| Key | Default | Meaning |
|---|---|---|
| `sources` | — | **Required.** list of `{lane, share}`, optionally `{user, lane, share}` or `{users: all, lane, share}` |
| `sources[].lane` | — | lane id to draw from |
| `sources[].share` | `1.0` | share of the **output**, checked at every slot, so a 10% source lands about every tenth position rather than in a block |
| `sources[].user` | the account being filled | draw from another account's copy of that lane. A named account that has no such lane aborts the mix |
| `sources[].users` | — | `all` expands to one source per managed account, splitting `share` evenly. Names nobody, so it survives enrolment; a member without that lane yet is skipped rather than aborting |
| `pure` | `0` | first N slots come from the first source alone, in its own order |

The older `{base, blend, ratio}` form is still read so a pre-2026-08-17 config
keeps working: `ratio: N` (default `2`) means N `base` per 1 `blend`.

Filtering is per **viewer**, not per source: whatever anyone contributed is
dropped if this viewer already watched it or blocked its channel.

#### `shuffle`

The hourly reorder (`iv-suggest shuffle`). Membership is the nightly run's job;
this only decides what sits at the top. Settable in `defaults` and per lane.

| Key | Default | Meaning |
|---|---|---|
| `enabled` | `true` | reorder this lane at all |
| `visible` | `10` | slots that count as "on screen" for the fatigue counter |
| `fatigue` | `0.75` | score multiplier per hour already spent on screen. Toward `1.0` = calmer lane |
| `fatigue_cap` | `12` | never penalise beyond this many hours |
| `recency_boost` | `1.0` | extra weight for a freshly added video… |
| `recency_halflife` | `12.0` | …halving every N hours |
| `jitter` | `0.15` | ± random factor, so equal scores order differently |
| `diversity` | `true` | no two adjacent videos from one channel |
| `round_robin_top` | `true` | slot 1 is a rota, not a ranking: only the half of the lane that has waited longest is eligible, so at least half of it leads before any video returns to the top |

# iv-suggest

A recommendation engine for a self-hosted [Invidious](https://github.com/iv-org/invidious)
instance. It fills server-side playlists — "lanes" — from your own watch history, so
your clients get an algorithm you control instead of none at all.

Invidious deliberately has no personalised feed. That is the right default for a
privacy frontend, but it means a self-hosted instance gives you a subscription list
and a search box and nothing else. `iv-suggest` fills the gap **without sending
anything to Google that a normal playback would not**: it reads your watch history
out of your own Postgres, asks the Invidious API for each seed's related videos, and
writes the results into ordinary Invidious playlists.

Because a lane is just a playlist, every client already supports it. Yattee,
Materialious and the Invidious web UI list them with no plugin and no sync.

## What a lane is

One playlist plus the rules that fill it. A new genre is a new block in
`lanes.yml`, not new code.

```yaml
  - id: gaming
    title: "Gaming"
    seed: {from: watched, limit: 15, scan: 300, genre: "Gaming", shuffle_window: 700}
    expand: recommended
    filter: {genre: "Gaming"}
    size: 20
    refresh_per_day: 4
    sample_pool: 30
```

Three policies, plus an hourly reorder that costs no fetches:

| `policy` | Behaviour |
|---|---|
| `refill` | score candidates, top the lane up to `size`, retire the stale |
| `last_played` | hold the N most recently *played* videos of a genre |
| `mix` | interleave other lanes — or other accounts' lanes — by share of output |

Four ways to find candidates:

| `expand` | Source | Fetch cost |
|---|---|---|
| `recommended` | each seed's `recommendedVideos` | 1 per seed |
| `channel_latest` | recent uploads of channels you watch but never subscribed to | 1 per channel, ~60 videos each |
| `subscription_feed` | the `channel_videos` table over SQL | **zero** |
| `none` | the seeds themselves, in watch order | zero |

Every key of both is in **[docs/CONFIG.md](docs/CONFIG.md)** — the one place
settings are documented.

## Requirements

- Invidious with Postgres, reachable over `docker compose exec`
- An Invidious account for the bot — an ordinary one, made the ordinary way
- Python 3.9+ with PyYAML, and the `docker` CLI, on the host running the script

`subscription_feed` lanes additionally need a patched Invidious; see
[patches/README.md](patches/README.md). The other lanes work against any instance.

## Install

```sh
install -m 700 iv-suggest /usr/local/bin/iv-suggest
install -d -m 700 /etc/iv-suggest
install -m 600 lanes.yml /etc/iv-suggest/lanes.yml
cp env.example /etc/iv-suggest/env && chmod 600 /etc/iv-suggest/env
$EDITOR /etc/iv-suggest/env          # IV_SUGGEST_ACCOUNT is the only required line

iv-suggest init                      # schema, and a session for the account
iv-suggest run --dry-run             # writes nothing, caps seeds at 10
iv-suggest run                       # create the playlists and fill them

install -m 644 systemd/* /etc/systemd/system/
systemctl enable --now iv-suggest.timer iv-suggest-shuffle.timer
```

That is the whole setup on a default install. Everything else in
`/etc/iv-suggest/env` only exists because your paths might differ.

The units read `/etc/iv-suggest/env` off disk rather than through an
`EnvironmentFile`; [docs/CONFIG.md](docs/CONFIG.md) says why that matters when
you test a change by hand.

## Commands

```
iv-suggest init [--all-users]                     schema and sessions; playlists only with --all-users
iv-suggest run [--dry-run] [--lane ID]            fill the lanes
           [--account EMAIL]
           [--seeds N] [--rate N] [--budget N]
iv-suggest shuffle [--dry-run] [--lane ID]        reorder only, no fetches
           [--account EMAIL]
iv-suggest status                                 lane sizes and recent runs
iv-suggest dedupe [--dry-run]                     one upload per song
iv-suggest views [--rate N] [--budget N]          backfill missing view counts
iv-suggest metrics                                Prometheus text, database only
```

`metrics` takes no token and makes no fetch, so it is safe to scrape often.

## More than one account

`lanes.yml` is the shared library of lanes; `auto_enrol:` takes in every account
on the instance, `users:` gives named ones something different, and a `mix` lane
sourced from `users: all` is the household feed. All three are in
[docs/CONFIG.md](docs/CONFIG.md); the reasoning is in
[docs/MULTI-USER.md](docs/MULTI-USER.md). Three things worth knowing first:

- **Without `auto_enrol:`, an account absent from `users:` is never touched.**
  Enrolment is opt-in by default, because finding a dozen playlists the bot made
  in your account is a bad first impression.
- **`min_watched` is what makes enrolling everybody safe.** A lane an account
  has too little history to fill is held back instead of created empty, and
  appears on its own once the history is there. There is no phase to switch off:
  give new accounts a `users: all` mix lane at `min_watched: 0` and they open
  onto the household's feed until their own lanes are worth having.
- **One timer, one budget.** The accounts are a loop inside one run, the fetch
  budget is divided rather than multiplied, and whoever succeeded least recently
  is served first, so an exhausted budget starves a different person each night.
  The metadata cache is shared, so overlapping taste is nearly free.

## Blocking a channel

Some channel the recommendation graph loves and you do not. Open any of its
videos in any client and use **add to playlist → Blocked**. That is the whole
interface. One video is enough — `playlist_videos` records the `ucid` of every
entry, so the block lands on the *channel*, and the entry can stay in the
playlist as the record of why.

Doing it this way rather than as a config list buys three things. The playlist
is **server side**, so the list is the same on the phone, the TV and the web,
where a client-side content filter is per device. It needs **no new endpoint and
no client change**, because "add to playlist" is already in every client's menu.
And the bot reads it over SQL, so it costs **no API call and no YouTube fetch**.

A blocked channel is refused at four points: dropped as a seed, skipped by
`channel_latest`, subtracted from the `subscription_feed` channel set, and
rejected as a candidate. Anything of theirs already sitting in a lane is swept
out on the next run, logged as `- blocked`, and gets **no cooldown row** — so
removing the video from `Blocked` lets the channel back the same night.

`iv-suggest status` prints the current list and `iv_suggest_blocked_channels`
exports it. A lane playlist is excluded by id, so naming a lane `Blocked` cannot
make the lane feed itself back as its own blocklist.

## Design notes worth reading before you tune it

**Score is frequency across seeds, not view count.** A candidate scores
`sum(0.97 ** seed_rank)` over the seeds that recommend it. Popularity is
deliberately ignored — that is what makes the lane yours rather than YouTube's.

**A lane goes static unless you force turnover.** A slot frees up only when a
video is watched (which needs a client that reports playback) or when the TTL
fires — and the TTL fires for the whole lane on one night, because the whole lane
was filled on one night. `refresh_per_day` retires the oldest few every run and
spreads the ages out; `sample_pool` draws from a weighted random sample rather
than the strict top, because the score order barely moves between runs.

**But `refresh_per_day` is wrong for a window-bounded lane.** A
`subscription_feed` lane's candidate pool is capped by `max_age_hours`, so
retiring N a night into a 21-day `rotate_cooldown_days` benches more videos than
the window can supply and empties the lane in under a week. Set it to 0 there —
the age window *is* the turnover.

**Size a lane to the real supply, not a round number.** A live-streams lane at
size 20 filled to 3 on a 75-channel instance, because only 35 of those channels
ever stream.

**The hourly shuffle is a permutation, nothing else.** `playlists.index` *is* the
display order in Invidious, so reordering a lane is one SQL `UPDATE` on a
`bigint[]` — no delete, no re-add, no API call. It is race-safe against the
nightly run because it permutes whatever the array holds at write time. The
ranking discounts what has already been on screen and treats slot 1 as a rota
rather than a ranking, so at least half the lane leads before any video returns
to the top.

**Rate limiting toward YouTube is the main constraint.** Invidious's `videos`
cache is unlogged and short-lived, so assume every `/api/v1/videos/<id>` reaches
Google. Metadata is therefore cached permanently in `suggest.video_meta`, failures
are cached too (a 404 for a year, a 5xx for 30 days), fetches are paced at 20/min
with jitter, and there is a per-run budget plus a per-lane `fetch_cap` so one lane
cannot eat the night. A warm three-lane run costs ~39 fetches against ~161 cache
hits.

**Writes are not transactional, on purpose.** Invidious has no bulk playlist
endpoint, so each change is one API call plus its state write. A run that stops
halfway keeps what it applied and the next run refills, because `room = size -
kept` is recomputed from the live playlist. A short lane beats an empty one.

**Lanes are unlisted by default.** Invidious ignores `privacy` on playlist create
and always stores `Public`; only `PATCH /api/v1/auth/playlists/<plid>` changes
it, which the engine sends straight after create. Unlisted is readable by anyone
holding the playlist ID and absent from any listing, and the Atom feed at
`/feed/playlist/<plid>` still works — `rss_playlist` only 404s a `private`
playlist, so `public` buys nothing. What a listed lane leaks is watch taste, not
credentials, but the decision is made on somebody else's behalf as soon as the
instance has more than one account.

**The hourly reorder has its own tuning.** `shuffle:` is a block of its own in
[docs/CONFIG.md](docs/CONFIG.md); the defaults in `lanes.yml` were picked by
simulation, and the comment there says against what.

## Song identity

Music lanes collapse re-uploads of the same song. Content ID and `musicTracks`
are absent from the API, and MusicBrainz search proved useless on real titles
("stairway to heaven" → John Paul Young), so this is a local title parser:
bracketed qualifiers dropped, everything after `|` dropped, split on the dash,
~30 noise words removed (official, lyrics, remastered, 4K, live at…, OST, feat…,
a bare year), accents and articles flattened, spaces removed so "Freebird"
equals "Free Bird". The match key is the **song alone**, because a re-upload
channel replaces the artist. Two different songs sharing a title collapse; that
costs one slot and is accepted.

## Tests

```sh
python3 -m unittest discover -s tests -t tests
```

No dependencies beyond PyYAML. The schema migration is exercised against a
throwaway postgres container; those tests skip themselves when docker is not
available.

`kickstart.py` is a one-off that classifies a whole `channel_videos` backlog
without the per-tick caps — useful after a bulk import. It needs the patched
Invidious.

## Documentation

| | |
|---|---|
| [docs/CONFIG.md](docs/CONFIG.md) | every setting, once |
| [docs/MULTI-USER.md](docs/MULTI-USER.md) | why per-account lanes are shaped the way they are |
| [docs/PUBLIC-FEED.md](docs/PUBLIC-FEED.md) | TODO: a feed for logged-out visitors and brand new accounts |
| [patches/README.md](patches/README.md) | the Invidious patch `subscription_feed` needs |

## Licence

MIT. See [LICENSE](LICENSE).

Not affiliated with Invidious or YouTube.

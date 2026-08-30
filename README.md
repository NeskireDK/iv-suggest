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

Four policies:

| Policy | Behaviour |
|---|---|
| `refill` | score candidates, top the lane up to `size`, retire the stale |
| `last_played` | hold the N most recently *played* videos of a genre |
| `mix` | interleave other lanes by share of output |
| — | plus `iv-suggest shuffle`, an hourly reorder that costs no fetches |

Five ways to find candidates (`expand:`):

| Mode | Source | Fetch cost |
|---|---|---|
| `recommended` | each seed's `recommendedVideos` | 1 per seed |
| `channel_latest` | recent uploads of channels you watch but never subscribed to | 1 per channel, ~60 videos each |
| `subscription_feed` | the `channel_videos` table over SQL | **zero** |
| `none` | the seeds themselves, in watch order | zero |

## Design notes worth reading before you tune it

**Score is frequency across seeds, not view count.** A candidate scores
`sum(0.97 ** seed_rank)` over the seeds that recommend it. Popularity is
deliberately ignored — that is what makes the lane yours rather than YouTube's.

**A lane goes static unless you force turnover.** A slot frees up only when a
video is watched (which needs a client that reports playback) or when the TTL
fires — and the TTL fires for the whole lane on one night, because the whole lane
was filled on one night. `refresh_per_day: N` retires the N oldest every run and
spreads the ages out. `sample_pool: N` draws from a weighted random sample over
the top N rather than the strict top, because the score order barely moves
between runs.

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
ranking applies a fatigue discount (an hour on screen without a play is a small
negative, which decays back once the video drops off screen) and treats slot 1 as
a rota rather than a ranking, so at least half the lane leads before any video
returns to the top.

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

## Requirements

- Invidious with Postgres, reachable over `docker compose exec`
- An Invidious account, and an API token for it (`Authorization: Bearer {"session":…}` — the **raw JSON**; base64 returns 403)
- Python 3.9+ with PyYAML
- `docker` CLI on the host running the script

### The `subscription_feed` lanes need a patched Invidious

Those lanes filter on `channel_videos.kind` (`video` / `short` / `live`), which
**stock Invidious does not have**. Upstream records no content type for a feed
entry at all: `ChannelVideo#to_json` reports `"type": "shortVideo"` for every row
regardless, and `length_seconds` is 0 for anything absent from the channel's
Videos tab, so Shorts and stream VODs are indistinguishable from each other and
from ordinary uploads.

The other lanes work against any Invidious instance. To get the
subscription-feed ones, apply
[`patches/0001-feed-kinds-v2.20260804.1.patch`](patches/) — it adds the column
and a job that fills it from YouTube's per-channel uploads playlists (replace the
`UC` of a channel ID with `UULF` for long-form, `UUSH` for Shorts, `UULV` for
live). Measured over 75 channels, that misclassified 1 video in 679;
`HEAD /shorts/<id>` is the per-video fallback (200 = Short, 303 = not). The patch
also lets you drop Shorts and live streams out of the subscription feed
altogether. See [patches/README.md](patches/README.md) — **note the migration
warning there**, since Invidious does not run migrations at boot.

## Install

```sh
install -m 700 iv-suggest /usr/local/bin/iv-suggest
install -d -m 700 /etc/iv-suggest
install -m 600 lanes.yml /etc/iv-suggest/lanes.yml
cp env.example /etc/iv-suggest/env && chmod 600 /etc/iv-suggest/env
$EDITOR /etc/iv-suggest/env          # token + account, at minimum

iv-suggest init                      # create the schema
iv-suggest run --dry-run             # writes nothing, caps seeds at 10
iv-suggest run                       # create the playlists and fill them

install -m 644 systemd/* /etc/systemd/system/
systemctl enable --now iv-suggest.timer iv-suggest-shuffle.timer
```

The systemd units call the script directly with **no `EnvironmentFile`**, so
settings are read from `/etc/iv-suggest/env` as well as the environment. A value
set only in your shell will be missing from the nightly run.

## Configuration

Everything deployment-specific comes from the environment or `/etc/iv-suggest/env`:

| Variable | Default | Meaning |
|---|---|---|
| `IV_SUGGEST_TOKEN` | — | Invidious API token, raw JSON. **Required** |
| `IV_SUGGEST_ACCOUNT` | — | account whose playlists are filled (`users.email`). **Required** |
| `IV_SUGGEST_API` | `http://localhost:3000` | Invidious base URL |
| `IV_SUGGEST_COMPOSE_DIR` | `/root/docker/youtube` | directory with Invidious's `docker-compose.yml` |
| `IV_SUGGEST_DB_SERVICE` | `invidious-db` | compose service name of Postgres |
| `IV_SUGGEST_DB_USER` | `kemal` | Postgres role |
| `IV_SUGGEST_DB_NAME` | `invidious` | Postgres database |
| `IV_SUGGEST_CONFIG` | `/etc/iv-suggest/lanes.yml` | lane config |
| `IV_SUGGEST_ENVFILE` | `/etc/iv-suggest/env` | file the above are also read from |

## Commands

```
iv-suggest init                                   create the schema
iv-suggest run [--dry-run] [--lane ID]            fill the lanes
           [--seeds N] [--rate N] [--budget N]
iv-suggest shuffle [--dry-run] [--lane ID]        reorder only, no fetches
iv-suggest status                                 lane sizes and recent runs
iv-suggest dedupe [--dry-run]                     one upload per song
iv-suggest views                                  backfill missing view counts
iv-suggest metrics                                Prometheus text, database only
```

`metrics` takes no token and makes no fetch, so it is safe to scrape often.

`kickstart.py` is a one-off that classifies a whole `channel_videos` backlog
without the per-tick caps — useful after a bulk import. It needs the patched
Invidious described above.

## Playlist privacy

Invidious **ignores `privacy` on playlist create** and always stores `Public`;
only `PATCH /api/v1/auth/playlists/<plid>` changes it, which the engine sends
straight after create. A `public` lane is readable by anyone holding the playlist
ID and enables the Atom feed at `/feed/playlist/<plid>`; a `private` lane returns
404 there. Choose per lane in `lanes.yml`. What a public lane leaks is watch
taste, not credentials — but decide deliberately.

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

## Licence

MIT. See [LICENSE](LICENSE).

Not affiliated with Invidious or YouTube.

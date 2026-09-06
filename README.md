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
    seed: {limit: 15, scan: 300, genre: "Gaming", shuffle_window: 700}
    expand: recommended
    filter: {genre: "Gaming"}
    size: 20
    refresh_per_day: 4
    sample_pool: 30
```

Four policies, plus an hourly reorder that costs no fetches:

| `policy` | Behaviour |
|---|---|
| `refill` | score candidates, top the lane up to `size`, retire the stale |
| `last_played` | hold the N most recently *played* videos of a genre |
| `mix` | interleave other lanes — or other accounts' lanes — by share of output |
| `consensus` | one feed compiled from every account's mix, weighted by how many of them hold a video and redrawn every hour |

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

Beside Invidious, as one more compose service. It joins the compose network, so
it reaches Postgres and the API by service name and needs **no Docker socket**.

```sh
cd /path/to/your/invidious/compose          # where docker-compose.yml lives
cat /path/to/iv-suggest/compose.iv-suggest.yml   # paste the service in
cp /path/to/iv-suggest/lanes.yml ./lanes.yml

echo "IV_SUGGEST_ACCOUNT=you@example.com" >> .env   # the users.email value
                                            # IV_PG_PASSWORD is already there

docker compose run --rm iv-suggest init     # schema, session and playlists
docker compose run --rm iv-suggest run --dry-run   # writes nothing
docker compose run --rm iv-suggest run      # fill the lanes

install -m 644 systemd/* /etc/systemd/system/
$EDITOR /etc/systemd/system/iv-suggest.service     # WorkingDirectory, if not
                                                   # /root/docker/youtube
systemctl enable --now iv-suggest.timer iv-suggest-shuffle.timer \
                       iv-suggest-harvest.timer
```

One config file, two variables, no unit for the engine itself. The timers run
`docker compose run --rm`, so `systemctl list-timers` still shows the schedule
and a bad night is debuggable the way any other unit is. The compose directory
lives in the unit rather than in the engine — the engine is portable, the
schedule is local.

**Do not give the service a Watchtower label.** If your compose uses Watchtower
with `--label-enable`, an update pulled mid-run would swap the image underneath
a fill; the shipped fragment says so where it matters.

<details><summary>Running the script directly instead</summary>

It is one file with no dependency beyond PyYAML and the `psql` client, so it
still runs as a plain script. It needs a host and port that resolve from
wherever you put it — a compose service name will not, since Invidious does not
publish 5432.

```sh
install -m 700 iv-suggest /usr/local/bin/iv-suggest
install -d -m 700 /etc/iv-suggest
install -m 600 lanes.yml /etc/iv-suggest/lanes.yml
cp env.example /etc/iv-suggest/env && chmod 600 /etc/iv-suggest/env
$EDITOR /etc/iv-suggest/env    # account, password, and a reachable DB host
```

Note what that costs: a second plaintext copy of the Invidious database password
on the host, in a file the container path does not need and does not create.

`env` is read off disk as well as from the environment;
[docs/CONFIG.md](docs/CONFIG.md) says why that matters when you test by hand.

</details>

## Commands

```
iv-suggest init [--all-users]                     schema, sessions, playlists
iv-suggest run [--dry-run] [--lane ID]            fill the lanes
           [--account EMAIL]
           [--seeds N] [--rate N] [--budget N]
iv-suggest shuffle [--dry-run] [--lane ID]        reorder only, no fetches
           [--account EMAIL]
iv-suggest status [--account EMAIL]               lane sizes and recent runs
iv-suggest dedupe [--dry-run] [--account EMAIL]   one upload per song
iv-suggest views [--rate N] [--budget N]          backfill missing view counts
           [--account EMAIL]
iv-suggest metrics                                Prometheus text, database only
iv-suggest harvest-plays [--dry-run]              mark what somebody opened as
                                                  watched
iv-suggest sid-check                              did the logins survive the
                                                  nightly restart
```

`harvest-plays` exists because Yattee reports no play. It syncs subscriptions
and playlists with Invidious but never calls
`POST /api/v1/auth/history/:id`, so a video watched on an Apple TV never reaches
`users.watched` and every lane keeps offering it back. The only server side
trace of that playback is Invidious refreshing its own `videos` cache row, which
it does on any metadata fetch more than ten minutes stale; this command reads
those refreshes and marks the video watched.

It has to tell a person's open from this engine's own writes, and there are more
of those than the obvious one. `insert_video_into_playlist` reaches `get_video`
too, so **every candidate a fill adds to a lane rewrites the same row a playback
does** — about 60 a night on top of 160 metadata fetches. Marking those watched
would have the next fill retire its own candidates with a 365-day cooldown.

So every call the engine makes goes through `bot_api`, which records the video
into `suggest.bot_touches` before the call. The harvest treats an open as a
person's only when no touch lands within a minute of the refresh. A play that
coincides with an engine write to the same video inside that minute is skipped,
which is the safe direction: miss a real play rather than invent one. A static
test holds `api` to having exactly one caller, so a new call site cannot quietly
start reading as somebody watching.

`bot_touches` keeps **one row per touch**, not one per video. A lane fetches a
video's metadata and adds it to the playlist minutes later, and that second call
finds the cache row too fresh to rewrite — so one `updated` stands against two
touches at different moments, and keeping only the newest would leave a gap the
harvest would read as somebody watching.

Its watermark is **the last run**, not the newest judged open. A night whose
only refreshes were the engine's own judges nothing, and a watermark read off
the judged rows would reset to now on every quiet run and never see anything
again. A first run starts from now rather than reaching back over the cache
lifetime, because the fill that ran before the upgrade left refreshes with no
touch beside them.

Every open it judges is written to `suggest.plays`, skipped ones included, and
that log is also the watermark. Invidious deletes a cache row six hours after
the last refresh, so the timer has to run more often than that or opens are lost
with no trace anywhere; the shipped one runs twice an hour. It exits 1 when the
history API refused an open — 409 is the account's own `watch_history`
preference being off.

Four series follow it. `iv_suggest_plays_judged_24h{outcome=}` and
`iv_suggest_plays_logged{outcome=}` split every open into `watched`, `bot` or
`refused`; a rising `bot` share against a flat `watched` count is the separation
drifting, which is the thing to watch as the fill's fetch volume changes.

`iv_suggest_last_play_harvest_timestamp_seconds` is the one to alert on —
`time() - it > 6h` means the harvest has stopped and Invidious is deleting opens
before it reads them. It dates the *run*, so it does not go quiet just because
nobody watched anything; `iv_suggest_last_judged_open_timestamp_seconds` is the
one that does, which is why it is not the liveness signal. Both read 0 for
"never", so the staleness expression fires rather than looking healthy.

`sid-check` exits 0 when every login survived, 1 when one did not, and **2 when
it cannot tell** — no recorded nightly, or one too old to be evidence. Two is
not a pass and not a loss; it means the check is not watching anything, which is
the state worth knowing about.

`metrics` needs no session and makes no fetch, so it is safe to scrape often.

`views` exists because the feed reads its numbers out of `suggest.video_meta`.
A candidate that only ever arrived through `recommendedVideos` or
`channels/latest` carries no numeric `viewCount` — only text like `154K` — so
without the backfill it would show 0 for ever. The text form is parsed as a free
fallback when a listing is stored, and this command refreshes the rest.

## What it records, and how to ask

`suggest.fetches` holds **one row per call the engine makes**: when, which
account, which lane, which job, what sort, what it was about, the HTTP status,
the attempt number, and any error the answer carried. `job` is the subcommand, which is what tells the nightly
fill's traffic from a `views` somebody started by hand. `lane` is set by the
fill and left empty by every other job, `dedupe` included: only the fill's
dispatcher starts a lane. `views` leaves **`account` empty too**, because the
set it walks is de-duplicated across accounts — one fetch serves everybody, and
naming whichever account the collection happened to serve last would charge one
person for the household. It is the only place a request is dated — `suggest.runs` carries
a count per lane per night and nothing finer, so before this table "how many
requests reached YouTube in that hour, for whom, of what sort" had no answer.

`upstream` marks the calls that **can** reach YouTube, and it is a column rather
than something each query works out from paths. `video` and `channel_latest`
always do: the video cache is short lived and a channel listing is not cached at
all. `playlist_add` can, because adding a video reaches `get_video` too — it
goes out whenever that cache row has gone stale, which is the normal case for a
compiled `mix` or `consensus` lane adding a video no lane fetched recently, and
not the case for a fill adding a candidate it fetched a minute ago. So
`WHERE upstream` is an upper bound; `WHERE kind IN ('video','channel_latest')`
is the calls made *in order to* fetch. A playlist read, delete or create never
leaves the machine.

Rows are dated when the call happened, not when they are written — a `views`
batch spans ten minutes at the default pacing and flushes in one statement, so
the flush time would misdate every row in it. A **dry run is logged like any
other**: it makes the same real calls, and `run --dry-run` already fills the
metadata cache, so withholding these would hide requests that genuinely
happened.

```sql
-- requests to YouTube per hour, by account and sort
SELECT date_trunc('hour', at) AS hour, account, kind, count(*)
FROM suggest.fetches WHERE upstream
GROUP BY 1, 2, 3 ORDER BY 1 DESC;

-- what each lane of the nightly fill cost, and what it was on
SELECT lane, kind, count(*) FROM suggest.fetches
WHERE upstream AND job = 'run' AND at > now() - interval '7 days'
GROUP BY 1, 2 ORDER BY 3 DESC;

-- what the retries and the refusals cost, by sort of non-answer
SELECT date_trunc('day', at) AS day, status, left(error, 40) AS answered,
       count(*)
FROM suggest.fetches
WHERE upstream AND (status <> 200 OR coalesce(error, '') <> '')
GROUP BY 1, 2, 3 ORDER BY 1 DESC;

-- the cache hit ratio per night, which the run used to only print
SELECT date_trunc('day', started) AS day,
       sum(cache_hits) AS answered, sum(lookups) AS asked,
       round(sum(cache_hits)::numeric / nullif(sum(lookups), 0), 3) AS ratio
FROM suggest.runs GROUP BY 1 ORDER BY 1 DESC;
```

Rows are buffered in memory and written once per lane, so a night of 200 calls
costs 16 statements rather than 200. A crash mid-lane loses that lane's rows,
which is the right trade for a log: never fail a fill to record one, and a
failed write warns rather than raising. A command with no lane loop — `views` is
the one that makes real numbers of calls — flushes every `FETCH_LOG_BATCH` rows
instead, so a timeout kill cannot take a whole run's log with it.

Three metrics carry the same numbers into Prometheus for alerting:
`iv_suggest_upstream_fetches_24h{kind}`,
`iv_suggest_upstream_failures_24h{class}` and
`iv_suggest_cache_hit_ratio_24h`.

`rate_limited` rising means back off. `answered_an_error` is the one worth
knowing about: Invidious answers **200 with an error in the body** when it cannot
parse a video, and `Fetcher.video` buries that video for 30 days on the strength
of it. Counted as a clean call, a degraded night spends the whole fetch budget
and blacklists real videos while every failure count sits at zero. A body that
cannot be read at all is recorded as a 200 with an error too, because upstream
did answer — the body was the problem. A call nothing answered keeps its reason
in the same column, since the journal that used to hold it is thrown away with
the container every night.

That last one is `cache_hits / lookups`, both counted at the point of lookup.
It is deliberately not built on `fetches`, which counts channel listings and
every retry attempt — a rate-limited night would move a ratio built on that
while the cache did nothing different. A genre lane asks twice about one video,
once for its channel and again for its genre; both are real lookups and both
count, so the number stays comparable between a genre lane and a plain one. It
is not a per-candidate hit rate.

All three are 24-hour gauges recomputed at scrape time, so use the SQL above for
anything that needs a time or a window longer than Prometheus keeps.

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
  Every lane still runs whatever the budget did — `mix` and `consensus` read
  their sources over SQL and never wanted a fetch, so a heavy night still
  rebuilds the feeds.
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
display order in Invidious — the feed reads `ORDER BY array_position(index, ...)`
on every request — so reordering a lane is one SQL `UPDATE` on a `bigint[]`: no
delete, no re-add, no API call, and the client-visible `indexId` in
`playlist_videos.index` never moves. It is race-safe against the nightly run
because it permutes whatever the array holds at write time, so a video added
between the read and the write sorts last and survives. The ranking discounts
what has already been on screen and treats slot 1 as a rota rather than a
ranking, so at least half the lane leads before any video returns to the top.

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
channel replaces the artist ("American Pie Song" by "Hit Usa songs"). Two
different songs sharing a title collapse; that costs one slot and is accepted.

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
| [docs/PUBLIC-FEED.md](docs/PUBLIC-FEED.md) | why the compiled feeds for logged-out visitors and new accounts are shaped the way they are |
| [docs/SYNTHETIC-HARNESS.md](docs/SYNTHETIC-HARNESS.md) | the throwaway-Postgres harness, and why the fast suite keeps its own doubles |
| [patches/README.md](patches/README.md) | the Invidious patch `subscription_feed` needs |

## Licence

MIT. See [LICENSE](LICENSE).

Not affiliated with Invidious or YouTube.

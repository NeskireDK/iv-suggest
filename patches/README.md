# Invidious patch: content kinds for subscription feed entries

`0001-feed-kinds-v2.20260804.1.patch` adds a `kind` column (`video` / `short` /
`live`) to `channel_videos` and a job that fills it, which is what the
`subscription_feed` lanes in `lanes.yml` filter on. It also lets you drop Shorts
and live streams out of the subscription feed entirely, via a `feed_kinds`
config setting.

**You only need this for `subs-top48`, `subs-shorts` and `subs-live`.** The other
nine lanes work against stock Invidious.

Based on **v2.20260804.1**. Verified to apply cleanly to a pristine checkout of
that tag.

## Why a patch and not a setting

Invidious records no content type for a feed entry:

- `ChannelVideo#to_json` reports `"type": "shortVideo"` for **every** row. That
  string is legacy naming for "abbreviated video object" and says nothing about
  Shorts, so no client can distinguish them.
- The real `VideoType` enum (`Video` / `Livestream` / `Scheduled`) exists only on
  the full watch-page object, never on a listing — and has no Short member.
- Length cannot stand in. `channel_videos.length_seconds` is only populated for
  entries that also appeared in the channel's *Videos* tab, so Shorts **and**
  past-stream VODs both arrive as `0`. On a 75-channel instance that was 531 of
  1586 rows (34%), containing 23-second Shorts and an 18199-second stream alike.

Upstream has declined this feature class repeatedly — iv-org/invidious
[#2585](https://github.com/iv-org/invidious/issues/2585),
[#3541](https://github.com/iv-org/invidious/issues/3541),
[#3920](https://github.com/iv-org/invidious/issues/3920),
[#4457](https://github.com/iv-org/invidious/issues/4457),
[#5485](https://github.com/iv-org/invidious/issues/5485).

## Where the signal comes from

YouTube publishes a per-channel uploads playlist per content kind. Replace the
`UC` of a channel ID with a prefix and read it as RSS:

| Prefix | Contents |
|---|---|
| `UULF…` | long-form uploads |
| `UUSH…` | Shorts |
| `UULV…` | live streams |

```
https://www.youtube.com/feeds/videos.xml?playlist_id=UUSH<channel-id-minus-UC>
```

Measured over 75 subscribed channels, 15 newest entries each: 1114 long-form,
679 Shorts, 299 live, **one** Short leaking into a `UULF` feed, and
`UULF ∩ UULV` empty. That is YouTube's own classification, so it beats any
heuristic.

For rows older than a 15-entry feed window there is a per-video fallback:
`HEAD https://www.youtube.com/shorts/<id>` answers **200** for a Short and
**303** for anything else. Verified identical for HEAD and GET.

## Applying it

```sh
git clone https://github.com/iv-org/invidious
cd invidious
git checkout v2.20260804.1
git apply ../iv-suggest/patches/0001-feed-kinds-v2.20260804.1.patch
# or, to keep authorship:
git am  ../iv-suggest/patches/0001-feed-kinds-v2.20260804.1.patch
```

Then build as usual, and **run the migration** — see the warning below.

Config:

```yaml
feed_kinds:
  - video          # drop "short" and "live" from the subscription feed
```

Leave `feed_kinds` unset or empty and nothing is filtered; the column still gets
populated, which is all the `subscription_feed` lanes need.

## ⚠️ Migrations do not run at boot

Invidious runs migrations only via `--migrate`, which then exits. Nothing in the
stock compose stack calls it, so a build carrying this patch will start with no
`kind` column and every `RefreshChannelsJob` insert will fail with *"INSERT has
more expressions than target columns"*.

```sh
docker compose run --rm --no-deps invidious /invidious/invidious --migrate
docker compose up -d invidious
```

`check_tables: true` does **not** save you. `Database.check_table` looks up each
column's definition with a regex matching `CREATE TABLE public.<table>\n(`,
while every `config/sql/*.sql` in the tree says
`CREATE TABLE IF NOT EXISTS public.` — the match fails, `column_types` is nil,
and the function returns early. Its auto-add-column path is dead code.

## Design notes

**The job is separate from `fetch_channel` on purpose.** That function is one of
the most frequently changed upstream, so putting classification there guarantees
conflicts on every rebase. Upstream keeps inserting rows unchanged; the job
corrects them afterwards.

**`kind` is absent from the insert's `ON CONFLICT DO UPDATE` set.** A channel
refresh re-inserts every row it reads, so including it would wipe the label on
every pass. This way the upsert physically cannot overwrite a classification.

**Two passes.** The window pass reads a channel's Shorts and live feeds and
labels everything they carry; anything else that channel owes, newer than the
point *both* windows reached back to, is long-form by elimination. A channel that
posts neither kind 404s both playlists and is closed out in one tick. The tail
pass probes `/shorts/<id>` for rows older than those windows, 60 per tick.

**Failing open is deliberate.** An unclassified row is shown, and an empty
`feed_kinds` admits everything. A late or broken job costs a few Shorts slipping
through for one interval; it can never blank someone's subscription feed. The
reported `type` follows the same rule — unclassified reads as `video`, not
`shortVideo`.

**Views are swapped, never dropped first.** The feed restriction is baked into
each user's materialized view at CREATE time (`REFRESH` re-runs the stored
definition, so it cannot pick up a changed setting). The job therefore builds
each view beside the live one and swaps with a transactional `DROP` + `RENAME`.
Dropping first cost both subscription feeds on the instance this was developed
on, when the column the new definition referenced did not yet exist.

**Materialized views are absent from `information_schema`.** The staleness check
uses `pg_class` / `pg_attribute` with `relkind = 'm'`. A check against
`information_schema.columns` returns zero rows forever and passes silently —
verified on a live instance. It also honours the 63-character identifier
truncation Postgres applies to these view names, which are 77 characters long.

## What it does not cover

Search results and channel pages still list Shorts. This is the subscription
feed only.

## Not upstreamed

This has not been submitted to iv-org. It is published so the
`subscription_feed` lanes are reproducible, not as a proposal. If you want to
take it upstream yourself, please do.

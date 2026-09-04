# Running iv-suggest as a container

Why: a fresh install was a binary, a config directory and four systemd units. It
is one compose service, two variables and a mounted `lanes.yml`. The engine also
stops needing to live on the Invidious host.

**It is a privilege reduction, not just packaging.** `_psql` used to shell out
through the Docker CLI, which in a container means mounting the Docker socket —
root-equivalent on the host, for a playlist filler. It now connects to Postgres
over the compose network, so the engine goes from *can run Docker on the host*
to *knows the Postgres password* — the same password Invidious's own compose
already holds.

## What changed in the engine

| before | after |
|---|---|
| `docker compose exec -T invidious-db psql …` | `psql -h invidious-db -p 5432 …` |
| `IV_SUGGEST_COMPOSE_DIR`, `IV_SUGGEST_DB_SERVICE` | `IV_SUGGEST_DB_HOST`, `IV_SUGGEST_DB_PORT`, `IV_SUGGEST_DB_PASSWORD` |
| `iv-sid-check`, a host script | `iv-suggest sid-check`, with tests |

`sid-check` also gained a bound the shell script did not have. It reads the
nightly's finish time from `suggest.host_events` instead of from systemd, and
that row only moves if the nightly's last command succeeds — so a nightly that
has been failing for a week would leave a week-old boundary, every session would
predate it, and every account would read as a survivor. A boundary older than 26
hours is treated as no evidence at all: exit 2, not 0.

The psql *client* stays. `query`, `one` and `execute` parse the CSV it prints,
so keeping it left all three byte-identical; a driver would have rewritten them
and added a build dependency for nothing.

The password goes in the environment, never on the command line — a command line
is readable by every process on the box. `-w` is passed so a missing password
fails immediately instead of waiting on a prompt no nightly run will ever
answer, and `-X` so no `.psqlrc` can rewrite what `query` parses.

Only psql's **first line** of complaint is kept. The lines after it echo the
failing statement, and `open_session` interpolates a live session id into an
INSERT — so a failure there used to put that id in the journal and in
`suggest.runs.error`. The environment psql gets is an allowlist rather than a
copy, because `PGOPTIONS` can set a `search_path` and make unqualified reads
answer from another schema with exit 0.

## Deploy order

Do them in this order. They are **not** independent: step 3 writes to a table
step 2 creates, and step 5 must not run before step 4.

**Do not `install` this version of the engine to `/usr/local/bin` during the
migration.** Every previous deploy did. This one cannot reach the database from
the host — Invidious publishes no port for it — so installing it would break
`metrics` and raise `iv_suggest_up 0`.

1. **Build and push.** A merge to `main` publishes
   `ghcr.io/neskiredk/iv-suggest:<sha>` after the tests pass. There is no
   `:latest`, so nothing deploys itself.
2. **Add the compose service.** Paste the `iv-suggest:` block from
   `compose.iv-suggest.yml` into `/root/docker/youtube/docker-compose.yml` --
   the timers run `docker compose` in that directory and read that one file,
   so a second file would need a `COMPOSE_FILE` or an `include:` to be seen at
   all. Put `lanes.yml` beside `docker-compose.yml` **world-readable** — the
   container runs as uid 10001, and the file holds no secret:

   ```sh
   install -m 644 lanes.yml /root/docker/youtube/lanes.yml
   cat >> .env <<'EOT'
   IV_SUGGEST_ACCOUNT=you@example.com
   IV_SUGGEST_IMAGE_TAG=<the sha from step 1>
   EOT
   docker compose pull iv-suggest
   docker compose run --rm -T --no-deps iv-suggest init
   ```

   `pull` matters: compose defaults to `pull_policy: missing`, so without it
   `run` uses whatever image is already cached. Safe to repeat — the schema is
   `CREATE … IF NOT EXISTS` throughout and a session is minted only when the
   account has none.
3. **Have the nightly record its finish.** Append to `iv-nightly.sh`, after the
   recreate. The `|| true` is not optional: the script runs `set -euo pipefail`,
   and without it a psql failure here would fail `iv-nightly.service` *after* a
   successful backup and restart.

   ```sh
   docker compose exec -T invidious-db psql -U kemal -d invidious -q -c \
     "INSERT INTO suggest.host_events(event, at) VALUES ('nightly-recreate', now())
      ON CONFLICT (event) DO UPDATE SET at = EXCLUDED.at;" || true
   ```

   Until this lands, `sid-check` exits **2** and says it cannot judge. Two is
   not a failure and not a pass.
4. **Repoint the timers.** `install -m 644 systemd/* /etc/systemd/system/`,
   check each `WorkingDirectory`, `systemctl daemon-reload`, then
   `systemctl enable --now iv-suggest-sid-check.timer` and disable the old
   `iv-sid-check.timer`. The shipped units carry three things that are load
   bearing, each explained where it sits: a fixed `--name`, an `ExecStopPost`
   removal, and `--no-deps`.
5. **Repoint the metrics line.** In `iv-stats-json.sh`, one line:

   ```diff
   -  /usr/local/bin/iv-suggest metrics 2>/dev/null || echo "iv_suggest_up 0"
   +  docker compose run --rm -T --no-deps iv-suggest metrics 2>/dev/null || echo "iv_suggest_up 0"
   ```

   That is all Phase 2 needed. The script already appended the engine's own
   metrics into the file it serves, so there is no mount, no second scrape
   target, one port, the same metric names, and no alert rule to change. The
   `ariktube_*` series in that script need the Docker daemon and the GitHub API
   and stay on the host, which is why serving `/metrics` from the container
   would have *split* the scrape rather than removed the host job.

   `--no-deps` is not cosmetic here: this runs every 15 minutes as root, and
   without it a `.env` edit would make the next tick recreate Postgres under a
   live instance.
6. **Clean up**, once a real fill has gone through the container:
   `/usr/local/bin/iv-suggest` and its `.bak` pile, `/usr/local/bin/iv-sid-check`
   and its two units, and `/etc/iv-suggest/` entirely — `lanes.yml` there is now
   a decoy that nothing reads, and `env` holds nothing the compose `.env` does
   not. Check `env` before deleting it rather than assuming what is in it: on
   the reference install it held only `IV_SUGGEST_ACCOUNT`, because the old
   transport authenticated by being inside the database container and never
   needed a password at all.

## What the first deploy actually needed

Recorded from the reference install (LXC 109, 2026-09-04), because three of
these are not in the steps above and each cost a retry:

- **The compose file already ends in `volumes:`**, so the service block goes
  before that, not at the end of the file. `docker compose config --quiet`
  validates without printing a single interpolated secret.
- **`docker compose exec … psql -c "…'literal'…"` loses its quotes** through
  `ssh root@host 'pct exec 109 -- sh -c "…"'`. Three layers of shell strip
  them and psql then reports a syntax error pointing at the value. Put the
  statement in a file and send the file.
- **Seed `suggest.host_events` from the real last nightly finish**, not from
  `now()`. `systemctl show -p ExecMainExitTimestamp --value iv-nightly.service`
  is the same boundary the old shell script read, so the first `sid-check`
  answers about a real nightly instead of waiting a day. Until a row exists it
  exits 2 and the unit reads as failed, which is correct and not an incident.
- **`init` without `--all-users` prints one account.** That is the whole
  schema created and the primary account confirmed; the other accounts' rows
  and sessions are already in the database and do not need reminting.

## Verifying a deploy

There is no file on the box to hash. Two answers have to agree:

```sh
# on 109
grep IV_SUGGEST_IMAGE_TAG /root/docker/youtube/.env
docker image inspect "ghcr.io/neskiredk/iv-suggest:$IV_SUGGEST_IMAGE_TAG" \
  --format '{{index .Config.Labels "org.opencontainers.image.revision"}}'
journalctl -u iv-suggest.service -n 50 | grep '^iv-suggest '
```

The journal line is there because a label alone says what is *pulled*, not what
*ran*. `run` and `shuffle` print the revision as their first line.

Those three agreeing prove the deploy is *consistent*, not that it is *current*.
Every push to `main` publishes a tag, docs-only pushes included, so a tag being
behind `main` does not by itself mean anything is undeployed. What settles it is
whether anything the image is built from has moved:

```sh
# on 108, in the repo -- empty output means the running tag is current
git log --oneline <deployed-tag>..main -- iv-suggest Dockerfile
```

## Rolling back

**Preferred: change the tag.** `IV_SUGGEST_IMAGE_TAG` in the compose `.env` back
to the previous sha, `docker compose pull iv-suggest`. That is why the tag is
immutable and recorded — a floating `:latest` leaves nothing to point at.

Running the script directly is **not** a usable rollback on the reference
install, and the doc used to imply it was. It needs all of:

- a Postgres client on the host — LXC 109 has none;
- a database host that resolves from outside the compose network. Invidious
  publishes no port for 5432. The bridge gateway reaches it (`172.18.0.2` today)
  but the address moves on every recreate, so this means a compose edit to
  publish `127.0.0.1:5432`;
- `IV_SUGGEST_DB_PASSWORD` in `/etc/iv-suggest/env`, which is a second plaintext
  copy of the database password on the host, and one that step 6 stopped
  collecting.

## What this shrinks

`bin/collect.sh` for LXC 109 goes from thirteen paths to three:
`docker-compose.yml`, `lanes.yml` and the sealed `.env`. The engine and its units
stop being config-as-code because the image carries them.

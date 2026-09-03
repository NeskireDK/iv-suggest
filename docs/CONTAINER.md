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

The psql *client* stays. `query`, `one` and `execute` parse the CSV it prints,
so keeping it left all three byte-identical; a driver would have rewritten them
and added a build dependency for nothing.

The password goes in the environment, never on the command line — a command line
is readable by every process on the box, and the engine puts psql's stderr into
a log line on failure. `-w` is passed so a missing password fails immediately
instead of waiting on a prompt no nightly run will ever answer.

## Deploy order

The steps are separable and each is safe on its own. Do them in this order,
because step 3 writes to a table step 2 creates.

1. **Build and push the image.** The revision label is what the deploy check
   reads afterwards, so build from a clean tree.
2. **Add the compose service** (`compose.iv-suggest.yml`), put `lanes.yml`
   beside `docker-compose.yml`, add `IV_SUGGEST_ACCOUNT` to the compose `.env`,
   then `docker compose run --rm iv-suggest init`. Safe to repeat: the schema is
   `CREATE … IF NOT EXISTS` throughout and a session is only minted when the
   account has none.
3. **Have the nightly record its finish.** Append to `iv-nightly.sh`, after the
   recreate:

   ```sh
   docker compose exec -T invidious-db psql -U kemal -d invidious -q -c \
     "INSERT INTO suggest.host_events(event, at) VALUES ('nightly-recreate', now())
      ON CONFLICT (event) DO UPDATE SET at = EXCLUDED.at;"
   ```

   Until this lands, `sid-check` exits 2 and says it cannot judge — which is the
   honest answer, not a failure.
4. **Repoint the timers.** `install -m 644 systemd/* /etc/systemd/system/`,
   check `WorkingDirectory`, `systemctl daemon-reload`. Retire
   `iv-sid-check.service`/`.timer` and `/usr/local/bin/iv-sid-check`; run
   `iv-suggest sid-check` from a timer instead if you want the 05:34 check back.
5. **Repoint the metrics line.** In `iv-stats-json.sh`, one line:

   ```diff
   -  /usr/local/bin/iv-suggest metrics 2>/dev/null || echo "iv_suggest_up 0"
   +  docker compose run --rm iv-suggest metrics 2>/dev/null || echo "iv_suggest_up 0"
   ```

   That is all Phase 2 needed. The script already appended the engine's own
   metrics into the file it serves, so there is no mount, no second scrape
   target, one port, the same metric names, and no alert rule to change. The
   `ariktube_*` series in that script need the Docker daemon and the GitHub API
   and stay on the host, which is why serving `/metrics` from the container
   would have *split* the scrape rather than removed the host job.
6. **Remove the old binary** once a real fill has gone through the container:
   `/usr/local/bin/iv-suggest` and the backup beside it.

## Verifying a deploy

The md5 check is gone, because there is no file on the box to hash. Use the
revision label, the same check the two forks already use:

```sh
git -C ~/iv-suggest-repo rev-parse HEAD
docker image inspect ghcr.io/neskiredk/iv-suggest:latest \
  --format '{{index .Config.Labels "org.opencontainers.image.revision"}}'
```

Those two must match. `docker compose run --rm iv-suggest status` then says
whether it can reach the database and what the lanes hold.

## Rolling back

Redeploy the previous image tag. If the container itself is the problem rather
than the code, the script still runs directly — see the collapsed section in the
README — but it needs a database host that resolves from outside the compose
network, and Invidious does not publish 5432.

## What this shrinks

`bin/collect.sh` for LXC 109 goes from thirteen paths to three:
`docker-compose.yml`, `lanes.yml` and the sealed `.env`. The engine and its units
stop being config-as-code because the image carries them.

# The synthetic multi-account harness

One command:

    tests/synthetic.sh

It creates a throwaway PostgreSQL, gives it the Invidious tables the engine
reads, generates a household of accounts with deliberately different
histories, runs the real `iv-suggest init` and several nights of the real
`iv-suggest run` against it, asserts on what came out, and destroys the
container. Safe to run twice: a container left behind by an interrupted run is
removed first, and the cluster lives on a tmpfs so nothing survives it. Without
Docker it prints why it is skipping and exits 0.

The same tests are picked up by the ordinary invocation, and skip cleanly
there when Docker is missing:

    python3 -m unittest discover -s tests -t tests

## Why it exists

The live instance has one account with nearly ten thousand watched videos and
one with three. Every multi-account path — the `min_watched` gate, account
scoping of `suggest.*`, the shared metadata cache, the household mix — was
therefore asserted only against hand-built dictionaries. This is the first
thing that has watched the engine fill lanes for several accounts at once.

## What is synthetic, and how you can tell

Nothing is read from a live instance and nothing could be mistaken for it:

| thing | form | example |
| --- | --- | --- |
| account | `synth-*@synthetic.invalid` | `synth-alice@synthetic.invalid` |
| video id | `SYN` + channel + index | `SYN037000012` |
| channel id | `UCSYNTH` + channel | `UCSYNTH00000000000000037` |
| playlist id | `IVPLSYNTH` + counter | `IVPLSYNTH000000004` |
| title | two nonsense words | `Synthetic upload abl aam` |

Titles carry letters rather than digits because `song_key` strips any run of
four digits, and a numbered title would collapse every video onto one key.

## The accounts, and the branch each one exists to take

| account | watched | subscriptions | why |
| --- | --- | --- | --- |
| `synth-alice` | 3000 | 40 | heavy |
| `synth-bob` | 400 | 10 | medium |
| `synth-cold` | 0 | 0 | nothing to fill from |
| `synth-thin` | 49 | 4 | just under `min_watched` |
| `synth-just-over` | 50 | 4 | just over it, otherwise identical to `synth-thin` |
| `synth-twin` | 2008 | 30 | 58 of alice's 60 channels, and her newest history |
| `synth-loner` | 600 | 12 | no channel in common with anybody |
| `synth-newcomer` | 600 | 8 | registered after the config was written |
| `synth-bot` | 0 | 0 | owns the compiled public feed and contributes no mix |

`synth-thin` and `synth-just-over` differ by one watched video and nothing
else, so a difference in their lane lists can only be the gate. `synth-twin`
shares alice's newest history on purpose, so the two seed from the same videos
— which is both what makes cross-account eviction visible and the spread a
consensus feed would have to rank on.

## What is stubbed, and what is not

Only the two transports.

`_psql` is repointed at the throwaway container, so `query`, `one`, `execute`,
`lit` and every SQL string in the engine are the real ones. It runs behind a
single long-lived `psql` because the `docker exec` handshake costs 170 ms
against the 0.7 ms a statement actually takes, and a household run issues
thousands.

`api` is repointed at `ApiStub`, which writes playlist changes through to
`playlists` and `playlist_videos`, so a lane read back over SQL sees exactly
what was added to it. It refuses a playlist belonging to another account with
403, as Invidious does, which is what turns "the mix reads its sources over
SQL" into a property rather than a habit. It counts the calls that would have
reached YouTube separately from the playlist writes, so a test can assert what
a lane cost.

The `suggest.*` schema is **not** in the fixture. `iv-suggest init` creates it,
so the fixture cannot drift away from the migration.

## Files

| file | what |
| --- | --- |
| `tests/synthetic.py` | the generator, the container, the API stub. No tests. |
| `tests/synthetic_lanes.yml` | the config the household run uses: three lanes, small and deterministic |
| `tests/test_synthetic_household.py` | the assertions about filling several accounts' lanes |
| `tests/synthetic_consensus_lanes.yml` | the config the public feed run uses: four contributors and one account owning the feed |
| `tests/test_synthetic_consensus.py` | the assertions about the compiled public feed |
| `tests/synthetic.sh` | the one command |

Each `test_synthetic_*.py` file starts and destroys a database of its own, so
the two configs cannot interfere: an account list is part of what each is
asserting.

## Overriding the image

    IV_SUGGEST_TEST_PG_IMAGE=postgres:18-alpine tests/synthetic.sh

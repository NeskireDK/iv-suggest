"""A synthetic Invidious household, for driving the real engine end to end.

Everything here is generated and says so: accounts are named `synth-*`, video
ids begin `SYN` and channel ids begin `UCSYNTH`, so no row could be mistaken
for one taken off a real instance. Nothing is read from a live database.

Only the two transports are replaced. `_psql` is repointed at a throwaway
container, so `query`, `one`, `execute`, `lit` and every SQL string in the
engine stay the real ones. `api` is repointed at ApiStub, which keeps
`playlists` and `playlist_videos` in step with what it is told -- so a lane
read back over SQL sees what the API wrote, the way Invidious behaves -- and
refuses to show one account's playlist to another account's session, which is
what makes the mix's SQL-only source path a property rather than a habit.
"""

import csv
import io
import os
import subprocess
import threading
import time
import unittest
import urllib.error
import uuid

IMAGE = os.environ.get("IV_SUGGEST_TEST_PG_IMAGE", "postgres:16-alpine")
DB_USER = "kemal"
DB_NAME = "invidious"
DOMAIN = "synthetic.invalid"
SENTINEL = "__STATEMENT_DONE__"

CHANNELS = 200
VIDEOS_PER_CHANNEL = 100
RECOMMEND_STRIDE = 37
RECOMMEND_WIDTH = 12
FEED_FIRST_INDEX = 90
FEED_UPLOADS_PER_CHANNEL = 6
GENRES = ("Music", "Gaming", "Science & Technology", "Autos & Vehicles")

# Has to stay equal to `min_watched` in tests/synthetic_lanes.yml.
MIN_WATCHED = 50

_ALPHABET = "abcdefghijklmnopqrstuvwxyz"


def word_of(number):
    """A three letter token. Titles carry these, not digits, because
    `song_key` strips any run of four digits and would collide every title."""
    return "".join(_ALPHABET[(number // 26 ** power) % 26] for power in (2, 1, 0))


def vid_of(channel, index):
    """A deterministic 11 character video id that reads as synthetic."""
    return "SYN%03d%05d" % (channel % CHANNELS, index % VIDEOS_PER_CHANNEL)


def ucid_of(channel):
    """A deterministic 24 character channel id that reads as synthetic."""
    return "UCSYNTH%017d" % (channel % CHANNELS)


def channel_of(vid):
    return int(vid[3:6])


def index_of(vid):
    return int(vid[6:])


def channel_of_ucid(ucid):
    return int(ucid[7:])


def author_of(channel):
    return "Synth Channel %s" % word_of(channel % CHANNELS)


def title_of(vid):
    return "Synthetic upload %s %s" % (word_of(channel_of(vid)),
                                       word_of(index_of(vid)))


def seconds_of(vid):
    return 180 + (index_of(vid) * 37 + channel_of(vid)) % 900


def views_of(vid):
    return 1000 + (index_of(vid) * 7919 + channel_of(vid) * 104729) % 900000


def genre_of(channel):
    return GENRES[channel % len(GENRES)]


def listing_of(vid, published):
    """One video as a recommendation or channel listing carries it: no genre."""
    channel = channel_of(vid)
    return {"videoId": vid, "title": title_of(vid), "author": author_of(channel),
            "authorId": ucid_of(channel), "lengthSeconds": seconds_of(vid),
            "viewCount": views_of(vid), "published": published}


def recommendations_of(vid):
    """The graph around one video: a fixed stride, so it spreads over channels."""
    now = int(time.time())
    return [listing_of(vid_of(channel_of(vid) + RECOMMEND_STRIDE * step,
                              index_of(vid) + step), now - 3600 * step)
            for step in range(1, RECOMMEND_WIDTH + 1)]


def video_of(vid):
    """One video as /api/v1/videos answers it. The genre is only here."""
    full = listing_of(vid, int(time.time()) - 86400)
    full["genre"] = genre_of(channel_of(vid))
    full["recommendedVideos"] = recommendations_of(vid)
    return full


class Account:
    """One synthetic account: a channel neighbourhood, a history, subscriptions.

    `tail` is somebody else's newest history appended to this one, which is how
    two accounts come to seed from the same videos: the seed picker walks the
    history newest first, so a shared tail is a shared candidate set.
    """

    def __init__(self, name, channels, watched, subscriptions=0, tail=()):
        self.name = name
        self.email = "%s@%s" % (name, DOMAIN)
        self.channels = list(channels)
        self.watched = self._history(watched)
        already = set(self.watched)
        self.watched += [vid for vid in tail if vid not in already]
        self.subscriptions = [ucid_of(c) for c in self.channels[:subscriptions]]

    def _history(self, count):
        if not self.channels:
            return []
        return [vid_of(self.channels[n % len(self.channels)],
                       n // len(self.channels)) for n in range(count)]


ALICE = Account("synth-alice", range(0, 60), 3000, subscriptions=40)
BOB = Account("synth-bob", range(60, 78), 400, subscriptions=10)
COLD = Account("synth-cold", (), 0)
THIN = Account("synth-thin", range(80, 86), MIN_WATCHED - 1, subscriptions=4)
JUST_OVER = Account("synth-just-over", range(90, 96), MIN_WATCHED, subscriptions=4)
TWIN = Account("synth-twin", range(0, 58), 2000, subscriptions=30,
               tail=ALICE.watched[-8:])
LONER = Account("synth-loner", range(150, 168), 600, subscriptions=12)
NEWCOMER = Account("synth-newcomer", range(170, 186), 600, subscriptions=8)

HOUSEHOLD = (ALICE, BOB, COLD, THIN, JUST_OVER, TWIN, LONER)

BOT = Account("synth-bot", (), 0)
CONTRIBUTORS = (ALICE, TWIN, BOB, LONER)

LANES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "synthetic_lanes.yml")
CONSENSUS_LANES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    "synthetic_consensus_lanes.yml")

# Column order and types as `\d` reports them on a live instance. Hand written
# rather than dumped, so it can hold no real row. suggest.* is absent on purpose:
# `iv-suggest init` creates that, so the fixture cannot drift from the migration.
INVIDIOUS_SQL = """
CREATE TYPE privacy AS ENUM ('Public', 'Unlisted', 'Private');
CREATE TABLE users (
  updated timestamptz, notifications text[], subscriptions text[],
  email text NOT NULL, preferences text, password text, token text,
  watched text[], feed_needs_update boolean,
  CONSTRAINT users_email_key UNIQUE (email));
CREATE UNIQUE INDEX email_unique_idx ON users (lower(email));
CREATE TABLE session_ids (
  id text NOT NULL PRIMARY KEY, email text, issued timestamptz);
CREATE TABLE playlists (
  title text, id text NOT NULL PRIMARY KEY, author text, description text,
  video_count integer, created timestamptz, updated timestamptz,
  privacy privacy, "index" bigint[]);
CREATE TABLE playlist_videos (
  title text, id text, author text, ucid text, length_seconds integer,
  published timestamptz, plid text NOT NULL REFERENCES playlists(id),
  "index" bigint NOT NULL, live_now boolean, PRIMARY KEY ("index", plid));
CREATE TABLE channel_videos (
  id text NOT NULL, title text, published timestamptz, updated timestamptz,
  ucid text, author text, length_seconds integer, live_now boolean,
  premiere_timestamp timestamptz, views bigint, kind text,
  CONSTRAINT channel_videos_id_key UNIQUE (id));
CREATE INDEX channel_videos_ucid_idx ON channel_videos (ucid);
CREATE TABLE channels (
  id text NOT NULL, author text, updated timestamptz, deleted boolean,
  subscribed timestamptz, CONSTRAINT channels_id_key UNIQUE (id));
"""


def docker_works():
    try:
        return subprocess.run(["docker", "info"], capture_output=True,
                              timeout=30).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def require_docker():
    """Skip with a reason rather than failing obscurely on a machine without it."""
    if not docker_works():
        raise unittest.SkipTest(
            "docker is not available: the synthetic harness needs a throwaway "
            "postgres. The rest of the suite runs without it.")


class Database:
    """A throwaway PostgreSQL behind one long lived psql, holding no real row.

    One psql session rather than a `docker exec` per statement: the engine
    issues thousands over a household run and the exec handshake alone costs
    170 ms of the 0.7 ms each actually takes. The session outlives an error, so
    a failing statement raises here exactly as the engine's own psql wrapper
    does and the next one still runs.

    The cluster lives on a tmpfs, so it can leave nothing behind: `docker rm`
    without `-v` keeps the anonymous volume a postgres container creates, and
    64 of those from earlier test runs were what filled this machine's disk.
    """

    def __init__(self, image=IMAGE):
        self.image = image
        self.name = "ivs-synth-" + uuid.uuid4().hex[:8]
        self.session = None
        self.stderr = []

    def start(self):
        started = subprocess.run(
            ["docker", "run", "-d", "--rm", "--name", self.name,
             "--tmpfs", "/var/lib/postgresql/data:rw,size=512m",
             "-e", "PGDATA=/var/lib/postgresql/data/pgdata",
             "-e", "POSTGRES_PASSWORD=synthetic", "-e", "POSTGRES_USER=" + DB_USER,
             "-e", "POSTGRES_DB=" + DB_NAME, self.image],
            capture_output=True, text=True)
        if started.returncode:
            raise unittest.SkipTest("cannot start %s: %s"
                                    % (self.image, started.stderr.strip()))
        self._await_cluster()
        self._open_session()
        self.psql(INVIDIOUS_SQL)
        return self

    def _await_cluster(self):
        """A real query, not pg_isready: the entrypoint answers for the
        temporary server it runs while it initialises the cluster."""
        for _ in range(120):
            ready = subprocess.run(
                ["docker", "exec", self.name, "psql", "-U", DB_USER, "-d",
                 DB_NAME, "-c", "SELECT 1"], capture_output=True)
            if ready.returncode == 0:
                return
            time.sleep(0.5)
        self.stop()
        raise unittest.SkipTest("postgres never became ready")

    def _open_session(self):
        self.session = subprocess.Popen(
            ["docker", "exec", "-i", self.name, "psql", "-U", DB_USER, "-d",
             DB_NAME, "-q", "--csv", "-t", "--no-psqlrc"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, bufsize=1)
        threading.Thread(target=self._drain_stderr, daemon=True).start()

    def _drain_stderr(self):
        for line in self.session.stderr:
            self.stderr.append(line)

    def psql(self, sql):
        """Every output line of one or more statements, as psql's csv."""
        del self.stderr[:]
        self.session.stdin.write("%s\n;\n\\echo %s\n" % (sql, SENTINEL))
        self.session.stdin.flush()
        lines = []
        while True:
            line = self.session.stdout.readline()
            if not line:
                raise RuntimeError("psql died: " + "".join(self.stderr).strip())
            if line.rstrip("\n") == SENTINEL:
                break
            lines.append(line.rstrip("\n"))
        self._raise_any_error()
        return lines

    def _raise_any_error(self):
        time.sleep(0.005)
        complaint = "".join(self.stderr)
        if "ERROR:" in complaint:
            raise RuntimeError("psql failed: " + complaint.strip())

    def rows(self, sql):
        """Parsed rows, read the way the engine's own `query` reads psql."""
        text = "\n".join(self.psql(sql))
        return [row for row in csv.reader(io.StringIO(text)) if row]

    def value(self, sql):
        rows = self.rows(sql)
        return rows[0][0] if rows else ""

    def attach(self, engine):
        """Give the engine this database in place of its compose exec."""
        engine._psql = self._engine_psql

    def _engine_psql(self, flags, sql):
        return "\n".join(self.psql(sql)) + "\n"

    def stop(self):
        if self.session:
            self.session.stdin.close()
            self.session.wait(timeout=30)
            self.session.stdout.close()
            self.session.stderr.close()
            self.session = None
        subprocess.run(["docker", "rm", "-f", "-v", self.name], capture_output=True)


class ApiStub:
    """A playlist endpoint that remembers, and a video endpoint that costs a fetch.

    Every write goes through to `playlists` and `playlist_videos`, so the SQL
    the engine reads a lane back with sees exactly what it added. A playlist
    belonging to another account answers 403, as Invidious does.
    """

    def __init__(self, engine, db):
        self.engine = engine
        self.db = db
        self.calls = []
        self.fetched = []
        self.refused = []
        self.missing = set()
        self.playlists_made = 0

    def install(self):
        self.engine.api = self
        return self

    @property
    def fetches(self):
        """Calls that would have reached YouTube."""
        return len(self.fetched)

    def since(self):
        """A mark to count calls from, for `a mix costs zero fetches`."""
        return len(self.calls), len(self.fetched)

    def spent(self, mark):
        """Calls and fetches made since a mark, as (calls, fetches)."""
        return len(self.calls) - mark[0], len(self.fetched) - mark[1]

    def __call__(self, method, path, body=None):
        self.calls.append((method, path))
        if path == "/api/v1/stats":
            return {"software": {"name": "synthetic"}}
        if path.startswith("/api/v1/videos/"):
            return self._video(path.rsplit("/", 1)[-1])
        if path.startswith("/api/v1/channels/"):
            return self._channel_latest(path.split("/")[4])
        if path == "/api/v1/auth/playlists":
            return self._create(body)
        if path.startswith("/api/v1/auth/playlists/"):
            return self._playlist(method, path.split("/")[5:], body)
        raise AssertionError("the stub was asked for %s %s" % (method, path))

    def _video(self, vid):
        self.fetched.append(vid)
        if vid in self.missing:
            raise urllib.error.HTTPError(vid, 404, "no such video", {}, None)
        return video_of(vid)

    def _channel_latest(self, ucid):
        self.fetched.append(ucid)
        channel = channel_of_ucid(ucid)
        now = int(time.time())
        return {"videos": [listing_of(vid_of(channel, index), now - 3600 * index)
                           for index in range(FEED_UPLOADS_PER_CHANNEL)]}

    def _email(self):
        """The account whose session is on the call, as Invidious would resolve it."""
        sid = self.engine.SESSION
        if not sid:
            raise self.engine.Aborted("the stub was called with no session")
        return self.db.value("SELECT email FROM session_ids WHERE id=%s;"
                             % self.engine.lit(sid))

    def _create(self, body):
        lit = self.engine.lit
        self.playlists_made += 1
        plid = "IVPLSYNTH%09d" % self.playlists_made
        self.db.psql(
            "INSERT INTO playlists(title,id,author,description,video_count,"
            "created,updated,privacy,\"index\") "
            "VALUES (%s,%s,%s,'',0,now(),now(),%s,'{}'::bigint[]);"
            % (lit(body["title"]), lit(plid), lit(self._email()),
               lit(str(body.get("privacy") or "unlisted").capitalize())))
        return {"playlistId": plid}

    def _playlist(self, method, parts, body):
        plid = parts[0]
        owner = self.db.value("SELECT author FROM playlists WHERE id=%s;"
                              % self.engine.lit(plid))
        if not owner:
            raise urllib.error.HTTPError(plid, 404, "no such playlist", {}, None)
        if owner != self._email():
            self.refused.append(plid)
            raise urllib.error.HTTPError(
                plid, 403, "that playlist belongs to another account", {}, None)
        if len(parts) == 1:
            return self._show(plid) if method == "GET" else self._retitle(plid, body)
        if method == "POST":
            return self._add(plid, body["videoId"])
        return self._drop(plid, parts[2])

    def _show(self, plid):
        videos = [{"videoId": row[0], "title": row[1], "author": row[2],
                   "authorId": row[3], "lengthSeconds": int(row[4]),
                   "indexId": row[5]}
                  for row in self.db.rows(
                      "SELECT pv.id, coalesce(pv.title,''), coalesce(pv.author,''), "
                      "coalesce(pv.ucid,''), coalesce(pv.length_seconds,0), "
                      "pv.\"index\" FROM playlists p "
                      "CROSS JOIN LATERAL unnest(p.\"index\") WITH ORDINALITY "
                      "AS u(idx, ord) JOIN playlist_videos pv "
                      "ON pv.plid = p.id AND pv.\"index\" = u.idx "
                      "WHERE p.id = %s ORDER BY u.ord;" % self.engine.lit(plid))]
        return {"playlistId": plid, "videos": videos}

    def _retitle(self, plid, body):
        lit = self.engine.lit
        self.db.psql("UPDATE playlists SET title=%s, privacy=%s, updated=now() "
                     "WHERE id=%s;"
                     % (lit(body["title"]),
                        lit(str(body["privacy"]).capitalize()), lit(plid)))
        return None

    def _add(self, plid, vid):
        lit = self.engine.lit
        channel = channel_of(vid)
        index = int(self.db.value(
            "SELECT coalesce(max(\"index\"),0)+1 FROM playlist_videos "
            "WHERE plid=%s;" % lit(plid)) or 1)
        self.db.psql(
            "INSERT INTO playlist_videos(title,id,author,ucid,length_seconds,"
            "published,plid,\"index\",live_now) "
            "VALUES (%s,%s,%s,%s,%d,now(),%s,%d,false); "
            "UPDATE playlists SET \"index\" = \"index\" || %d::bigint, "
            "video_count = video_count + 1, updated = now() WHERE id = %s;"
            % (lit(title_of(vid)), lit(vid), lit(author_of(channel)),
               lit(ucid_of(channel)), seconds_of(vid), lit(plid), index,
               index, lit(plid)))
        return None

    def _drop(self, plid, index_id):
        lit = self.engine.lit
        index = int(index_id)
        self.db.psql(
            "DELETE FROM playlist_videos WHERE plid=%s AND \"index\"=%d; "
            "UPDATE playlists SET \"index\" = array_remove(\"index\", %d::bigint), "
            "video_count = greatest(coalesce(video_count,0) - 1, 0), "
            "updated = now() WHERE id = %s;" % (lit(plid), index, index, lit(plid)))
        return None


FAST_RATE = 6000

# Channel 999 and 998 are outside CHANNELS, so vid_of() can never produce these.
UNREACHABLE_VID = "SYN99999999"
UNREACHABLE_OTHER_VID = "SYN99899999"


class Args:
    """The argparse namespace the commands read, with harness defaults.

    FAST_RATE stands in for the fetch pacing, which is the only part of a run
    that costs wall clock time against a stub.
    """

    def __init__(self, **over):
        self.__dict__.update(
            dict(all_users=False, dry_run=False, lane=None, account=None,
                 seeds=0, rate=FAST_RATE, budget=600), **over)


class Instance:
    """A throwaway Invidious the engine can be pointed at, and torn down."""

    def __init__(self, engine, image=IMAGE):
        self.engine = engine
        self.db = Database(image)
        self.api = None
        self.log = []

    def start(self):
        self.db.start()
        self.db.attach(self.engine)
        self.api = ApiStub(self.engine, self.db).install()
        self.engine.log = self.log.append
        return self

    def stop(self):
        self.db.stop()

    def enrol(self, account):
        """Add one synthetic account, its history and its subscription feed."""
        lit = self.engine.lit
        self.db.psql(
            "INSERT INTO users(email,watched,subscriptions,notifications,"
            "preferences,password,token,updated,feed_needs_update) VALUES "
            "(%s,%s,%s,'{}'::text[],'{}','','',now(),false);"
            % (lit(account.email), self._text_array(account.watched),
               self._text_array(account.subscriptions)))
        for ucid in account.subscriptions:
            self._publish(ucid)
        self.engine._CONFIG_CACHE.pop("users", None)
        self.engine._WATCH_COUNTS.clear()
        return account

    def _publish(self, ucid):
        """A fortnight of uploads on one channel, above the history's indices."""
        lit = self.engine.lit
        channel = channel_of_ucid(ucid)
        values = []
        for step in range(FEED_UPLOADS_PER_CHANNEL):
            vid = vid_of(channel, FEED_FIRST_INDEX + step)
            values.append(
                "(%s,%s,now() - interval '%d hours',now(),%s,%s,%d,false,NULL,"
                "%d,'video')"
                % (lit(vid), lit(title_of(vid)), step * 6, lit(ucid),
                   lit(author_of(channel)), seconds_of(vid), views_of(vid)))
        self.db.psql(
            "INSERT INTO channels(id,author,updated,deleted,subscribed) "
            "VALUES (%s,%s,now(),false,now()) ON CONFLICT (id) DO NOTHING; "
            "INSERT INTO channel_videos(id,title,published,updated,ucid,author,"
            "length_seconds,live_now,premiere_timestamp,views,kind) VALUES %s "
            "ON CONFLICT (id) DO NOTHING;"
            % (lit(ucid), lit(author_of(channel)), ",".join(values)))

    def block(self, account, vid):
        """Put one video in an account's own Blocked playlist, as a client would."""
        lit = self.engine.lit
        plid = "IVPLSYNTHBLOCK%s" % account.name
        channel = channel_of(vid)
        self.db.psql(
            "INSERT INTO playlists(title,id,author,description,video_count,"
            "created,updated,privacy,\"index\") VALUES "
            "('Blocked',%s,%s,'',0,now(),now(),'Private','{}'::bigint[]) "
            "ON CONFLICT (id) DO NOTHING; "
            "INSERT INTO playlist_videos(title,id,author,ucid,length_seconds,"
            "published,plid,\"index\",live_now) VALUES (%s,%s,%s,%s,%d,now(),%s,"
            "(SELECT coalesce(max(\"index\"),0)+1 FROM playlist_videos "
            "WHERE plid=%s),false);"
            % (lit(plid), lit(account.email), lit(title_of(vid)), lit(vid),
               lit(author_of(channel)), lit(ucid_of(channel)), seconds_of(vid),
               lit(plid), lit(plid)))
        return ucid_of(channel)

    def lane_videos(self, email, lane_id):
        """A lane's playlist contents, over SQL, as [(vid, ucid), ...]."""
        lit = self.engine.lit
        plid = self.db.value("SELECT plid FROM suggest.lanes "
                             "WHERE account=%s AND lane=%s;"
                             % (lit(email), lit(lane_id)))
        if not plid:
            return []
        return [(row[0], row[1]) for row in self.db.rows(
            "SELECT pv.id, coalesce(pv.ucid,'') FROM playlists p "
            "CROSS JOIN LATERAL unnest(p.\"index\") WITH ORDINALITY AS u(idx, ord) "
            "JOIN playlist_videos pv ON pv.plid = p.id AND pv.\"index\" = u.idx "
            "WHERE p.id = %s ORDER BY u.ord;" % lit(plid))]

    def lanes_of(self, email):
        return [row[0] for row in self.db.rows(
            "SELECT lane FROM suggest.lanes WHERE account=%s ORDER BY 1;"
            % self.engine.lit(email))]

    def snapshot(self):
        """Every per-account row of suggest.*, keyed by account, as text."""
        state = {}
        for table, columns, order in (
                ("lanes", "account,lane,plid,title", "1,2"),
                ("items", "account,lane,vid,score,song_key", "1,2,3"),
                ("cooldown", "account,lane,vid,reason", "1,2,3")):
            for row in self.db.rows("SELECT %s FROM suggest.%s ORDER BY %s;"
                                    % (columns, table, order)):
                state.setdefault(row[0], []).append((table, tuple(row[1:])))
        return state

    def night(self, **over):
        """One nightly run, as the timer would fire it."""
        self.log.append("=== iv-suggest run %s ===" % sorted(over.items()))
        return self.engine.cmd_run(Args(**over))

    def hour(self, **over):
        """One hourly reorder, as the shuffle timer would fire it."""
        self.log.append("=== iv-suggest shuffle %s ===" % sorted(over.items()))
        return self.engine.cmd_shuffle(Args(**over))

    def metrics(self):
        """`iv-suggest metrics` output as a list of sample lines."""
        printed = []
        self.engine.print = printed.append
        try:
            self.engine.cmd_metrics(Args())
        finally:
            del self.engine.print
        return [line for block in printed for line in block.splitlines()]

    def _text_array(self, values):
        if not values:
            return "'{}'::text[]"
        return "ARRAY[%s]::text[]" % ",".join(self.engine.lit(v) for v in values)

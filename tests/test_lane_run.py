"""What one refill run does to a lane: what it sweeps, what it offers, what it keeps.

These are the pins for the refill pipeline. Every assertion is on something a
reader could see afterwards -- the API calls made, the rows written, the counts
the run row records -- rather than on how the pipeline is spelled, so the same
tests hold however it is split into functions.
"""

import os
import re
import tempfile
import unittest
import urllib.error

from support import load

ME = "andre@example.com"


class Db:
    """Stands in for the psql helpers: answers the reads, records the writes."""

    def __init__(self, plid="PL1", items=None, cooldown=(), elsewhere=(),
                 other_songs=(), feed=()):
        self.plid = plid
        self.items = dict(items or {})
        self.cooldown = list(cooldown)
        self.elsewhere = list(elsewhere)
        self.other_songs = list(other_songs)
        self.feed = list(feed)
        self.written = []
        self.today = "2026-08-31"
        self.ttl_cutoff = "2026-08-01"
        self.asked = []

    def install(self, mod):
        mod.one = self.one
        mod.query = self.query
        mod.execute = self.written.append
        return self

    def one(self, sql):
        self.asked.append(sql)
        if "SELECT plid FROM suggest.lanes" in sql:
            return self.plid
        if "to_char(now() - interval" in sql:
            return self.ttl_cutoff
        if "to_char(now()" in sql:
            return self.today
        return ""

    def query(self, sql):
        self.asked.append(sql)
        if "FROM suggest.items" in sql and "lane <> " in sql:
            if "song_key" in sql:
                return [[k] for k in self.other_songs]
            return [[v] for v in self.elsewhere]
        if "FROM suggest.items" in sql:
            return [[vid, day, str(epoch), str(score)]
                    for vid, (day, epoch, score) in self.items.items()]
        if "FROM suggest.cooldown" in sql:
            return [[v] for v in self.cooldown]
        if "FROM channel_videos" in sql:
            return self.feed
        return []

    def flat(self):
        return [" ".join(sql.split()) for sql in self.written]

    def item_rows(self):
        """(vid, score, song_key) for every suggest.items row the run wrote."""
        rows = []
        for sql in self.flat():
            m = re.search(r"INSERT INTO suggest\.items\(account,lane,vid,score,"
                          r"song_key\) VALUES \('[^']*','[^']*','([^']*)',"
                          r"([-\d.]+),'([^']*)'\)", sql)
            if m:
                rows.append((m.group(1), float(m.group(2)), m.group(3)))
        return rows

    def adopted(self):
        return re.findall(
            r"INSERT INTO suggest\.items\(account,lane,vid,score\) "
            r"VALUES \('[^']*','[^']*','([^']*)',0\)", " ".join(self.flat()))

    def forgotten(self):
        return re.findall(
            r"DELETE FROM suggest\.items WHERE account='[^']*' AND "
            r"lane='[^']*' AND vid='([^']*)'", " ".join(self.flat()))

    def cooldowns(self):
        """(vid, days, reason) for every cooldown row the run wrote."""
        return [(m.group(1), int(m.group(2)), m.group(3)) for m in re.finditer(
            r"INSERT INTO suggest\.cooldown\(account,lane,vid,until,reason\) "
            r"VALUES \('[^']*','[^']*','([^']*)',now\(\) \+ interval "
            r"'(\d+) days','([^']*)'\)", " ".join(self.flat()))]


class Api:
    """The Invidious auth API, without a network."""

    def __init__(self, videos=(), refuse=()):
        self.playlist = {"videos": [dict(v) for v in videos]}
        self.refuse = set(refuse)
        self.calls = []

    def install(self, mod):
        mod.api = self.call
        return self

    def call(self, method, path, body=None):
        self.calls.append((method, path, body))
        if method == "POST" and "/videos" in path:
            if body["videoId"] in self.refuse:
                raise urllib.error.HTTPError(path, 500, "boom", None, None)
            return {}
        if method == "GET":
            return self.playlist
        return {}

    def added(self):
        return [body["videoId"] for method, path, body in self.calls
                if method == "POST" and "/videos" in path]

    def removed(self):
        return [path.rsplit("/", 1)[1] for method, path, body in self.calls
                if method == "DELETE"]


class Fetch:
    """The pacing and caching layer, without a network."""

    def __init__(self, meta=None, recs=None, channels=None, dead=()):
        self.meta = dict(meta or {})
        self.recs = dict(recs or {})
        self.channels = dict(channels or {})
        self.dead = set(dead)
        self.fetches = 0
        self.cache_hits = 0
        self.lane_used = 0
        self.lane_cap = None
        self.buried = []
        self.remembered = []

    def begin_lane(self, cap):
        self.lane_cap, self.lane_used = cap, 0

    def video(self, vid):
        self.fetches += 1
        self.lane_used += 1
        return self.recs.get(vid)

    def channel_latest(self, ucid):
        self.fetches += 1
        self.lane_used += 1
        return self.channels.get(ucid)

    def known(self, vid):
        return self.meta.get(vid)

    def meta_of(self, vid):
        return self.meta.get(vid)

    def bury(self, vid, days, reason):
        self.buried.append((vid, days, reason))

    def remember_partial(self, entries):
        self.remembered.extend(entries)


def entry(vid, title=None, author_id="UC-a", index=None):
    return {"videoId": vid, "title": title or vid.upper(),
            "authorId": author_id, "author": "A", "indexId": index or ("ix-" + vid)}


def rec(vid, title=None, author_id="UC-r", seconds=600, published=None):
    out = {"videoId": vid, "title": title or vid.upper(), "author": "R",
           "authorId": author_id, "lengthSeconds": seconds}
    if published is not None:
        out["published"] = published
    return out


class LaneCase(unittest.TestCase):

    def setUp(self):
        self.mod = load(IV_SUGGEST_ACCOUNT=ME)
        self.said = []
        self.mod.log = self.said.append

    def lane(self, **patch):
        base = dict(self.mod.DEFAULTS, shuffle=dict(self.mod.SHUFFLE_DEFAULTS))
        return self.mod.merge_lane(
            base, dict({"id": "lane1", "title": "Lane One"}, **patch))

    def context(self, watched=(), subs=(), blocked=None, dry=False,
                seed_override=None):
        return self.mod.LaneRun(list(watched), set(watched), set(subs),
                                blocked or {}, self.fetch, dry, seed_override)

    def fill(self, lane, watched=(), subs=(), blocked=None, dry=False,
             seed_override=None, db=None, api=None, fetch=None):
        self.db = db or Db()
        self.api = api or Api()
        self.fetch = fetch or Fetch()
        self.db.install(self.mod)
        self.api.install(self.mod)
        return self.mod.run_lane(
            lane, self.context(watched, subs, blocked, dry, seed_override))

    def logged(self, fragment):
        return [line for line in self.said if fragment in line]


class Sweep(LaneCase):
    """What leaves a lane, and what it costs the video to come back."""

    def test_an_untracked_entry_is_adopted_so_the_ttl_can_reach_it(self):
        api = Api(videos=[entry("aaaaaaaaaaa")])
        self.fill(self.lane(), db=Db(), api=api)
        self.assertEqual(["aaaaaaaaaaa"], self.db.adopted())
        self.assertTrue(self.logged("adopted an untracked entry"))

    def test_a_blocked_channel_leaves_tonight_with_no_cooldown(self):
        api = Api(videos=[entry("aaaaaaaaaaa", author_id="UC-bad")])
        db = Db(items={"aaaaaaaaaaa": (self.today(), 100.0, 1.0)})
        self.fill(self.lane(), blocked={"UC-bad": "Bad"}, db=db, api=api)
        self.assertEqual(["ix-aaaaaaaaaaa"], self.api.removed())
        self.assertEqual(["aaaaaaaaaaa"], self.db.forgotten())
        self.assertEqual([], self.db.cooldowns(),
                         "unblocking the channel must let it back the same night")

    def test_a_watched_entry_leaves_on_the_watched_cooldown(self):
        api = Api(videos=[entry("aaaaaaaaaaa")])
        db = Db(items={"aaaaaaaaaaa": (self.today(), 100.0, 1.0)})
        self.fill(self.lane(watched_cooldown_days=365), watched=["aaaaaaaaaaa"],
                 db=db, api=api)
        self.assertEqual([("aaaaaaaaaaa", 365, "watched")], self.db.cooldowns())

    def test_an_expired_entry_leaves_on_the_plain_cooldown(self):
        api = Api(videos=[entry("aaaaaaaaaaa")])
        db = Db(items={"aaaaaaaaaaa": ("2026-01-01", 100.0, 1.0)})
        self.fill(self.lane(cooldown_days=60), db=db, api=api)
        self.assertEqual([("aaaaaaaaaaa", 60, "expired")], self.db.cooldowns())

    def test_forced_turnover_retires_the_oldest_survivors(self):
        videos = [entry("aaaaaaaaaaa"), entry("bbbbbbbbbbb"), entry("ccccccccccc")]
        db = Db(items={"aaaaaaaaaaa": (self.today(), 10.0, 1.0),
                       "bbbbbbbbbbb": (self.today(), 20.0, 1.0),
                       "ccccccccccc": (self.today(), 30.0, 1.0)})
        self.fill(self.lane(refresh_per_day=2, rotate_cooldown_days=21),
                 db=db, api=Api(videos=videos))
        self.assertEqual([("aaaaaaaaaaa", 21, "rotated"),
                          ("bbbbbbbbbbb", 21, "rotated")],
                         sorted(self.db.cooldowns()))

    def test_keep_min_is_the_floor_turnover_will_not_go_under(self):
        videos = [entry("aaaaaaaaaaa"), entry("bbbbbbbbbbb"), entry("ccccccccccc")]
        db = Db(items={"aaaaaaaaaaa": (self.today(), 10.0, 1.0),
                       "bbbbbbbbbbb": (self.today(), 20.0, 1.0),
                       "ccccccccccc": (self.today(), 30.0, 1.0)})
        self.fill(self.lane(refresh_per_day=3, keep_min=2),
                 db=db, api=Api(videos=videos))
        self.assertEqual(1, len(self.db.cooldowns()))

    def test_a_full_lane_stops_before_looking_for_anything(self):
        videos = [entry("v%09d" % i) for i in range(3)]
        db = Db(items={v["videoId"]: (self.today(), 10.0, 1.0) for v in videos})
        fetch = Fetch()
        removed, added, kept, used = self.fill(
            self.lane(size=3), db=db, api=Api(videos=videos), fetch=fetch)
        self.assertEqual((0, 0, 3), (removed, added, kept))
        self.assertEqual(0, fetch.fetches, "a full lane must cost no fetch")
        self.assertEqual([], self.api.added())

    def test_a_dry_run_writes_nothing_and_changes_no_playlist(self):
        api = Api(videos=[entry("aaaaaaaaaaa")])
        db = Db(items={"aaaaaaaaaaa": ("2026-01-01", 100.0, 1.0)})
        self.fill(self.lane(expand="none"), watched=["bbbbbbbbbbb"],
                 dry=True, db=db, api=api)
        self.assertEqual([], db.written)
        self.assertEqual([], api.removed())
        self.assertEqual([], api.added())

    def today(self):
        return "2026-08-31"


class Seeds(LaneCase):
    """Which history entries the lane expands from."""

    def test_seeds_are_the_newest_history_first_up_to_the_limit(self):
        fetch = Fetch(meta={"h%010d" % i: {"authorId": "UC-h"} for i in range(5)})
        self.fill(self.lane(expand="none", exclude_watched=False,
                            seed={"limit": 2}),
                  watched=["h%010d" % i for i in range(5)],
                  db=Db(), api=Api(), fetch=fetch)
        self.assertEqual(["h0000000004", "h0000000003"], self.db_added_vids())

    def test_a_seed_on_a_blocked_channel_is_never_expanded(self):
        fetch = Fetch(meta={"h0000000000": {"authorId": "UC-bad"},
                            "h0000000001": {"authorId": "UC-ok"}})
        self.fill(self.lane(expand="none", exclude_watched=False),
                  watched=["h0000000000", "h0000000001"],
                  blocked={"UC-bad": "Bad"}, db=Db(), api=Api(), fetch=fetch)
        self.assertEqual(["h0000000001"], self.db_added_vids())

    def test_the_seed_override_caps_the_configured_limit(self):
        fetch = Fetch()
        self.fill(self.lane(expand="none", exclude_watched=False,
                            seed={"limit": 10}),
                  watched=["h%010d" % i for i in range(6)], seed_override=2,
                  db=Db(), api=Api(), fetch=fetch)
        self.assertEqual(2, len(self.db_added_vids()))

    def test_an_account_with_no_history_makes_no_api_call_at_all(self):
        removed, added, kept, used = self.fill(self.lane(), db=Db(), api=Api())
        self.assertEqual((0, 0, 0), (removed, added, kept))
        self.assertEqual([], self.api.added())

    def test_a_subscription_feed_lane_needs_no_history(self):
        db = Db(feed=[["s0000000001", "Feed One", "Author", "UC-s", "600", "900"]])
        feed = self.lane(expand="subscription_feed", exclude_subscribed=False)
        self.fill(feed, subs=["UC-s"], db=db, api=Api())
        self.assertEqual(["s0000000001"], self.api.added())
        self.assertEqual([], self.logged("  seeds "),
                         "a feed lane must not report a seed count it never used")

    def db_added_vids(self):
        return [vid for vid, _, _ in self.db.item_rows()]


class Expand(LaneCase):
    """Where the candidates come from, and what score each carries."""

    def test_a_recommendation_of_two_seeds_outscores_one_of_a_single_seed(self):
        recs = {"h0000000000": {"recommendedVideos": [rec("ccccccccccc"),
                                                      rec("ddddddddddd")]},
                "h0000000001": {"recommendedVideos": [rec("ccccccccccc")]}}
        fetch = Fetch(recs=recs)
        self.fill(self.lane(size=2, seed={"limit": 2}),
                 watched=["h0000000001", "h0000000000"],
                 db=Db(), api=Api(), fetch=fetch)
        scores = dict((vid, score) for vid, score, _ in self.db.item_rows())
        self.assertGreater(scores["ccccccccccc"], scores["ddddddddddd"])

    def test_recommend_max_age_days_drops_a_recommendation_that_is_too_old(self):
        recs = {"h0000000000": {"recommendedVideos": [
            rec("ccccccccccc", published="2020-01-01T00:00:00Z"),
            rec("ddddddddddd", published="2099-01-01T00:00:00Z")]}}
        self.fill(self.lane(recommend_max_age_days=7), watched=["h0000000000"],
                 db=Db(), api=Api(), fetch=Fetch(recs=recs))
        self.assertEqual(["ddddddddddd"], self.api.added())

    def test_channel_latest_reads_watched_channels_that_are_not_subscribed(self):
        fetch = Fetch(meta={"h0000000000": {"authorId": "UC-x"},
                            "h0000000001": {"authorId": "UC-sub"}},
                      channels={"UC-x": {"videos": [rec("ccccccccccc",
                                                        author_id="UC-x")]}})
        self.fill(self.lane(expand="channel_latest"),
                 watched=["h0000000001", "h0000000000"], subs=["UC-sub"],
                 db=Db(), api=Api(), fetch=fetch)
        self.assertEqual(["ccccccccccc"], self.api.added())

    def test_channel_latest_skips_a_live_or_upcoming_upload(self):
        live = dict(rec("ccccccccccc", author_id="UC-x"), liveNow=True)
        soon = dict(rec("ddddddddddd", author_id="UC-x"), isUpcoming=True)
        fetch = Fetch(meta={"h0000000000": {"authorId": "UC-x"}},
                      channels={"UC-x": {"videos": [live, soon,
                                                    rec("eeeeeeeeeee",
                                                        author_id="UC-x")]}})
        self.fill(self.lane(expand="channel_latest"), watched=["h0000000000"],
                 db=Db(), api=Api(), fetch=fetch)
        self.assertEqual(["eeeeeeeeeee"], self.api.added())

    def test_the_subscription_feed_carries_the_view_count_it_came_with(self):
        db = Db(feed=[["s0000000001", "Feed One", "Author", "UC-s", "600", "900"]])
        fetch = Fetch()
        feed = self.lane(expand="subscription_feed", exclude_subscribed=False)
        self.fill(feed, subs=["UC-s"], db=db, api=Api(), fetch=fetch)
        self.assertEqual([("s0000000001", 900)],
                         [(vid, m["views"]) for vid, m in fetch.remembered])

    def test_a_blocked_channel_is_subtracted_from_the_subscription_feed(self):
        db = Db(feed=[])
        feed = self.lane(expand="subscription_feed", exclude_subscribed=False)
        self.fill(feed, subs=["UC-bad"], blocked={"UC-bad": "Bad"}, db=db, api=Api())
        self.assertTrue(self.logged("no subscriptions"))

    def test_expand_none_offers_the_history_newest_play_first(self):
        self.fill(self.lane(expand="none", exclude_watched=False),
                  watched=["h0000000000", "h0000000001"], db=Db(), api=Api())
        rows = self.db.item_rows()
        self.assertEqual(["h0000000001", "h0000000000"], [r[0] for r in rows])
        self.assertGreaterEqual(rows[0][1], rows[1][1])


class Filter(LaneCase):
    """Why a candidate is refused, counted reason by reason."""

    def offer(self, lane, **kw):
        recs = {"h0000000000": {"recommendedVideos": [
            rec("ccccccccccc", title="Track One"),
            rec("ddddddddddd", title="Track Two", author_id="UC-two")]}}
        fetch = kw.pop("fetch", None) or Fetch(recs=recs)
        if not fetch.recs:
            fetch.recs = recs
        self.fill(lane, watched=kw.pop("watched", ["h0000000000"]),
                 fetch=fetch, **kw)
        return self.api.added()

    def test_a_video_already_in_the_lane_is_not_offered_again(self):
        api = Api(videos=[entry("ccccccccccc", title="Track One")])
        db = Db(items={"ccccccccccc": (Sweep.today(self), 10.0, 1.0)})
        self.assertEqual(["ddddddddddd"], self.offer(self.lane(), db=db, api=api))

    def test_a_known_unfetchable_video_is_not_offered(self):
        fetch = Fetch(dead=["ccccccccccc"])
        self.assertEqual(["ddddddddddd"], self.offer(self.lane(), fetch=fetch))

    def test_a_video_this_account_watched_is_not_offered(self):
        self.assertEqual(["ddddddddddd"], self.offer(
            self.lane(), watched=["h0000000000", "ccccccccccc"]))

    def test_a_video_on_cooldown_is_not_offered(self):
        self.assertEqual(["ddddddddddd"], self.offer(
            self.lane(), db=Db(cooldown=["ccccccccccc"])))

    def test_a_video_held_by_another_lane_is_not_offered(self):
        self.assertEqual(["ddddddddddd"], self.offer(
            self.lane(), db=Db(elsewhere=["ccccccccccc"])))

    def test_dedupe_across_lanes_off_stops_asking_about_other_lanes(self):
        db = Db(elsewhere=["ccccccccccc"])
        got = self.offer(self.lane(dedupe_across_lanes=False), db=db)
        self.assertIn("ccccccccccc", got)
        self.assertEqual([], [q for q in db.asked if "lane <> " in q])

    def test_this_runs_own_drops_are_on_cooldown_even_in_a_dry_run(self):
        api = Api(videos=[entry("ccccccccccc", title="Track One")])
        db = Db(items={"ccccccccccc": ("2026-01-01", 10.0, 1.0)})
        self.fill(self.lane(), watched=["h0000000000"], dry=True, db=db, api=api,
                 fetch=Fetch(recs={"h0000000000": {"recommendedVideos": [
                     rec("ccccccccccc", title="Track One")]}}))
        self.assertTrue(self.logged("cooldown"),
                        "a dry run must not show a video it just swept coming back")

    def test_a_blocked_channels_recommendation_is_refused(self):
        self.assertEqual(["ddddddddddd"], self.offer(
            self.lane(), blocked={"UC-r": "Bad"}))

    def test_a_subscribed_channel_is_refused_when_the_lane_excludes_them(self):
        self.assertEqual(["ddddddddddd"], self.offer(
            self.lane(), subs=["UC-r"]))

    def test_a_video_shorter_than_min_seconds_is_refused(self):
        fetch = Fetch(recs={"h0000000000": {"recommendedVideos": [
            rec("ccccccccccc", title="Track One", seconds=30),
            rec("ddddddddddd", title="Track Two", seconds=600,
                author_id="UC-two")]}})
        self.assertEqual(["ddddddddddd"],
                         self.offer(self.lane(min_seconds=120), fetch=fetch))

    def test_a_video_longer_than_max_seconds_is_refused(self):
        fetch = Fetch(recs={"h0000000000": {"recommendedVideos": [
            rec("ccccccccccc", title="Track One", seconds=9000),
            rec("ddddddddddd", title="Track Two", seconds=600,
                author_id="UC-two")]}})
        self.assertEqual(["ddddddddddd"],
                         self.offer(self.lane(max_seconds=3600), fetch=fetch))

    def test_max_per_channel_counts_what_the_lane_already_holds(self):
        api = Api(videos=[entry("aaaaaaaaaaa", title="Held", author_id="UC-r")])
        db = Db(items={"aaaaaaaaaaa": (Sweep.today(self), 10.0, 1.0)})
        got = self.offer(self.lane(max_per_channel=1), db=db, api=api)
        self.assertEqual(["ddddddddddd"], got)

    def test_one_upload_per_song_across_the_candidates(self):
        fetch = Fetch(recs={"h0000000000": {"recommendedVideos": [
            rec("ccccccccccc", title="Artist - Song (Official Video)"),
            rec("ddddddddddd", title="Artist - Song", author_id="UC-two")]}})
        self.assertEqual(["ccccccccccc"],
                         self.offer(self.lane(dedupe_songs=True), fetch=fetch))

    def test_a_song_another_lane_already_holds_is_refused(self):
        fetch = Fetch(recs={"h0000000000": {"recommendedVideos": [
            rec("ccccccccccc", title="Artist - Song"),
            rec("ddddddddddd", title="Artist - Other", author_id="UC-two")]}})
        db = Db(other_songs=[self.mod.song_key("Artist - Song")])
        self.assertEqual(["ddddddddddd"],
                         self.offer(self.lane(), db=db, fetch=fetch))

    def test_the_lane_stops_at_the_room_the_sweep_left(self):
        fetch = Fetch(recs={"h0000000000": {"recommendedVideos": [
            rec("c%010d" % i, title="Track %d" % i, author_id="UC-%d" % i)
            for i in range(6)]}})
        got = self.offer(self.lane(size=2), fetch=fetch)
        self.assertEqual(2, len(got))


class RunContext(LaneCase):
    """The values that hold still for a whole account must not be interchangeable."""

    def test_watched_subscribed_and_blocked_each_reject_their_own_candidate(self):
        recs = {"h0000000000": {"recommendedVideos": [
            rec("ccccccccccc", author_id="UC-ok"),
            rec("ddddddddddd", author_id="UC-sub"),
            rec("eeeeeeeeeee", author_id="UC-bad"),
            rec("fffffffffff", author_id="UC-ok")]}}
        self.fill(self.lane(), watched=["h0000000000", "fffffffffff"],
                  subs=["UC-sub"], blocked={"UC-bad": "Bad"},
                  db=Db(), api=Api(), fetch=Fetch(recs=recs))
        self.assertEqual(["ccccccccccc"], self.api.added())
        rejected = self.logged("rejected")[0]
        self.assertIn("'watched': 1", rejected)
        self.assertIn("'subscribed': 1", rejected)
        self.assertIn("'blocked': 1", rejected)

    def test_one_fetcher_is_shared_by_the_seed_scan_and_the_expansion(self):
        fetch = Fetch(recs={"h0000000000": {"recommendedVideos": [rec("ccccccccccc")]}})
        removed, added, kept, used = self.fill(
            self.lane(), watched=["h0000000000"], db=Db(), api=Api(), fetch=fetch)
        self.assertEqual(fetch.fetches, used)
        self.assertEqual(1, added)


class GenreAndSampling(LaneCase):
    """The branches that cost extra fetches or bring randomness in."""

    def test_a_seed_must_match_the_seed_genre_before_it_is_expanded(self):
        fetch = Fetch(
            meta={"h0000000000": {"genre": "Music", "authorId": "UC-1"},
                  "h0000000001": {"genre": "Gaming", "authorId": "UC-2"}},
            recs={"h0000000000": {"recommendedVideos": [rec("ccccccccccc")]},
                  "h0000000001": {"recommendedVideos": [rec("ddddddddddd")]}})
        self.fill(self.lane(seed={"genre": "Music", "limit": 5}),
                  watched=["h0000000001", "h0000000000"],
                  db=Db(), api=Api(), fetch=fetch)
        self.assertEqual(["ccccccccccc"], self.api.added())

    def test_a_candidate_must_match_the_lane_filter_genre(self):
        fetch = Fetch(
            meta={"ccccccccccc": {"genre": "Gaming", "authorId": "UC-c",
                                  "seconds": 600, "title": "C", "author": "c"},
                  "ddddddddddd": {"genre": "Music", "authorId": "UC-d",
                                  "seconds": 600, "title": "D", "author": "d"}},
            recs={"h0000000000": {"recommendedVideos": [
                rec("ccccccccccc"), rec("ddddddddddd", author_id="UC-d")]}})
        self.fill(self.lane(filter={"genre": "Music"}), watched=["h0000000000"],
                  db=Db(), api=Api(), fetch=fetch)
        self.assertEqual(["ddddddddddd"], self.api.added())

    def test_the_genre_check_gives_up_at_its_cap_rather_than_walking_them_all(self):
        many = [rec("c%010d" % i, author_id="UC-%d" % i) for i in range(25)]
        fetch = Fetch(recs={"h0000000000": {"recommendedVideos": many}})
        self.fill(self.lane(size=1, filter={"genre": "Music"}),
                  watched=["h0000000000"], db=Db(), api=Api(), fetch=fetch)
        self.assertTrue(self.logged("genre check cap reached after 20"))
        self.assertEqual([], self.api.added())

    def test_a_weighted_sample_still_fills_the_room_the_sweep_left(self):
        many = [rec("c%010d" % i, title="Track %d" % i, author_id="UC-%d" % i)
                for i in range(10)]
        fetch = Fetch(recs={"h0000000000": {"recommendedVideos": many}})
        self.fill(self.lane(size=3, sample_pool=8), watched=["h0000000000"],
                  db=Db(), api=Api(), fetch=fetch)
        self.assertEqual(3, len(self.api.added()))
        self.assertTrue(self.logged("weighted sample over the top 8"))

    def test_a_spent_budget_expands_fewer_seeds_and_keeps_what_it_found(self):
        mod = self.mod

        class Broke(Fetch):
            def video(self, vid):
                if vid == "h0000000001":
                    raise mod.BudgetSpent("run budget 1 spent")
                return super().video(vid)

        fetch = Broke(recs={"h0000000000": {"recommendedVideos": [
            rec("ccccccccccc")]}})
        self.fill(self.lane(seed={"limit": 5}),
                  watched=["h0000000000", "h0000000001"],
                  db=Db(), api=Api(), fetch=fetch)
        self.assertTrue(self.logged("expanding only the first 0 seeds"))
        self.assertEqual([], self.api.added())


class Reconcile(LaneCase):
    """Putting the chosen videos in the playlist, and what is recorded."""

    def setUp(self):
        super().setUp()
        self.recs = {"h0000000000": {"recommendedVideos": [
            rec("ccccccccccc", title="Artist - Song")]}}

    def test_a_chosen_video_is_added_and_tracked_with_its_song_key(self):
        self.fill(self.lane(), watched=["h0000000000"], db=Db(), api=Api(),
                 fetch=Fetch(recs=self.recs))
        vid, score, skey = self.db.item_rows()[0]
        self.assertEqual("ccccccccccc", vid)
        self.assertGreater(score, 0.0)
        self.assertEqual(self.mod.song_key("Artist - Song"), skey)

    def test_a_refused_add_is_buried_and_does_not_count_as_added(self):
        api = Api(refuse=["ccccccccccc"])
        fetch = Fetch(recs=self.recs)
        removed, added, kept, used = self.fill(
            self.lane(), watched=["h0000000000"], db=Db(), api=api, fetch=fetch)
        self.assertEqual(0, added)
        self.assertEqual([("ccccccccccc", 30, "playlist add 500")], fetch.buried)
        self.assertEqual([], self.db.item_rows(),
                         "a video Invidious refused must not be tracked")

    def test_only_what_landed_gets_its_listing_metadata_persisted(self):
        recs = {"h0000000000": {"recommendedVideos": [
            rec("ccccccccccc", title="Artist - Song"),
            rec("ddddddddddd", title="Artist - Other", author_id="UC-two")]}}
        fetch = Fetch(recs=recs)
        self.fill(self.lane(size=1), watched=["h0000000000"], db=Db(),
                 api=Api(), fetch=fetch)
        self.assertEqual(["ccccccccccc"], [vid for vid, _ in fetch.remembered])

    def test_the_returned_counts_are_removed_added_kept_and_fetches(self):
        api = Api(videos=[entry("aaaaaaaaaaa", title="Old")])
        db = Db(items={"aaaaaaaaaaa": ("2026-01-01", 10.0, 1.0)})
        fetch = Fetch(recs=self.recs)
        fetch.fetches = 7
        removed, added, kept, used = self.fill(
            self.lane(), watched=["h0000000000"], db=db, api=api, fetch=fetch)
        self.assertEqual((1, 1, 0, 1), (removed, added, kept, used))


class LastPlayed(LaneCase):
    """policy: last_played holds the top of the play order and nothing else."""

    def play(self, lane, watched, blocked=None, dry=False, db=None, api=None,
             fetch=None):
        self.db = db or Db()
        self.api = api or Api()
        self.fetch = fetch or Fetch()
        self.db.install(self.mod)
        self.api.install(self.mod)
        return self.mod.run_lane_last_played(
            lane, self.context(watched, blocked=blocked, dry=dry))

    def test_the_most_recently_played_become_the_lane_newest_first(self):
        self.play(self.lane(policy="last_played", size=2),
                  ["h0000000000", "h0000000001", "h0000000002"])
        self.assertEqual(["h0000000002", "h0000000001"], self.api.added())

    def test_a_video_that_fell_out_of_the_window_is_evicted(self):
        api = Api(videos=[entry("h0000000000")])
        self.play(self.lane(policy="last_played", size=1),
                  ["h0000000000", "h0000000001"], api=api)
        self.assertEqual(["ix-h0000000000"], api.removed())
        self.assertEqual(["h0000000000"], self.db.forgotten())

    def test_an_evicted_video_gets_no_cooldown_so_a_replay_brings_it_back(self):
        api = Api(videos=[entry("h0000000000")])
        self.play(self.lane(policy="last_played", size=1),
                  ["h0000000000", "h0000000001"], api=api)
        self.assertEqual([], self.db.cooldowns())

    def test_a_blocked_channel_leaves_even_though_it_was_just_played(self):
        api = Api(videos=[entry("h0000000000", author_id="UC-bad")])
        fetch = Fetch(meta={"h0000000000": {"authorId": "UC-bad"}})
        self.play(self.lane(policy="last_played", size=5), ["h0000000000"],
                  blocked={"UC-bad": "Bad"}, api=api, fetch=fetch)
        self.assertEqual(["ix-h0000000000"], api.removed())

    def test_the_score_falls_off_with_the_play_rank(self):
        self.play(self.lane(policy="last_played", size=2, played_decay=0.5),
                  ["h0000000000", "h0000000001"])
        rows = self.db.item_rows()
        self.assertEqual([("h0000000001", 10.0), ("h0000000000", 5.0)],
                         [(vid, score) for vid, score, _ in rows])

    def test_a_dry_run_writes_nothing_and_changes_no_playlist(self):
        api = Api(videos=[entry("h0000000000")])
        self.play(self.lane(policy="last_played", size=1),
                  ["h0000000000", "h0000000001"], dry=True, api=api)
        self.assertEqual([], self.db.written)
        self.assertEqual([], api.removed())
        self.assertEqual([], api.added())

    def test_a_refused_add_is_buried_and_does_not_count_as_added(self):
        api = Api(refuse=["h0000000001"])
        fetch = Fetch()
        removed, added, kept, used = self.play(
            self.lane(policy="last_played", size=1), ["h0000000001"],
            api=api, fetch=fetch)
        self.assertEqual((0, 0, 0), (removed, added, kept))
        self.assertEqual([("h0000000001", 30, "playlist add 500")], fetch.buried)

    def test_the_returned_counts_are_removed_added_kept_and_fetches(self):
        api = Api(videos=[entry("h0000000000"), entry("h0000000009")])
        removed, added, kept, used = self.play(
            self.lane(policy="last_played", size=2),
            ["h0000000000", "h0000000001"], api=api)
        self.assertEqual((1, 1, 1, 0), (removed, added, kept, used))


class Dedupe(LaneCase):
    """One upload per song per account, the artist's own copy preferred."""

    def account(self, lanes=("music",)):
        fh = tempfile.NamedTemporaryFile("w", suffix=".yml", delete=False)
        fh.write("lanes:\n" + "".join(
            "  - id: %s\n    title: %s\n" % (l, l.title()) for l in lanes))
        fh.close()
        self.addCleanup(os.unlink, fh.name)
        self.mod = load(IV_SUGGEST_CONFIG=fh.name, IV_SUGGEST_ACCOUNT=ME)
        self.said = []
        self.mod.log = self.said.append

    def dedupe(self, lane_videos, dry=False):
        self.account(tuple(lane_videos))
        self.db = Db()
        self.db.query = lambda sql: (
            [[lane, "PL-" + lane] for lane in lane_videos]
            if "FROM suggest.lanes" in sql else [])
        self.db.install(self.mod)
        self.api = Api()
        self.api.playlist = None
        self.api.call = lambda method, path, body=None: (
            self.api.calls.append((method, path, body))
            or {"videos": lane_videos[path.rsplit("/", 1)[1][3:]]}
            if method == "GET" else
            self.api.calls.append((method, path, body)) or {})
        self.mod.api = self.api.call
        return self.mod.dedupe_account(Args(dry))

    def test_the_artists_own_upload_is_kept_over_a_re_upload(self):
        self.dedupe({"music": [
            entry("aaaaaaaaaaa", title="Queen - Bohemian Rhapsody"),
            entry("bbbbbbbbbbb", title="Bohemian Rhapsody (Full HD)")]})
        self.assertEqual(["ix-bbbbbbbbbbb"], self.api.removed())

    def test_an_earlier_lane_in_the_file_keeps_the_song(self):
        self.dedupe({"music": [entry("aaaaaaaaaaa", title="Artist - Song")],
                     "other": [entry("bbbbbbbbbbb", title="Artist - Song")]})
        self.assertEqual(["ix-bbbbbbbbbbb"], self.api.removed())

    def test_a_song_held_once_is_left_alone_but_its_key_is_recorded(self):
        self.dedupe({"music": [entry("aaaaaaaaaaa", title="Artist - Song")]})
        self.assertEqual([], self.api.removed())
        self.assertTrue([sql for sql in self.db.flat()
                         if "UPDATE suggest.items SET song_key" in sql])

    def test_a_dry_run_removes_nothing(self):
        self.dedupe({"music": [
            entry("aaaaaaaaaaa", title="Artist - Song"),
            entry("bbbbbbbbbbb", title="Artist - Song (Official Video)")]},
            dry=True)
        self.assertEqual([], self.api.removed())
        self.assertEqual([], self.db.written)


class Args:
    def __init__(self, dry_run=False):
        self.dry_run = dry_run


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""One-off: drain the channel_videos kind backlog using the same rules as
ClassifyChannelVideosJob (UUSH/UULV windows, then /shorts/<id> probes)."""
import http.client, os, subprocess, sys, time, urllib.request, xml.etree.ElementTree as ET

NS = {"yt": "http://www.youtube.com/xml/schemas/2015", "d": "http://www.w3.org/2005/Atom"}
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/128.0 Safari/537.36"


DB_CONTAINER = os.environ.get("IV_SUGGEST_DB_CONTAINER", "youtube-invidious-db-1")
DB_USER = os.environ.get("IV_SUGGEST_DB_USER", "kemal")
DB_NAME = os.environ.get("IV_SUGGEST_DB_NAME", "invidious")


def psql(sql, tuples=True):
    cmd = ["docker", "exec", "-i", DB_CONTAINER, "psql", "-U", DB_USER, "-d", DB_NAME]
    if tuples:
        cmd += ["-t", "-A", "-F", "\t"]
    cmd += ["-v", "ON_ERROR_STOP=1", "-f", "-"]
    r = subprocess.run(cmd, input=sql, capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(r.stderr.strip())
    return r.stdout.strip()


def feed(ucid, prefix):
    """(ids, oldest_published) or None when the feed could not be read."""
    url = f"https://www.youtube.com/feeds/videos.xml?playlist_id={prefix}{ucid[2:]}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=25) as resp:
            if resp.status != 200:
                return None
            body = resp.read()
    except urllib.error.HTTPError as e:
        return ([], None) if e.code == 404 else None
    except Exception:
        return None

    root = ET.fromstring(body)
    ids, oldest = [], None
    for entry in root.findall("d:entry", NS):
        vid = entry.find("yt:videoId", NS)
        if vid is None:
            continue
        ids.append(vid.text)
        pub = entry.find("d:published", NS)
        if pub is not None and (oldest is None or pub.text < oldest):
            oldest = pub.text
    return (ids, oldest)


def probe(vid):
    """'short' | 'video' | None, from the status of /shorts/<id>."""
    try:
        conn = http.client.HTTPSConnection("www.youtube.com", timeout=20)
        conn.request("HEAD", f"/shorts/{vid}", headers={"User-Agent": UA})
        code = conn.getresponse().status
        conn.close()
    except Exception:
        return None
    if code == 200:
        return "short"
    if code in (301, 302, 303, 307, 308):
        return "video"
    return None


def q(v):
    return "'" + v.replace("'", "''") + "'"


def window_pass():
    rows = psql("SELECT ucid, count(*) FROM channel_videos WHERE kind IS NULL AND ucid IS NOT NULL "
                "GROUP BY ucid ORDER BY count(*) DESC;")
    channels = [l.split("\t")[0] for l in rows.splitlines() if l.strip()]
    print(f"window pass: {len(channels)} channel(s) owing labels", flush=True)

    stmts, done = [], 0
    for ucid in channels:
        shorts, lives = feed(ucid, "UUSH"), feed(ucid, "UULV")
        if shorts is None or lives is None:
            print(f"  {ucid}: feed unreadable, skipped", flush=True)
            continue

        for ids, kind in ((shorts[0], "short"), (lives[0], "live")):
            if ids:
                lst = ", ".join(q(i) for i in ids)
                stmts.append(f"UPDATE channel_videos SET kind = '{kind}' "
                             f"WHERE kind IS NULL AND id IN ({lst});")

        if not shorts[0] and not lives[0]:
            stmts.append(f"UPDATE channel_videos SET kind = 'video' "
                         f"WHERE kind IS NULL AND ucid = {q(ucid)};")
        else:
            floors = [f for f in (shorts[1], lives[1]) if f]
            if floors:
                stmts.append(f"UPDATE channel_videos SET kind = 'video' WHERE kind IS NULL "
                             f"AND ucid = {q(ucid)} AND published >= {q(max(floors))}::timestamptz;")
        done += 1
        if done % 10 == 0:
            print(f"  {done}/{len(channels)}", flush=True)
        time.sleep(0.3)

    if stmts:
        psql("\n".join(stmts), tuples=False)
    print("window pass applied", flush=True)


def tail_pass(limit=1200):
    rows = psql(f"SELECT id FROM channel_videos WHERE kind IS NULL ORDER BY published DESC LIMIT {limit};")
    ids = [l.strip() for l in rows.splitlines() if l.strip()]
    print(f"tail pass: {len(ids)} row(s) to probe", flush=True)

    stmts = []
    for n, vid in enumerate(ids, 1):
        kind = probe(vid)
        if kind:
            stmts.append(f"UPDATE channel_videos SET kind = '{kind}' WHERE kind IS NULL AND id = {q(vid)};")
        if n % 50 == 0:
            print(f"  {n}/{len(ids)}", flush=True)
            if stmts:
                psql("\n".join(stmts), tuples=False)
                stmts = []
        time.sleep(0.25)

    if stmts:
        psql("\n".join(stmts), tuples=False)
    print("tail pass applied", flush=True)


if __name__ == "__main__":
    window_pass()
    tail_pass()
    print(psql("SELECT coalesce(kind,'(unclassified)'), count(*) FROM channel_videos "
               "GROUP BY 1 ORDER BY 2 DESC;", tuples=False))

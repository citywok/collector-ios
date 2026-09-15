#!/usr/bin/env python3
"""Phase 1: enumerate sampling frames for the speech-evidence layers.

A) Fox layer: TV News Archive episode identifiers per prime-time show,
   grouped by month, as fetchable archive.org items.
B) YouTube layer: recent video IDs for member/podcast channels (with
   URL-variant discovery when handles are unknown).
C) Rally layer: index page of Roll Call's factbase transcript library.

Writes /tmp/frames/*.json  (each: source, query/url, items, counts)
"""
import collections
import json
import os
import subprocess
import time
import urllib.parse

OUT = "/tmp/frames"
os.makedirs(OUT, exist_ok=True)
UA = "Mozilla/5.0 (X11; Linux x86_64)"

SHOWS = ["Hannity", "The Five", "Fox and Friends", "The Ingraham Angle",
         "Gutfeld", "Special Report", "Americas Newsroom",
         "The Faulkner Focus", "America Reports", "Fox News Night", "Fox News Live"]


def archive_search(q, rows=1000, pages=3):
    items = []
    for page in range(pages):
        url = ("https://archive.org/advancedsearch.php?q=" + urllib.parse.quote(q)
               + f"&fl%5B%5D=identifier&fl%5B%5D=title&fl%5B%5D=date&rows={rows}"
               + f"&page={page+1}&output=json")
        r = subprocess.run(["curl", "-s", "--max-time", "30", url],
                           capture_output=True, text=True)
        try:
            docs = json.loads(r.stdout)["response"]["docs"]
        except Exception:
            break
        if not docs:
            break
        items.extend(docs)
        if len(docs) < rows:
            break
        time.sleep(1)
    return items


def fox_layer():
    frames = {}
    for show in SHOWS:
        q = f"tvnews FOXNEWSW {show}"
        items = archive_search(q, rows=1000, pages=4)
        by_month = collections.Counter(
            (i.get("date") or "")[:7] for i in items)
        frames[show] = {
            "count": len(items),
            "months": dict(sorted(by_month.items())),
            "sample_identifiers": [i["identifier"] for i in items[:3]],
        }
        print(f"{show}: {len(items)}", flush=True)
        time.sleep(2)
    json.dump(frames, open(f"{OUT}/fox_episodes.json", "w"), indent=1)
    return frames


def youtube_layer():
    from youtube_transcript_api import YouTubeTranscriptApi
    api = YouTubeTranscriptApi()
    frames = {}
    channels = {
        "FoxNews": ["https://www.youtube.com/@FoxNews/videos"],
        "RepBrandonGill": ["https://www.youtube.com/@RepBrandonGill/videos",
                           "https://www.youtube.com/@brandon.gill/videos"],
        "SenTedCruz": ["https://www.youtube.com/@SenTedCruz/videos"],
        "RepKatCammack": ["https://www.youtube.com/@RepKatCammack/videos"],
        "CharlieKirk": ["https://www.youtube.com/@charlie-kirk/videos",
                        "https://www.youtube.com/@TurningPointUSA/videos",
                        "https://www.youtube.com/@realCharlieKirk/videos"],
        "BannonWarRoom": ["https://www.youtube.com/@WarRoomLive/videos",
                          "https://www.youtube.com/@BannonWarRoom/videos",
                          "https://www.youtube.com/@RealWarRoom/videos"],
    }
    for name, variants in channels.items():
        ids = []
        used = None
        for u in variants:
            r = subprocess.run(
                ["yt-dlp", "--flat-playlist", "--print", "%(id)s",
                 "--playlist-end", "20", u],
                capture_output=True, text=True, timeout=120)
            got = [l.strip() for l in r.stdout.split() if len(l.strip()) == 11]
            if got:
                ids, used = got, u
                break
        captions = None
        hit = 0
        if ids:
            captions = []
            for v in ids[:8]:
                try:
                    t = api.fetch(v)
                    captions.append(v)
                    hit += 1
                except Exception:
                    pass
            time.sleep(2)
        frames[name] = {"channel_url": used, "recent_ids": ids[:20],
                        "caption_hit": f"{hit}/8"}
        print(f"{name}: {len(ids)} ids, captions {hit}/8", flush=True)
    json.dump(frames, open(f"{OUT}/youtube_channels.json", "w"), indent=1)
    return frames


def rally_layer():
    url = "https://www.rollcall.com/factbase/transcripts"
    r = subprocess.run(["curl", "-sL", "--max-time", "40", "-A", UA, url],
                       capture_output=True, text=True)
    page = r.stdout or ""
    import re
    links = re.findall(r'href="(/factbase/transcript/[^"]+)"', page)
    count_ma = re.search(r"transcripts?\b[^<]{0,200}", page[:4000])
    json.dump({"url": url, "status": "ok" if page else "empty",
               "link_count": len(links), "links_sample": links[:10],
               "page_head": count_ma.group(0)[:150] if count_ma else None},
              open(f"{OUT}/rally_index.json", "w"), indent=1)
    print(f"factbase: {len(links)} transcript links", flush=True)


if __name__ == "__main__":
    fox = fox_layer()
    try:
        yt = youtube_layer()
    except Exception as e:
        yt = {"error": str(e)[:200]}
    rally_layer()
    summary = {
        "fox_total": sum(v["count"] for v in fox.values()),
        "fox_shows": {k: v["count"] for k, v in fox.items()},
        "youtube": {k: v.get("caption_hit") for k, v in yt.items()},
        "factbase": json.load(open(f"{OUT}/rally_index.json"))["link_count"],
    }
    json.dump(summary, open(f"{OUT}/phase1_summary.json", "w"), indent=1)
    print("PHASE1_DONE", summary["fox_total"], flush=True)

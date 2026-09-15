#!/usr/bin/env python3
"""Generate the phone collector's work bundle.

Discovery: YouTube RSS feeds (user=<legacy-handle>) — served by a different
edge than the innertube API, so this works even while the workstation's IP is
blocked for captions. Picks up to N recent video ids per source.

Handoff: S3 (bucket llm-chat-artifacts, prefix collector/v1/):
  public/work.json — public-read object with the batch: video ids + titles +
    short-TTL presigned PUT slots (results) + a status slot.
The app fetches work.json anonymously, fetches captions natively (device IP),
and PUTs each result to its slot. No device credentials, no server.

Usage: python3 gen_work.py [--batch 8]
"""
import argparse
import json
import subprocess
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import boto3

BUCKET = "llm-chat-artifacts"
PREFIX = "collector/v1"

SOURCES = [
    ("FoxNews", "Fox host channel"),
    ("charliekirkshow", "Charlie Kirk show"),
    ("BannonWarRoom", "War Room"),
    ("SenTedCruz", "Ted Cruz"),
    ("RepKatCammack", "Kat Cammack"),
]


def rss_latest(username):
    url = f"https://www.youtube.com/feeds/videos.xml?user={username}"
    r = subprocess.run(["curl", "-sL", "--max-time", "30", "-A", "Mozilla/5.0", url],
                       capture_output=True, text=True)
    try:
        root = ET.fromstring(r.stdout)
    except Exception:
        return []
    ns = {"a": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015"}
    items = []
    for e in root.findall("a:entry", ns):
        vid = e.find("yt:videoId", ns)
        title = e.find("a:title", ns)
        if vid is not None:
            items.append({"videoId": vid.text, "title": (title.text or "")[:80]})
    return items


def presigned_put(key, expires=86400):
    s3 = boto3.client("s3")
    return s3.generate_presigned_url(
        "put_object", Params={"Bucket": BUCKET, "Key": key}, ExpiresIn=expires)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", type=int, default=8)
    args = ap.parse_args()

    pool = []
    for user, label in SOURCES:
        for it in rss_latest(user):
            pool.append({**it, "source": label})
        time.sleep(1.0)
    if not pool:
        raise SystemExit("no RSS items fetched — check network/RSS lane")

    picked = pool[: args.batch]
    slot = subprocess.run(
        ["python3", "-c",
         "import json,sys;print(json.dumps(json.loads(sys.argv[1]),indent=1))",
         json.dumps(picked)], capture_output=True, text=True)
    items = []
    upload_slots = []
    for i, it in enumerate(picked):
        items.append({"videoId": it["videoId"], "title": it["title"], "slot": i})
        upload_slots.append(presigned_put(f"{PREFIX}/results/{datetime.now(timezone.utc).strftime('%Y%m%d')}/{i:03d}_{it['videoId']}.json"))
    status = presigned_put(f"{PREFIX}/results/{datetime.now(timezone.utc).strftime('%Y%m%d')}/status.json")
    work = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "availableInPool": len(pool),
        "items": items,
        "uploadSlots": upload_slots,
        "statusSlot": status,
    }
    key = f"{PREFIX}/public/work.json"
    s3 = boto3.client("s3")
    s3.put_object(Bucket=BUCKET, Key=key, Body=json.dumps(work).encode(), ContentType="application/json")
    print(f"WORK_WRITE done: {len(items)} items; url=https://{BUCKET}.s3.amazonaws.com/{key}")


if __name__ == "__main__":
    main()

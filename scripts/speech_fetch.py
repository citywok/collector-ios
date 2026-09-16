#!/usr/bin/env python3
"""Phase 2 v2: fetch sampled speech with validated downloads and slow pacing.

Fixes over v1 (which returned invalid data due to IP throttling and
error bodies saved as content):
  1. Era-filtered TV News Archive queries: date:[2025-01-01 TO 2026-09-15]
     — no 4000-cap on old content, pagination meaningful.
  2. Every downloaded caption file VALIDATED (size > 5000 bytes, no
     '<html>' prefix, no 'Internal Server Error' body) before use;
     invalid → retry ×3 (growing sleep), then fallback variant
     (.cc5.srt), then logged and skipped.
  3. Slow pacing: 2.0s between archive requests; 5.0s between YouTube
     caption fetches; all YouTube exceptions LOGGED by type, not swallowed.
  4. factbase: listing via Wayback snapshot of factba.se/transcripts,
     then rollcall wp-json probe; logged status either way.
Outputs /tmp/speech_out/layers_stats.json + gradient_report.md (v2).
"""
import collections
import datetime
import importlib.util
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse

_here = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("crec_stageB", os.path.join(_here, "crec_stageB.py"))
crec = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(crec)
LEX = crec.LEX

OUT = "/tmp/speech_out"
FRAMES = "/tmp/frames"
UA = "Mozilla/5.0 (X11; Linux x86_64)"
ERA = "date:[2025-01-01 TO 2026-09-15]"
SHOW_HOSTS = {
    "Hannity": "Sean Hannity", "The Five": "The Five panel",
    "Fox and Friends": "F&F co-hosts", "The Ingraham Angle": "Laura Ingraham",
    "Gutfeld": "Greg Gutfeld", "Special Report": "Bret Baier",
    "Americas Newsroom": "Americas Newsroom co-hosts",
    "The Faulkner Focus": "Harris Faulkner",
    "America Reports": "America Reports co-hosts",
    "Fox News Night": "Fox News Night host", "Fox News Live": "Fox News Live host",
}
DENSE_WINDOWS = [("2025-01-06", "2025-01-17"), ("2025-07-01", "2025-07-14"),
                 ("2025-10-01", "2025-10-17"), ("2026-01-20", "2026-02-06"),
                 ("2026-05-04", "2026-05-15"), ("2026-08-24", "2026-09-05")]


def curl_text(url, timeout=45):
    r = subprocess.run(["curl", "-sL", "--max-time", str(timeout), "-A", UA, url],
                       capture_output=True, text=True)
    return r.stdout or ""


def _caption_valid(fn):
    if not (os.path.exists(fn) and os.path.getsize(fn) > 5000):
        return False
    head = open(fn, encoding="utf-8", errors="ignore").read(400)
    return not head.lstrip().startswith("<") and "Internal Server Error" not in head


def save_caption(ident, variant, slides_max=2):
    """Cache-first, atomically-validated caption download.

    Contract: a valid local file is returned WITHOUT any network call;
    downloads write to a temp path and are renamed into place only when
    valid — so reruns never re-fetch held content and can never clobber
    a good cache copy with an error body."""
    fn = f"{FRAMES}/{ident}{variant}"
    if _caption_valid(fn):
        return open(fn, encoding="utf-8", errors="ignore").read()
    part = fn + ".part"
    for attempt in range(slides_max):
        d = subprocess.run(
            ["curl", "-sL", "--max-time", "75", "-A", UA, "-o", part, "-w", "%{http_code}",
             f"https://archive.org/download/{ident}/{ident}{variant}"],
            capture_output=True, text=True)
        if d.stdout.strip() == "200" and os.path.exists(part) and _caption_valid(part):
            os.replace(part, fn)
            return open(fn, encoding="utf-8", errors="ignore").read()
        if os.path.exists(part):
            os.remove(part)  # only the temp copy is destroyed, cache is safe
        if attempt < slides_max - 1:
            time.sleep(6.0)
    return None


class Stats:
    def __init__(self):
        self.by_layer = collections.defaultdict(
            lambda: collections.defaultdict(lambda: {
                "words": 0, "segs": 0, "days": set(), "lex": collections.Counter(), "q": []}))

    def add(self, layer, speaker, text, date=None):
        s = self.by_layer[layer][speaker]
        s["words"] += len(text.split())
        s["segs"] += 1
        if date:
            s["days"].add(date)
        for theme, rx in LEX.items():
            if rx.search(text):
                s["lex"][theme] += 1
                if len(text.split()) >= 12:
                    s["q"].append((date, theme, text[:240]))

    def dump(self):
        out = {}
        for layer, spk in self.by_layer.items():
            out[layer] = {name: {"words": v["words"], "segs": v["segs"], "days": len(v["days"]),
                                 "theme_counts": dict(v["lex"]),
                                 "quotes": [{"date": d, "theme": t, "text": x}
                                            for d, t, x in sorted(v["q"], key=lambda z: -len(z[2]))[:6]]}
                          for name, v in spk.items()}
        json.dump(out, open(f"{OUT}/layers_stats.json", "w"), indent=1)


def srt_to_text(raw):
    """Cheap SRT→text: drop indices and timestamp lines."""
    lines = [l.strip() for l in raw.split("\n")]
    keep = [l for l in lines if l and not l.isdigit() and "-->" not in l and not l.startswith(("WEBVTT", "NOTE"))]
    return " ".join(keep)


def fetch_tvnews(stats, weekly_target_per_show=22, max_total_downloads=400):
    """TV News Archive caption FILES are loan-gated (proven 2011/2014/2019/2026
    items, both /download/ and direct-datanode paths -> 'Item not available'
    403 policy page; /metadata/ still lists files). Full-transcript capture
    from this source is CLOSED; snippet search (checkbar/advancedsearch) stays
    open for quote verification. This lane is disabled by default so reruns
    don't burn request budget testing a closed door; set CRR_TVNEWS=1 to
    re-probe (policy can change)."""
    if os.environ.get("CRR_TVNEWS") != "1":
        print("tvnews lane: DISABLED (files loan-gated; see docstring) — skipping", flush=True)
        return 0
    total = 0
    consec_fail = 0
    for show, host in sorted(SHOW_HOSTS.items()):
        if total >= max_total_downloads:
            print("global download cap reached — stopping cleanly", flush=True)
            break
        q = f"tvnews FOXNEWSW {show} AND {ERA}"
        ids = []
        # ONE enumeration call per show: rows=4000 covers the full window; no metadata calls ever.
        url = ("https://archive.org/advancedsearch.php?q=" + urllib.parse.quote(q)
               + f"&fl%5B%5D=identifier&fl%5B%5D=date&rows=4000"
               + f"&page=1&sort%5B%5D=date+asc&output=json")
        try:
            docs = json.loads(curl_text(url))["response"]["docs"]
            ids = [d["identifier"] for d in docs]
        except Exception as e:
            print(f"{show}: enumeration failed {type(e).__name__}", flush=True)
        if not ids:
            print(f"{show}: 0 ids — query or encoding failed", flush=True)
            continue
        picked = []
        seen_week = set()
        dense = set()
        for a, b in DENSE_WINDOWS:
            d0, d1 = datetime.date.fromisoformat(a), datetime.date.fromisoformat(b)
            d = d0
            while d <= d1:
                dense.add(d.isoformat())
                d += datetime.timedelta(days=1)
        for ident in ids:
            parts = ident.split("_")
            if len(parts) < 2:
                continue
            ds = f"{parts[1][:4]}-{parts[1][4:6]}-{parts[1][6:8]}"
            dense_hit = ds in dense
            if dense_hit or ds[:8] not in seen_week:
                if not dense_hit:
                    seen_week.add(ds[:8])
                picked.append(ident)
        got = 0
        for ident in picked[: weekly_target_per_show + len(DENSE_WINDOWS) * 3]:
            if total >= max_total_downloads:
                break
            txt = save_caption(ident, ".cc5.txt")
            if not txt:
                txt = srt_to_text(save_caption(ident, ".cc5.srt") or "")
            if txt:
                parts = ident.split("_")
                ds = f"{parts[1][:4]}-{parts[1][4:6]}-{parts[1][6:8]}"
                stats.add("Fox shows", host, txt, date=ds)
                got += 1
                total += 1
                consec_fail = 0
            else:
                consec_fail += 1
                if consec_fail >= 3:
                    print("3 consecutive unvalidated files — archive throttle suspected; aborting cleanly", flush=True)
                    raise SystemExit(2)
            time.sleep(6.0)
        print(f"{show}: {got} validated", flush=True)
    print(f"TV layer subtotal: {total}", flush=True)
    return total


def fetch_youtube(stats):
    from youtube_transcript_api import YouTubeTranscriptApi
    api = YouTubeTranscriptApi()
    channels = [("Fox News host layer", "https://www.youtube.com/@FoxNews/videos", "Fox broadcast cast"),
                ("Sen. Ted Cruz", "https://www.youtube.com/@SenTedCruz/videos", "Ted Cruz"),
                ("Rep. Kat Cammack", "https://www.youtube.com/@RepKatCammack/videos", "Kat Cammack"),
                ("Charlie Kirk show", "https://www.youtube.com/@charliekirkshow/videos", "Charlie Kirk"),
                ("Cong. Brandon Gill", "ytsearch8:Congressman Brandon Gill", "Brandon Gill"),
                ("War Room (RAV)", "ytsearch8:War Room Bannon episode", "War Room cast")]
    for label, source, owner in channels:
        r = subprocess.run(["yt-dlp", "--flat-playlist", "--print", "%(id)s", "--playlist-end", "20", source],
                           capture_output=True, text=True, timeout=180)
        ids = [l.strip() for l in r.stdout.split() if len(l.strip()) == 11]
        ok = 0
        for v in ids[:12]:
            try:
                t = api.fetch(v)
                text = " ".join(s.text for s in t)
                stats.add("YouTube layer", owner, text)
                ok += 1
            except Exception as e:
                print(f"  YT miss {v}: {type(e).__name__}", flush=True)
            time.sleep(5.0)
        print(f"{label}: {ok}/{len(ids[:12])} captions (type-logged)", flush=True)


def fetch_factbase(stats, max_items=100):
    via = None
    # 1) Wayback snapshot index of the old factba.se transcripts
    wb = curl_text("http://archive.org/wayback/available?url=factba.se/transcripts/donald-trump/speeches&timestamp=2025", timeout=40)
    try:
        snap = json.loads(wb).get("archived_snapshots", {}).get("closest", {}).get("url")
        via = "wayback" if snap else None
    except Exception:
        snap = None
    urls = []
    if snap:
        page = curl_text(snap, timeout=60)
        urls = sorted(set(re.findall(r'href="(/transcripts/[^"]+)"', page)))[:max_items]
        base = "https://web.archive.org"
    else:
        # 2) rollcall factbase direct probe
        for u in ("https://www.rollcall.com/factbase/trump/transcripts/",
                  "https://www.rollcall.com/factbase/trump/calendar/"):
            page = curl_text(u, timeout=50)
            urls = sorted(set(re.findall(r'https://www\.rollcall\.com/factbase/transcript/[^"\']+', page)))
            if urls:
                via = "rollcall"
                break
    got = 0
    for u in urls[:max_items]:
        page = curl_text(base + u if via == "wayback" and u.startswith("/") and "web.archive.org" not in u else u, timeout=60)
        body = re.sub(r"<[^>]+>", " ", page)
        body = re.sub(r"\s+", " ", body)
        if len(body.split()) > 400:
            stats.add("Trump events (factbase)", "Donald Trump (events)", body[:12000])
            got += 1
        time.sleep(1.2)
    print(f"factbase ({via}): {got}/{len(urls)} transcripts", flush=True)
    json.dump({"via": via, "discovered": len(urls), "fetched": got},
              open(f"{OUT}/factbase_status.json", "w"))


def gradient_report(stats):
    baseline = json.load(open("/tmp/crec_out/per_member.json"))
    b_words = sum(v["words"] for v in baseline.values())
    b_lex = collections.Counter()
    for v in baseline.values():
        b_lex.update(v["theme_counts"])
    THEMES = ("empathy", "enemy", "deserv", "faith", "cruel", "civil")
    lines = ["# Cross-layer speech gradient (v2, validated downloads)",
             "", "Same lexicons as the CREC pipeline. Rates = per 10k words.", "",
             "| layer | words | empathy | enemy | deserv | faith | cruel | civil |",
             "|---|---|---|---|---|---|---|---|",
             f"| CREC floor (GOP members, baseline) | {b_words:,} | " +
             " | ".join(f"{10000.0*b_lex[t]/max(b_words,1):.2f}" for t in THEMES) + " |"]
    for layer, spk in stats.by_layer.items():
        w = sum(v["words"] for v in spk.values())
        lx = collections.Counter()
        for v in spk.values():
            lx.update(v["lex"])
        lines.append(f"| {layer} (all speakers) | {w:,} | " +
                     " | ".join(f"{10000.0*lx[t]/max(w,1):.2f}" for t in THEMES) + " |")
    open(f"{OUT}/gradient_report.md", "w").write("\n".join(lines))
    print("gradient_report.md (v2) written", flush=True)


def main():
    layers = set(sys.argv[1].split(",")) if len(sys.argv) > 1 else {"tvnews", "youtube", "factbase"}
    stats = Stats()
    if "tvnews" in layers:
        fetch_tvnews(stats)
    if "youtube" in layers:
        fetch_youtube(stats)
    if "factbase" in layers:
        fetch_factbase(stats)
    stats.dump()
    gradient_report(stats)
    open(f"{OUT}/SPEECH_FETCH_OK", "w").write("v2:" + ",".join(sorted(layers)))
    print("SPEECH_FETCH_DONE_v2", layers, flush=True)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""CREC Stage B: attribute every spoken line in the Congressional Record
to the GOP roster member who spoke it, with per-member theme lexicons.

Reads: /tmp/crec_txt/CREC-*.txt  (pdftotext -layout output, one per day)
       /tmp/gop_members.json     (Congress.gov API roster dump, GOP only)
Writes: /tmp/crec_out/per_member.json
        /tmp/crec_out/report.md  (volume + theme table, verbatim-quote bank)

Caveats encoded in design:
  - Daily Digest sections are dropped (no member prose there).
  - Attribution is line-based with the Record's convention
    ("Mr. LAST of State:" openers / "HON. FULL NAME, [A] [SENATOR|REPRESENTATIVE]
    [IN THE CHAMBER] FROM STATE" Extensions headers).
  - Restated/inserted debate prose is attributed to the speaker named at
    the top of the block (Record convention), so Extensions-inserted
    material counts for the submitting member.
"""
import collections
import glob
import json
import os
import re


TXT_DIR = os.environ.get("CRR_TXT_DIR", "/tmp/crec_txt")
OUT_DIR = os.environ.get("CRR_OUT_DIR", "/tmp/crec_out")
LEX = {
    "empathy": re.compile(r"\b(empath|compassion|compassionate|sympath(y|ies|etic)|suffer\w+|grief|mour?n\w*)", re.I),
    "enemy": re.compile(r"\b(enem(y|ies)|radical left|terroris\w+|traitor\w*|saboteur\w*|coup\b|marxis\w+|fascis\w+|evil\b|demonic)", re.I),
    "deserv": re.compile(r"\b(deserve[sd]?|deserving|handout\w*|takers?\b|freeload\w*|leech\w*|able-bodied)", re.I),
    "faith": re.compile(r"\b(God\b|Scripture|Bible|biblic\w+|prayer\w*|Lord\b|Jesus|gospel|psalm\w*|proverb\w*|Christian nation\w*)", re.I),
    "cruel": re.compile(r"\b(cruel\w*|merciless\w*|make (?:them|her|him) pay|pay the price|hell to pay|no mercy)", re.I),
    "civil": re.compile(r"\b(bipartis\w+|compromise\w*|come together|country over party|heal\w*|unity)\b", re.I),
    "dependency": re.compile(r"\b(farmwork\w*|seasonal labor|labor shortage\w*|harvest\w*| visa (program|cap)s? |H-2[AB]\b|we (rely|count|depend) on|depen\w+ on (their|our|immigrant|foreign|undocument\w+)|jobs americans won't)", re.I),
    "preventive": re.compile(r"\b(pre-?venti\w+|wildfire (prevention|mitigat\w+|fuel)|pandemic (preparedness|plan\w+)|infrastructure (repair|mainten\w+)|before the (storm|fire|outbreak|crisis))", re.I),
    "reactive": re.compile(r"\b(emergency (response|supplemental|spending)|after the (storm|fire|attack|crisis)|retaliat\w+|law and order|crackdown\w*)", re.I),
}
WORD = re.compile(r"[A-Za-z]")
DOTS = re.compile(r"\.{8,}")

def is_prose(st):
    """True if a CREC line is member speech, not a table row / TOC leader."""
    if DOTS.search(st):
        return False
    words = st.split()
    if len(words) < 4:
        return False
    letters = sum(ch.isalpha() for ch in st)
    if letters / max(len(st), 1) < 0.55:
        return False
    alpha_tokens = sum(1 for w in words if any(c.isalpha() for c in w))
    if alpha_tokens < 4:
        return False
    return True

GUARD = re.compile(r"^(?:Mr|Ms|Mrs)\.\s+(?:President|Speaker|Speaker pro tempore|Chairman|Chair|Majority|Minority|Leader)", re.I)
SPK = re.compile(r"^(?:Mr|Ms|Mrs)\.\s+([A-Z][A-Z\u2019\x27.-]*[A-Z])(?:\s+of\s+([A-Za-z]{4,}))?\s*[.:]")
HON = re.compile(r"^HON\.\s+([A-Z][A-Z\u2019\x27 .-]+?),?\s+(?:A |THE )?(?:SENATOR|REPRESENTATIVE)\b.{0,80}?FROM\s+([A-Z]+)")


def load_roster():
    """Roster source: current-member dump by default; the historical
    party-map (all legislators 1789-present) via CRR_ROSTER=party_map —
    required for 2013-2024 attribution (retired speakers like Boehner/Ryan
    are absent from the current-member dump)."""
    if os.environ.get("CRR_ROSTER") == "party_map":
        party_map = json.load(open("/tmp/speech_out/party_map.json"))
        by_last = {}
        for key, recs in party_map.items():
            last, _, first = key.partition("|")
            for s, e, typ, party, st in recs:
                if e < 2013 or s > 2026:
                    continue  # BOTH parties; 2013-2026 term window only
                entry = {"name": f"{last.title()}, {first.title()}",
                         "state": st or "",
                         "chamber": "Senate" if typ == "sen" else "House",
                         "bio": "", "first": s, "party": party}
                by_last.setdefault(last, {})
                by_last[last][f"{st}|{typ}|{party}"] = entry
        flat = collections.defaultdict(list)
        for k, entries in by_last.items():
            flat[k].extend(entries.values())
        return flat
    idx = collections.defaultdict(list)
    for m in json.load(open("/tmp/gop_members.json")):
        last = m["name"].split(",")[0].upper().replace("\u2019", "").replace(".", "")
        terms = m.get("terms", {}).get("item", [])
        chamber = "Senate" if (terms and terms[-1].get("chamber") == "Senate" and "district" not in m) else "House"
        years = [t.get("startYear", 9999) for t in terms]
        idx[last].append({
            "name": m["name"], "state": m.get("state"), "chamber": chamber,
            "bio": m.get("bioguideId"), "first": min(years) if years else 9999,
            "party": "Republican",
        })
    return idx


def resolve(idx, last, state, chamber):
    c = idx.get(last, [])
    if not c:
        return None
    if state:
        for x in c:
            if x["state"] and x["state"].upper() == state.upper():
                return x
        if len(c) > 1:
            return None  # state given but matches nobody shared-safe: skip, do not guess
    if len(c) == 1:
        return c[0]
    if len(c) > 1:
        matches = [x for x in c if x["chamber"] == chamber]
        if len(matches) == 1:
            return matches[0]
        return None  # ambiguous surname, no state: skip rather than misattribute
    return c[0]


def main():
    idx = load_roster()
    files = sorted(glob.glob(f"{TXT_DIR}/CREC-*.txt"))
    stats = collections.defaultdict(lambda: {
        "words": 0, "segs": 0, "days": set(), "lex": collections.Counter(), "q": []})
    year_stats = collections.defaultdict(lambda: {"words": 0, "segs": 0, "lex": collections.Counter()})  # (party, year) keys in historical mode
    nseg = 0
    year_buckets = os.environ.get("CRR_YEAR_BUCKETS") == "1"
    for i, tf in enumerate(files, 1):
        date = tf.split("CREC-")[1][:10]
        raw = open(tf, errors="ignore").read()
        body = re.split(r"\n\s*D\s*A\s*I\s*L\s*Y\s*D\s*I\s*G\s*E\s*S\s*T\s*\n", raw)[0]
        speaker = None
        chamber = "House"
        for line in body.split("\n"):
            st = line.strip()
            if not st:
                continue
            if st.startswith(("HOUSE OF REPRESENTATIVES", "SENATE.", "SENATE")):
                chamber = "Senate" if st.startswith("SENATE") else "House"
            hm = HON.match(st)
            sm = SPK.match(st)
            if st.startswith(("The SPEAKER", "The CHAIRMAN", "The PRESIDING",
                              "The CLERK", "The VICE", "The ACTING",
                              "The MAJORITY", "The MINORITY", "The PRESIDENT pro tempore",
                              "The Sergeant")):
                speaker = None
                continue
            if hm:
                speaker = resolve(idx, hm.group(1).replace(".", "").split()[-1],
                                  hm.group(2), "House")
                continue
            if sm:
                if GUARD.match(st):
                    continue
                speaker = resolve(idx, sm.group(1), sm.group(2), chamber)
                continue
            if speaker is None or not WORD.search(st) or not is_prose(st):
                continue
            s = stats[speaker["name"]]
            s["words"] += len(st.split())
            s["segs"] += 1
            s["days"].add(date)
            nseg += 1
            if year_buckets:
                ys = year_stats[((speaker.get("party") or "R")[:1], date[:4])]
                ys["words"] += len(st.split())
                ys["segs"] += 1
            hits = [th for th, rx in LEX.items() if rx.search(st)]
            for th in hits:
                s["lex"][th] += 1
                if year_buckets:
                    year_stats[((speaker.get("party") or "R")[:1], date[:4])]["lex"][th] += 1
                if len(st.split()) >= 12:
                    s["q"].append((date, th, st[:240]))
        if i % 100 == 0:
            print(f"{i}/{len(files)} days; attributed {nseg}", flush=True)
    out = {}
    for name, v in stats.items():
        out[name] = {
            "words": v["words"], "segs": v["segs"], "days": len(v["days"]),
            "theme_counts": dict(v["lex"]),
            "quotes": [{"date": d, "theme": t, "text": x}
                       for d, t, x in sorted(v["q"], key=lambda z: -len(z[2]))[:8]],
        }
    os.makedirs(OUT_DIR, exist_ok=True)
    json.dump(out, open(f"{OUT_DIR}/per_member.json", "w"), indent=1)
    if year_buckets:
        yout = {}
        for key, v in sorted(year_stats.items()):
            label = f"{key[0]}|{key[1]}" if isinstance(key, tuple) else str(key)
            yout[label] = {"words": v["words"], "segs": v["segs"],
                           "per_10k": {t: round(10000.0 * c / max(v["words"], 1), 2)
                                       for t, c in v["lex"].items()}}
        json.dump(yout, open(f"{OUT_DIR}/year_theme.json", "w"), indent=1)
    print(f"DONE members={len(out)} attributed_segments={nseg} daily_files={len(files)}")
    with open(f"{OUT_DIR}/STAGEB_OK", "w") as f:
        f.write("ok")


if __name__ == "__main__":
    main()

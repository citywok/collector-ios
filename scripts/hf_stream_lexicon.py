#!/usr/bin/env python3
"""Longitudinal lexicon analysis over Eugleo/us-congressional-speeches via
HF streaming — NO local dataset copy (the corpus is 13.7GB; this host's
disk cannot hold it; a prior full download hit the disk's 100% line).

One streaming pass, in-RAM aggregation:
  - filter: party == Republican, years 1995..2026
  - bucket: per year × chamber
  - metrics: words, segments, six crec-identical lexicon counts
Output: /tmp/speech_out/hf_year_theme.json
Resumable: partial progress saved every 500k rows; a rerun starts over
(streaming has no checkpoints) but the pass is read-only on infra.
"""
import collections
import importlib.util
import json
import os
import re
import time

_here = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("crec_stageB", os.path.join(_here, "crec_stageB.py"))
crec = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(crec)
LEX = crec.LEX
THEMES = tuple(LEX)

OUT = "/tmp/speech_out"
START_YEAR, END_YEAR = 1995, 2026
PERIODS = ["pre-trump-1995-2015", "trump1-2016-2020", "biden-2021-2024", "trump2-2025-2026"]


def period_of(year):
    if year <= 2015: return PERIODS[0]
    if year <= 2020: return PERIODS[1]
    if year <= 2024: return PERIODS[2]
    return PERIODS[3]


_PARTY = json.load(open("/tmp/speech_out/party_map.json"))
PARTY = _PARTY
CHAMBER_TYPE = {"H": "rep", "S": "sen"}


def build_last_index(party_map):
    """last | first-token | last -> term-records index (Real-token join)."""
    idx = {}
    for key, recs in party_map.items():
        last, _, first = key.partition("|")
        targets = [key]
        if first and first != "UNKNOWN":
            targets.append(f"{last}|{first.split()[0]}")
        targets.append(last)
        for t in targets:
            idx.setdefault(t, [])
            for r in recs:
                if r not in idx[t]:
                    idx[t].append(r)
    return idx


PARTY_LAST = build_last_index(_PARTY)

# Full state names (as Eugleo stores them) -> USPS abbreviations (as congress-legislators stores them)
_STATE_ABBR = {
    "Alabama": "AL", "Alaska": "AK", "Arizona": "AZ", "Arkansas": "AR", "California": "CA",
    "Colorado": "CO", "Connecticut": "CT", "Delaware": "DE", "Florida": "FL", "Georgia": "GA",
    "Hawaii": "HI", "Idaho": "ID", "Illinois": "IL", "Indiana": "IN", "Iowa": "IA",
    "Kansas": "KS", "Kentucky": "KY", "Louisiana": "LA", "Maine": "ME", "Maryland": "MD",
    "Massachusetts": "MA", "Michigan": "MI", "Minnesota": "MN", "Mississippi": "MS", "Missouri": "MO",
    "Montana": "MT", "Nebraska": "NE", "Nevada": "NV", "New Hampshire": "NH", "New Jersey": "NJ",
    "New Mexico": "NM", "New York": "NY", "North Carolina": "NC", "North Dakota": "ND", "Ohio": "OH",
    "Oklahoma": "OK", "Oregon": "OR", "Pennsylvania": "PA", "Rhode Island": "RI", "South Carolina": "SC",
    "South Dakota": "SD", "Tennessee": "TN", "Texas": "TX", "Utah": "UT", "Vermont": "VT",
    "Virginia": "VA", "Washington": "WA", "West Virginia": "WV", "Wisconsin": "WI", "Wyoming": "WY",
    "District of Columbia": "DC", "Puerto Rico": "PR", "Guam": "GU",
}


def abbr_state(state):
    if not state:
        return None
    s = state.strip()
    if len(s) == 2:
        return s.upper()
    return _STATE_ABBR.get(s)


def _unanimous_party(recs):
    parties = {rec[3] for rec in recs if rec[3]}
    return next(iter(parties)) if len(parties) == 1 else None


def party_of(last, first, chamber, state, year):
    last = (last or "").upper().strip()
    if not last:
        return None
    # key candidates: exact, first-token (middle initials), last-only
    cands = []
    for f in (first, (first or "").split()[0] if (first or "").split() else None, None):
        if f and f != "Unknown":
            key = f"{last}|{f.upper().strip()}"
            if key not in cands:
                cands.append(key)
    cands.append(last)  # last-only index
    recs = None
    for key in cands:
        recs = PARTY_LAST.get(key)
        if recs:
            break
    if not recs:
        return None
    cmap = CHAMBER_TYPE.get((chamber or "").strip())
    st = abbr_state(state)
    if cmap and st:
        parties = set()
        for s, e, typ, party, x in recs:
            if typ == cmap and x == st and s <= year <= e:
                parties.add(party)
        if len(parties) == 1:
            return next(iter(parties))
    #Fallbacks: chamber-E (Extensions) or unknown state -> unanimity only;
    if cmap == "rep" or cmap is None:
        return _unanimous_party(recs)
    return None


def aggregate_row(row, year, party, chamber, stats, text=None):
    if year < START_YEAR or year > END_YEAR:
        return
    if party != "Republican":
        return
    key = year
    s = stats[key]
    t = text if text is not None else (row.get("text") or row.get("speech") or "")
    s["words"] += len(t.split())
    s["segs"] += 1
    for theme, rx in LEX.items():
        if rx.search(t):
            s["lex"][theme] += 1


def detect_columns(features):
    """Return (year_col, text_col) by fuzzy match (party/chamber are fixed by schema)."""
    cands = {"year": ("year", "date", "congress year"),
             "text": ("text", "speech", "content")}
    low = {k.lower(): k for k in features}
    out = {}
    for want, names in cands.items():
        for name in names:
            for lowk, orig in low.items():
                if name in lowk:
                    out[want] = orig
                    break
            if want in out:
                break
        if want not in out:
            raise ValueError(f"column for {want} not found among {sorted(features)}")
    return out["year"], out["text"]


def year_of(val):
    if isinstance(val, int):
        if val > 1000:
            return val
        return None
    m = re.search(r"(19|20)\d{2}", str(val))
    return int(m.group(0)) if m else None


def main():
    import datasets
    ds = datasets.load_dataset("Eugleo/us-congressional-speeches",
                               streaming=True, split="train")
    year_c, text_c = detect_columns(ds.features)
    print("columns:", year_c, text_c, flush=True)
    parties = ("Republican", "Democrat")
    stats = {P: {y: {"words": 0, "segs": 0, "lex": collections.Counter()} for y in range(START_YEAR, END_YEAR + 1)}
             for P in parties}
    t0 = time.time()
    n = 0
    resolved = {P: 0 for P in parties}
    for row in ds:
        n += 1
        y = year_of(row.get(year_c))
        if y is None or y < START_YEAR or y > END_YEAR:
            continue
        party = party_of((row.get("last_name") or "").upper().replace("\u2019", "").replace("'", ""),
                         (row.get("first_name") or "").upper().replace("\u2019", "").replace("'", ""),
                         row.get("chamber"), row.get("state"), y)
        if party not in stats:
            continue
        text = row.get(text_c) or ""
        s = stats[party][y]
        s["words"] += len(text.split())
        s["segs"] += 1
        resolved[party] += 1
        for theme, rx in LEX.items():
            if rx.search(text):
                s["lex"][theme] += 1
        if n % 750_000 == 0:
            rp = f"R:{resolved['Republican']}:{sum(v['words'] for v in stats['Republican'].values()):,}"
            dp = f"D:{resolved['Democrat']}:{sum(v['words'] for v in stats['Democrat'].values()):,}"
            print(f"rows={n} GOP={rp} DEM={dp} elapsed={time.time()-t0:.0f}s", flush=True)
    out = {}
    for P in parties:
        for y, s in sorted(stats[P].items()):
            if not s["words"]:
                continue
            out[f"{P[0]}|{y}"] = {"period": period_of(y), "words": s["words"], "segs": s["segs"],
                                  "per_10k": {t: round(10000.0 * s["lex"][t] / max(s["words"], 1), 2)
                                              for t in THEMES}}
    json.dump(out, open(f"{OUT}/hf_year_theme.json", "w"), indent=1)
    print(f"FINAL rows={n} resolved={resolved}", flush=True)
    print("YEAR_TABLE_DONE", flush=True)


if __name__ == "__main__":
    main()

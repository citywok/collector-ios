#!/usr/bin/env python3
"""Build a party lookup from unitedstates/congress-legislators YAML.

Output: /tmp/speech_out/party_map.json
  {"LASTNAME|FIRSTNAME|ST|chamber|year": "Republican", ...}  is too big —
  instead we store term records and resolve at query time:
  {"LAST|FIRST|ST": [[start_year, end_year, "rep"|"sen", "party"], ...]}
Resolution: (name, state) -> terms; match chamber rep/sen and year in range.
Ambiguous duplicated names may carry disjoint chambers — still resolvable.
"""
import json
import os
import subprocess
import urllib.parse

OUT = "/tmp/speech_out"
os.makedirs(OUT, exist_ok=True)
BASE = "https://raw.githubusercontent.com/unitedstates/congress-legislators/main"


def fetch(name):
    fn = f"/tmp/{name}"
    if not os.path.exists(fn):
        r = subprocess.run(["curl", "-sL", "--max-time", "120", "-A", "Mozilla/5.0",
                            "-o", fn, f"{BASE}/{name}"],
                           capture_output=True, text=True)
        ok = r.returncode == 0 and os.path.getsize(fn) > 100_000
        assert ok, f"download failed: {name}"
    import yaml
    return yaml.safe_load(open(fn, encoding="utf-8").read()[:50_000_000])


def normalize(s):
    return (s or "").strip().upper().replace("'", "").replace("\u2019", "")


def main():
    legis = fetch("legislators-historical.yaml") + fetch("legislators-current.yaml")
    party_map = {}
    for p in legis:
        name = p.get("name", {})
        last = normalize(name.get("last"))
        first = normalize(name.get("first"))
        if not last or not first:
            continue
        key = f"{last}|{first}"
        recs = party_map.setdefault(key, [])
        for t in p.get("terms", []):
            party = t.get("party")
            if not party:
                continue
            try:
                s = int(t.get("start", "0000")[:4])
                e = int(t.get("end", "9999")[:4])
            except (ValueError, TypeError):
                continue
            recs.append([s, e, t.get("type"), party, (t.get("state") or "").upper()])
    json.dump(party_map, open(f"{OUT}/party_map.json", "w"))
    names = len(party_map)
    terms = sum(len(v) for v in party_map.values())
    print(f"PARTY_MAP_DONE names={names} terms={terms}")


if __name__ == "__main__":
    main()

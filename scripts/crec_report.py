#!/usr/bin/env python3
"""CREC Stage C: build the per-member research report.

Reads: /tmp/crec_out/per_member.json (Stage B output, fixed attribution)
       /tmp/gop_members.json        (roster w/ terms -> first-elected year)
Writes: /tmp/crec_out/gop_record_report.md

Outputs: volume table (top 40), theme rates per 10k words, the
"up and coming" cohort (first elected >= 2022), and corpus totals.
"""
import collections
import json

OUT_DIR = "/tmp/crec_out"

THEMES = ["empathy", "enemy", "deserv", "faith", "cruel", "civil"]
THEME_LABEL = {
    "empathy": "empathy/compassion",
    "enemy": "enemy/framing",
    "deserv": "deservingness",
    "faith": "faith/religion",
    "cruel": "cruelty",
    "civil": "civility/unity",
}


def main():
    per = json.load(open(f"{OUT_DIR}/per_member.json"))
    roster = {m["name"]: m for m in json.load(open("/tmp/gop_members.json"))}
    senators = set()
    for name, m in roster.items():
        terms = m.get("terms", {}).get("item", [])
        if terms and terms[-1].get("chamber") == "Senate" and "district" not in m:
            senators.add(name)

    rows = []
    for name, v in per.items():
        r = roster.get(name, {})
        years = [t.get("startYear", 9999) for t in r.get("terms", {}).get("item", [])]
        first = min(years) if years else 9999
        rows.append({
            "name": name, "senate": name in senators, "first": first,
            "words": v["words"], "segs": v["segs"], "days": v["days"],
            "themes": v["theme_counts"], "quotes": v["quotes"],
            "rate": {t: round(10000.0 * v["theme_counts"].get(t, 0) / max(v["words"], 1), 2)
                     for t in THEMES},
        })

    total_words = sum(r["words"] for r in rows)
    tot = collections.Counter()
    for r in rows:
        for t, c in r["themes"].items():
            tot[t] += c

    lines = [
        "# GOP members in the Congressional Record, Jan 3 2025 - Sep 12 2026",
        "",
        f"Corpus: 355 published daily editions (complete publication set; other calendar days had no edition).",
        f"Attributed: {len(rows)}/{len(roster)} roster members; {sum(r['segs'] for r in rows):,} segments; {total_words:,} words.",
        "",
        "## Corpus theme totals (and per-10k-words rate)",
        "", "| theme | mentions | per 10k words |", "|---|---|---|",
    ]
    for t in THEMES:
        lines.append(f"| {THEME_LABEL[t]} | {tot[t]:,} | {10000.0*tot[t]/max(total_words,1):.2f} |")

    lines += ["", "## Top 40 members by floor volume", "",
              "| member | chamber | first elected | words | segments | active days |", "|---|---|---|---|---|---|"]
    for r in sorted(rows, key=lambda x: -x["words"])[:40]:
        lines.append(f"| {r['name']} | {'Senate' if r['senate'] else 'House'} | {r['first']} | {r['words']:,} | {r['segs']:,} | {r['days']} |")

    lines += ["", "## Theme intensity leaders (per 10k words, min 50k spoken words)", "", "| theme | top 5 members (rate) |", "|---|---|"]
    for t in THEMES:
        elite = [r for r in rows if r["words"] >= 50_000]
        top = sorted(elite, key=lambda x: -x["rate"][t])[:5]
        lines.append(f"| {THEME_LABEL[t]} | " + "; ".join(f"{r['name']} ({r['rate'][t]})" for r in top) + " |")

    lines += ["", "## Up and coming: first elected 2022 or later", "",
              "| member | words | segments | active days |", "|---|---|---|---|"]
    young = [r for r in rows if r["first"] >= 2022]
    for r in sorted(young, key=lambda x: -x["words"])[:20]:
        lines.append(f"| {r['name']} | {r['words']:,} | {r['segs']:,} | {r['days']} |")

    lines += ["", "## Verbatim exemplars (longest zero-digit attributed line per theme)", ""]
    for t in THEMES:
        best = None
        for r in rows:
            for q in r["quotes"]:
                if q["theme"] != t or any(ch.isdigit() for ch in q["text"]):
                    continue
                if best is None or len(q["text"]) > len(best[0]["text"]):
                    best = (q, r)
        if best:
            q, r = best
            lines.append(f"- **{THEME_LABEL[t]}** — {r['name']} ({q['date']}): “{q['text'][:200]}…”")

    md = "\n".join(lines)
    open(f"{OUT_DIR}/gop_record_report.md", "w").write(md)
    print(f"report written: {len(md)} chars")


if __name__ == "__main__":
    main()

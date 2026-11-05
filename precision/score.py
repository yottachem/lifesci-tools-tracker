"""Precision per entry from the labeled samples, under the current queries and terms.

Samples (precision/{technology}_{signal}.csv):
  platforms  brand queries (sample_platforms.py)   re-checked against OpenAlex default.search
  methods    method terms (sample_terms.py)         re-checked against OpenAlex title_and_abstract.search
  nih        RePORTER terms                         re-checked against the latest RePORTER snapshot
  patents    patent terms                           re-checked against the latest USPTO snapshot

A query that is narrowed (context terms added, OR branches dropped, type filter) returns a subset
of the works it returned when sampled, so the labeled rows it still matches are a uniform random
sample of its new results: precision is re-scored without relabeling. A query that is broadened
needs a fresh sample. Patent rows are kept when the patent still matches the technology in the
latest USPTO snapshot (re-run ingest.uspto after changing patent_terms); the entry shown is the
sampled phrase set.

Writes precision/summary.csv (per entry) and precision/spot_check.csv (rows for Josh: the random
10%, labels inferred from a repeated pattern, and judgment calls, limited to rows still kept).
Precision = yes / (yes + no), with a 95% Wilson interval; low = precision under LOW.
precision/reviewed.csv records rows Josh has spot-checked (python -m precision.score --reviewed
marks the current spot_check.csv done); they leave spot_check.csv but keep their josh_label.
Josh's josh_label, where filled, overrides label. Each run first copies josh_label from
spot_check.csv into the samples, so regenerating spot_check.csv never loses it.

Run: uv run python -m precision.score
"""
import csv
import json
import math

from ingest.common import ROOT, load_taxonomies, method_term, platforms
from ingest.openalex import get, method_query, platform_query, works_filter

OUT = ROOT / "precision"
RAW = ROOT / "data" / "raw"
LOW = 0.8
SIGNALS = ("platforms", "methods", "nih", "patents")
SPOT_FIELDS = ["technology", "signal", "entry", "id", "year", "type", "title", "evidence", "snippet",
               "label", "reason", "josh_check", "josh_label"]


def norm(r, signal):
    """Platform samples name their columns platform/work_id; term samples entry/id."""
    return {**r, "signal": signal, "entry": r.get("entry") or r.get("platform"), "id": r.get("id") or r.get("work_id")}


def openalex_kept(ids, field, query):
    """Work ids from ids that the query (with the type filter) still matches."""
    out = set()
    for i in range(0, len(ids), 50):
        params = {"filter": f"openalex:{'|'.join(ids[i:i + 50])}," + works_filter(field, query),
                  "per-page": 50, "select": "id"}
        r = get(params, f"re-scoring {query}")
        out |= {w["id"].rsplit("/", 1)[-1] for w in r.json()["results"]}
    return out


def latest(source):
    snap = json.load(open(sorted((RAW / source).glob("*.json"))[-1]))
    return snap.get("data", snap)


def groups(tech_id, tech, signal, rows):
    """(entry, sampled rows, ids still matched) for each entry of one sample file."""
    if signal == "platforms":
        for _, p in platforms(tech):
            sampled = [r for r in rows if r["entry"] == p["name"]]
            yield p["name"], sampled, openalex_kept([r["id"] for r in sampled], "default.search", platform_query(p))
    elif signal == "methods":
        for m in tech["method_terms"]:
            sampled = [r for r in rows if r["entry"] == method_term(m)]
            yield method_term(m), sampled, openalex_kept([r["id"] for r in sampled], "title_and_abstract.search", method_query(m))
    elif signal == "nih":
        ids = {str(p["appl_id"]) for p in latest("reporter")[tech_id]}
        yield "all nih_terms", rows, {r["id"] for r in rows if r["id"] in ids}
    elif signal == "patents":
        snap = latest("uspto")
        ids = {h["id"] for kind in ("granted", "pregrant") for h in snap[kind] if tech_id in h["technologies"]}
        yield "all patent_terms", rows, {r["id"] for r in rows if r["id"] in ids}


def merge_spot_checks():
    path = OUT / "spot_check.csv"
    if not path.exists():
        return
    josh = {}
    for r in csv.DictReader(open(path)):
        if lab := r["josh_label"].strip():
            r = norm(r, r.get("signal") or "platforms")
            josh[(r["technology"], r["signal"], r["entry"], r["id"])] = lab
    for tech_id, signal in {(t, s) for t, s, _, _ in josh}:
        sample = OUT / f"{tech_id}_{signal}.csv"
        rows = list(csv.DictReader(open(sample)))
        for r in rows:
            n = norm(r, signal)
            r["josh_label"] = josh.get((tech_id, signal, n["entry"], n["id"]), r["josh_label"])
        with open(sample, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)


def reviewed():
    path = OUT / "reviewed.csv"
    return {(r["technology"], r["signal"], r["entry"], r["id"]) for r in csv.DictReader(open(path))} if path.exists() else set()


def mark_reviewed(date):
    """Record every row in the current spot_check.csv as reviewed by Josh on date."""
    merge_spot_checks()
    path, done = OUT / "reviewed.csv", reviewed()
    new = [norm(r, r.get("signal") or "platforms") for r in csv.DictReader(open(OUT / "spot_check.csv"))]
    new = [r for r in new if (r["technology"], r["signal"], r["entry"], r["id"]) not in done]
    write_header = not path.exists()
    with open(path, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["technology", "signal", "entry", "id", "reviewed"])
        if write_header:
            w.writeheader()
        w.writerows({"technology": r["technology"], "signal": r["signal"], "entry": r["entry"], "id": r["id"],
                     "reviewed": date} for r in new)
    return len(new)


def final(r):
    return r["josh_label"] or r["label"]


def wilson(yes, n, z=1.96):
    if not n:
        return None, None
    p = yes / n
    mid, half = p + z * z / (2 * n), z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return round((mid - half) / (1 + z * z / n), 2), round((mid + half) / (1 + z * z / n), 2)


def run():
    merge_spot_checks()
    done = reviewed()
    summary, spot = [], []
    for tech_id, tech in load_taxonomies().items():
        for signal in SIGNALS:
            path = OUT / f"{tech_id}_{signal}.csv"
            if not path.exists():
                continue
            rows = [norm(r, signal) for r in csv.DictReader(open(path))]
            for entry, sampled, keep in groups(tech_id, tech, signal, rows):
                live = [r for r in sampled if r["id"] in keep]
                yes, no = (sum(final(r) == lab for r in live) for lab in ("yes", "no"))
                lo, hi = wilson(yes, yes + no)
                prec = round(yes / (yes + no), 2) if yes + no else None
                summary.append({"technology": tech_id, "signal": signal, "entry": entry, "sampled": len(sampled),
                                "kept": len(live), "yes": yes, "no": no, "unclear": len(live) - yes - no,
                                "precision": prec, "ci_low": lo, "ci_high": hi,
                                "coverage": round((yes + no) / len(live), 2) if live else None,
                                "low": prec is not None and prec < LOW})
                spot += [{k: r.get(k, "") for k in SPOT_FIELDS} for r in live if r["josh_check"]
                         and (tech_id, signal, r["entry"], r["id"]) not in done]
    for name, data, fields in (("summary", summary, list(summary[0])), ("spot_check", spot, SPOT_FIELDS)):
        with open(OUT / f"{name}.csv", "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            w.writerows(data)
    return summary, spot


if __name__ == "__main__":
    import sys
    if sys.argv[1:2] == ["--reviewed"]:
        print(mark_reviewed(sys.argv[2]), "rows marked reviewed")
        sys.exit()
    summary, spot = run()
    for s in summary:
        print(f"{s['signal']:9} {s['entry'][:34]:34} kept {s['kept']:2}/{s['sampled']:2}  precision {s['precision']}  "
              f"[{s['ci_low']}, {s['ci_high']}]  coverage {s['coverage']}{'  LOW' if s['low'] else ''}")
    print(len(spot), "spot-check rows -> precision/spot_check.csv")

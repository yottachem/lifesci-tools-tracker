"""Precision per platform from the labeled samples, under the current queries.

A query that is narrowed (context terms added, OR branches dropped, type filter) returns a subset
of the works it returned when sampled, so the labeled rows it still matches are a uniform random
sample of its new results: precision is re-scored without relabeling. A query that is broadened
needs a fresh sample (python -m precision.sample_platforms <technology>).

Writes precision/summary.csv (per platform) and precision/spot_check.csv (rows for Josh: the
random 10% plus labels inferred from a repeated pattern, limited to rows the current query keeps).
Precision = yes / (yes + no), with a 95% Wilson interval; low = precision under LOW.

Run: uv run python -m precision.score
"""
import csv
import math

import requests

from ingest.common import ROOT, load_taxonomies, platforms, secret
from ingest.openalex import URL, platform_query, works_filter

OUT = ROOT / "precision"
LOW = 0.8


def kept(ids, query):
    """Work ids from ids that the query (with the type filter) still matches."""
    out = set()
    for i in range(0, len(ids), 50):
        params = {"filter": f"openalex:{'|'.join(ids[i:i + 50])}," + works_filter("default.search", query),
                  "per-page": 50, "select": "id"}
        if key := secret("OPENALEX_API_KEY", "openalex-api-key"):
            params["api_key"] = key
        r = requests.get(URL, params=params, timeout=60)
        if not r.ok:
            raise RuntimeError(f"OpenAlex {r.status_code} re-scoring {query}")
        out |= {w["id"].rsplit("/", 1)[-1] for w in r.json()["results"]}
    return out


def wilson(yes, n, z=1.96):
    if not n:
        return None, None
    p = yes / n
    mid, half = p + z * z / (2 * n), z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return round((mid - half) / (1 + z * z / n), 2), round((mid + half) / (1 + z * z / n), 2)


def run():
    summary, spot = [], []
    for tech_id, tech in load_taxonomies().items():
        path = OUT / f"{tech_id}_platforms.csv"
        if not path.exists():
            continue
        rows = list(csv.DictReader(open(path)))
        for fam_id, p in platforms(tech):
            sampled = [r for r in rows if r["platform"] == p["name"]]
            keep = kept([r["work_id"] for r in sampled], platform_query(p))
            live = [r for r in sampled if r["work_id"] in keep]
            yes, no = (sum(r["label"] == lab for r in live) for lab in ("yes", "no"))
            lo, hi = wilson(yes, yes + no)
            prec = round(yes / (yes + no), 2) if yes + no else None
            summary.append({"technology": tech_id, "platform": p["name"], "sampled": len(sampled), "kept": len(live),
                            "yes": yes, "no": no, "unclear": len(live) - yes - no, "precision": prec,
                            "ci_low": lo, "ci_high": hi, "coverage": round((yes + no) / len(live), 2) if live else None,
                            "low": prec is not None and prec < LOW})
            spot += [r for r in live if r["josh_check"]]
    for name, data in (("summary", summary), ("spot_check", spot)):
        with open(OUT / f"{name}.csv", "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(data[0]))
            w.writeheader()
            w.writerows(data)
    return summary, spot


if __name__ == "__main__":
    summary, spot = run()
    for s in summary:
        print(f"{s['platform']:24} kept {s['kept']:2}/{s['sampled']:2}  precision {s['precision']}  "
              f"[{s['ci_low']}, {s['ci_high']}]  coverage {s['coverage']}{'  LOW' if s['low'] else ''}")
    print(len(spot), "spot-check rows -> precision/spot_check.csv")

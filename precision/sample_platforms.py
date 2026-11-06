"""Precision sample for platform brand queries: ~50 random OpenAlex hits per platform, with evidence.

Each hit gets a snippet around the brand from its title/abstract, or else from Europe PMC open-access
full text (cached in data/cache/europepmc/). Labels are added afterwards in the CSV:
  label       yes = names the commercial platform, no = another meaning, unclear = no evidence found
  reason      one line
  josh_check  random = in the random 10% spot-check; judgment/pattern = added by hand (rows labelled no/unclear are reviewed too)
Precision = yes / (yes + no); evidence coverage = share of rows not unclear.

Run: uv run python -m precision.sample_platforms [technology ...]
"""
import csv
import random
import re
import sys
import time

import requests

from ingest.common import ROOT, load_taxonomies, platforms
from ingest.openalex import get, platform_query, works_filter

OUT = ROOT / "precision"
EPMC = ROOT / "data" / "cache" / "europepmc"
N, SEED, WINDOW = 50, 2026, 170
FIELDS = ["technology", "platform", "work_id", "year", "type", "title", "evidence", "snippet",
          "label", "reason", "josh_check", "josh_label"]


def sample(query):
    params = {"filter": works_filter("default.search", query), "sample": N, "seed": SEED, "per-page": N,
              "select": "id,title,publication_year,abstract_inverted_index,ids"}
    return get(params, f"sampling {query}").json()["results"]


def abstract(inv):
    if not inv:
        return ""
    words = sorted((i, w) for w, idx in inv.items() for i in idx)
    return " ".join(w for _, w in words)


def fulltext(pmcid):
    path = EPMC / f"{pmcid}.xml"
    if not path.exists():
        EPMC.mkdir(parents=True, exist_ok=True)
        r = requests.get(f"https://www.ebi.ac.uk/europepmc/webservices/rest/{pmcid}/fullTextXML", timeout=60)
        time.sleep(0.2)
        path.write_text(r.text if r.ok else "")
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", path.read_text()))


def brand_regex(p):
    """The brand is the first quoted term of each top-level OR branch; AND context terms are left out."""
    branches = re.split(r"\s+OR\s+(?![^()]*\))", p["query"])
    terms = [re.findall(r'"([^"]+)"', b)[0] for b in branches if '"' in b]
    # whole word: "Absolute Q" must not match "absolute quantification", nor "BioMark" "biomarker"
    return re.compile(r"(?<![A-Za-z0-9])(?:" + "|".join(re.escape(t) for t in terms) + r")(?![a-z])", re.IGNORECASE)


def works_by_id(ids):
    params = {"filter": "openalex:" + "|".join(ids), "per-page": 50,
              "select": "id,title,publication_year,abstract_inverted_index,ids,type"}
    return {w["id"].rsplit("/", 1)[-1]: w for w in get(params, "fetching works").json()["results"]}


def evidence(w, rx):
    title = w.get("title") or ""
    ev, snip = "title/abstract", snippet(f"{title}. {abstract(w.get('abstract_inverted_index'))}", rx)
    pmcid = (w.get("ids") or {}).get("pmcid", "").rsplit("/", 1)[-1]
    pmcid = f"PMC{pmcid}" if pmcid.isdigit() else pmcid  # OpenAlex gives the bare number
    if snip is None and pmcid:
        ev, snip = "fulltext", snippet(fulltext(pmcid), rx)
    return ("none", "") if snip is None else (ev, snip)


def resnip(tech_id):
    """Recompute evidence for an existing sample (same works, labels kept) and add work type."""
    path = OUT / f"{tech_id}_platforms.csv"
    rows = list(csv.DictReader(open(path)))
    plats = {p["name"]: p for _, p in platforms(load_taxonomies()[tech_id])}
    ids = [r["work_id"] for r in rows]
    works = {}
    for i in range(0, len(ids), 50):
        works.update(works_by_id(ids[i:i + 50]))
    for r in rows:
        w = works[r["work_id"]]
        r["evidence"], r["snippet"] = evidence(w, brand_regex(plats[r["platform"]]))
        r["type"] = w.get("type", "")
    with open(path, "w", newline="") as f:
        out = csv.DictWriter(f, fieldnames=FIELDS)
        out.writeheader()
        out.writerows(rows)
    print(path, len(rows), "rows re-snipped", flush=True)


def snippet(text, rx):
    m = rx.search(text)
    if not m:
        return None
    a, b = max(0, m.start() - WINDOW), min(len(text), m.end() + WINDOW)
    return ("..." if a else "") + text[a:b].strip() + ("..." if b < len(text) else "")


def run(techs=None):
    rng = random.Random(SEED)
    for tech_id, tech in load_taxonomies().items():
        if techs and tech_id not in techs:
            continue
        path = OUT / f"{tech_id}_platforms.csv"
        if path.exists():
            print(path, "exists; delete it to resample (labels would be lost)", flush=True)
            continue
        rows = []
        for _, p in platforms(tech):
            rx = brand_regex(p)
            works = sample(platform_query(p))
            print(f"{tech_id} / {p['name']}: {len(works)} sampled", flush=True)
            for w in works:
                title = w.get("title") or ""
                ev, snip = "title/abstract", snippet(f"{title}. {abstract(w.get('abstract_inverted_index'))}", rx)
                pmcid = (w.get("ids") or {}).get("pmcid", "").rsplit("/", 1)[-1]
                pmcid = f"PMC{pmcid}" if pmcid.isdigit() else pmcid  # OpenAlex gives the bare number
                if snip is None and pmcid:
                    ev, snip = "fulltext", snippet(fulltext(pmcid), rx)
                if snip is None:
                    ev, snip = "none", ""
                rows.append({"technology": tech_id, "platform": p["name"], "work_id": w["id"].rsplit("/", 1)[-1],
                             "year": w.get("publication_year"), "title": title[:200], "evidence": ev, "snippet": snip,
                             "label": "", "reason": "", "josh_check": "random" if rng.random() < 0.10 else "", "josh_label": ""})
        with open(path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS)
            w.writeheader()
            w.writerows(rows)
        print(path, len(rows), "rows", flush=True)


if __name__ == "__main__":
    if sys.argv[1:2] == ["--resnip"]:
        for t in sys.argv[2:] or load_taxonomies():
            resnip(t)
    else:
        run(sys.argv[1:] or None)

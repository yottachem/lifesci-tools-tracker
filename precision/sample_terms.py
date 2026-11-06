"""Precision samples for the non-brand signals: method terms, NIH terms, patent terms.

  methods  ~50 random OpenAlex works per method term (title + abstract search, scholarly types)
  nih      ~50 random project-years per technology from the latest RePORTER snapshot
  patents  ~50 per technology from the latest USPTO snapshot (half grants, half applications)

Each row carries a snippet around the matched term from title/abstract (OpenAlex abstract,
RePORTER abstract text, PatentsView abstract). Labels follow the platform samples:
  label  yes = the work is about or uses the technology as defined in the taxonomy,
         no = another meaning (wrong field, wrong scale), unclear = not enough text
Writes precision/{technology}_{signal}.csv. Precision = yes / (yes + no).

Run: uv run python -m precision.sample_terms [signal ...]
"""
import csv
import json
import random
import re
import sys
import time

import pandas as pd
import requests

from ingest.common import ROOT, contact, load_taxonomies, method_term, secret
from ingest.openalex import URL, method_query, works_filter
from ingest.uspto import CACHE, phrase_regex
from precision.sample_platforms import N, SEED, abstract, snippet

OUT = ROOT / "precision"
RAW = ROOT / "data" / "raw"
REPORTER = "https://api.reporter.nih.gov/v2/projects/search"
FIELDS = ["technology", "signal", "entry", "id", "year", "type", "title", "evidence", "snippet",
          "label", "reason", "josh_check", "josh_label"]


def latest(source):
    return json.load(open(sorted((RAW / source).glob("*.json"))[-1]))


def payload(snap):
    return snap.get("data", snap)


def term_regex(term):
    """Quoted parts of an OpenAlex term, or the bare words when nothing is quoted (CyCIF)."""
    parts = re.findall(r'"([^"]+)"', term) + [w for w in re.sub(r'"[^"]*"|\bOR\b|\bAND\b', " ", term).split()]
    # OpenAlex strips a trailing "s" even inside quotes ("spatial transcriptomic" matches); phrase_regex re-allows it
    return re.compile("|".join(phrase_regex(re.sub(r"s$", "", p)).pattern for p in parts), re.IGNORECASE)


def methods(tech_id, tech, rng):
    rows = []
    for m in tech["method_terms"]:
        term = method_term(m)
        params = {"filter": works_filter("title_and_abstract.search", method_query(m)), "sample": N, "seed": SEED,
                  "per-page": N, "select": "id,title,publication_year,abstract_inverted_index,type"}
        if email := contact():
            params["mailto"] = email
        if key := secret("OPENALEX_API_KEY", "openalex-api-key"):
            params["api_key"] = key
        r = requests.get(URL, params=params, timeout=60)
        if not r.ok:
            raise RuntimeError(f"OpenAlex {r.status_code} sampling {term}")
        rx = term_regex(term)
        for w in r.json()["results"]:
            title = w.get("title") or ""
            snip = snippet(f"{title}. {abstract(w.get('abstract_inverted_index'))}", rx)
            rows.append(row(tech_id, "methods", term, w["id"].rsplit("/", 1)[-1], w.get("publication_year"),
                            w.get("type", ""), title, snip, rng))
    return rows


def nih(tech_id, tech, rng):
    projects = payload(latest("reporter"))[tech_id]
    picked = random.Random(f"{SEED}-{tech_id}").sample(projects, min(N, len(projects)))
    abstracts = {}
    ids = [p["appl_id"] for p in picked]
    body = {"criteria": {"appl_ids": ids}, "include_fields": ["ApplId", "AbstractText"], "limit": len(ids)}
    r = requests.post(REPORTER, json=body, timeout=90)
    r.raise_for_status()
    time.sleep(1)
    abstracts = {p["appl_id"]: p.get("abstract_text") or "" for p in r.json()["results"]}
    rows = []
    for p in picked:
        rx = re.compile("|".join(phrase_regex(t).pattern for t in p["terms"]), re.IGNORECASE)
        snip = snippet(f"{p['title']}. {abstracts.get(p['appl_id'], '')}", rx)
        rows.append(row(tech_id, "nih", "; ".join(sorted(set(p["terms"]))), p["appl_id"], p["fiscal_year"],
                        p.get("activity_code") or "", p["title"] or "", snip, rng))
    return rows


def patents(tech_id, tech, rng):
    snap = payload(latest("uspto"))
    rows = []
    for kind in ("granted", "pregrant"):
        hits = [h for h in snap[kind] if tech_id in h["technologies"]]
        picked = random.Random(f"{SEED}-{tech_id}-{kind}").sample(hits, min(N // 2, len(hits)))
        corpus = pd.read_parquet(CACHE / f"corpus_{kind}.parquet", columns=["id", "abstract"]).set_index("id")["abstract"]
        for h in picked:
            terms = h["technologies"][tech_id]
            rx = re.compile("|".join(phrase_regex(t).pattern for t in terms), re.IGNORECASE)
            snip = snippet(f"{h['title']}. {corpus.get(h['id'], '')}", rx)
            rows.append(row(tech_id, "patents", "; ".join(terms), h["id"], h["date"][:4], kind, h["title"], snip, rng))
    return rows


def row(tech_id, signal, entry, id_, year, type_, title, snip, rng):
    return {"technology": tech_id, "signal": signal, "entry": entry, "id": id_, "year": year, "type": type_,
            "title": (title or "")[:200], "evidence": "none" if snip is None else "title/abstract",
            "snippet": snip or "", "label": "", "reason": "", "josh_check": "random" if rng.random() < 0.10 else "", "josh_label": ""}


SIGNALS = {"methods": methods, "nih": nih, "patents": patents}


def run(signals):
    for signal in signals:
        rng = random.Random(f"{SEED}-{signal}")
        for tech_id, tech in load_taxonomies().items():
            path = OUT / f"{tech_id}_{signal}.csv"
            if path.exists():
                print(path, "exists; delete it to resample (labels would be lost)")
                continue
            rows = SIGNALS[signal](tech_id, tech, rng)
            with open(path, "w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=FIELDS)
                w.writeheader()
                w.writerows(rows)
            print(path, len(rows), "rows", sum(r["evidence"] == "none" for r in rows), "without snippet", flush=True)


if __name__ == "__main__":
    run(sys.argv[1:] or list(SIGNALS))

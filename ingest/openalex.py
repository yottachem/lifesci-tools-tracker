"""OpenAlex: works per publication year for each method term and platform brand.

Method terms match title + abstract (the paper is about the method). Platform brands match
title + abstract + open-access full text (methods sections name the instrument).
OpenAlex stems bare words and matches quoted terms exactly, so brands must be quoted
(tests/test_taxonomy.py enforces this).

Only scholarly work types count (TYPES): datasets, software, peer-review records and other
repository deposits name platforms too (e.g. HuBMAP "CODEX data from ..." records) and would
inflate counts.

Key: OPENALEX_API_KEY or Keychain item openalex-api-key. The key travels in the URL, so errors
are re-raised without it.

Run: uv run python -m ingest.openalex
"""
import time

import requests

from ingest.common import contact, load_taxonomies, method_term, platforms, secret, write_snapshot

URL = "https://api.openalex.org/works"
TYPES = ("article", "review", "preprint", "conference-abstract", "conference-paper", "book-chapter",
         "book", "dissertation", "editorial", "letter")


def works_filter(field, query):
    return f"type:{'|'.join(TYPES)},{field}:{query}"


def platform_query(p):
    q = p["query"]
    if ctx := p.get("context"):
        q = f"({q}) AND (" + " OR ".join(f'"{c}"' for c in ctx) + ")"
    return q


def method_query(m):
    q = method_term(m)
    if isinstance(m, dict) and (ctx := m.get("context")):
        q = f"({q}) AND (" + " OR ".join(f'"{c}"' for c in ctx) + ")"
    return q


def get(params, what):
    """GET /works with the contact and key added; backs off on the burst limit (429).
    Errors name `what`, never the URL, which carries the key."""
    params = dict(params)
    if email := contact():
        params["mailto"] = email
    if key := secret("OPENALEX_API_KEY", "openalex-api-key"):
        params["api_key"] = key
    for attempt in range(6):
        try:
            r = requests.get(URL, params=params, timeout=60)
        except requests.RequestException as e:
            raise RuntimeError(f"OpenAlex request failed ({type(e).__name__}) for {what}") from None
        if r.status_code != 429:
            break
        if int(r.headers.get("Retry-After", 0)) > 60:  # daily $ budget spent; resets at midnight UTC
            raise RuntimeError(f"OpenAlex daily budget spent ({what}); resets in {int(r.headers['Retry-After']) // 60} min")
        time.sleep(2 ** attempt)  # burst limit
    if not r.ok:
        raise RuntimeError(f"OpenAlex {r.status_code} for {what}")
    return r


def by_year(field, query):
    r = get({"filter": works_filter(field, query), "group_by": "publication_year"}, f"{field}:{query}")
    d = r.json()
    counts = {int(g["key"]): g["count"] for g in d["group_by"] if g["key"].isdigit()}
    return counts, d["meta"].get("cost_usd", 0)


def run():
    out, cost = {}, 0.0
    for tech_id, tech in load_taxonomies().items():
        methods, plats = {}, {}
        for m in tech["method_terms"]:
            methods[method_term(m)], usd = by_year("title_and_abstract.search", method_query(m))
            cost += usd
        for fam_id, p in platforms(tech):
            q = platform_query(p)
            c, usd = by_year("default.search", q)
            cost += usd
            plats[p["name"]] = {"family": fam_id, "query": q, "launched": p.get("launched"), "counts": c}
        out[tech_id] = {"methods": methods, "platforms": plats}
    path = write_snapshot("openalex", out)
    print(f"OpenAlex cost ${cost:.3f}")
    return path


if __name__ == "__main__":
    print(run())

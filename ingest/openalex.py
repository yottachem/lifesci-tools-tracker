"""OpenAlex: works per publication year for each method term and platform brand.

Method terms match title + abstract (the paper is about the method). Platform brands match
title + abstract + open-access full text (methods sections name the instrument).
OpenAlex stems bare words and matches quoted terms exactly, so brands must be quoted
(tests/test_taxonomy.py enforces this).

Key: OPENALEX_API_KEY or Keychain item openalex-api-key. The key travels in the URL, so errors
are re-raised without it.

Run: uv run python -m ingest.openalex
"""
import requests

from ingest.common import contact, load_taxonomies, platforms, secret, write_snapshot

URL = "https://api.openalex.org/works"


def platform_query(p):
    q = p["query"]
    if ctx := p.get("context"):
        q = f"({q}) AND (" + " OR ".join(f'"{c}"' for c in ctx) + ")"
    return q


def by_year(field, query):
    params = {"filter": f"{field}:{query}", "group_by": "publication_year"}
    if email := contact():
        params["mailto"] = email
    if key := secret("OPENALEX_API_KEY", "openalex-api-key"):
        params["api_key"] = key
    try:
        r = requests.get(URL, params=params, timeout=60)
    except requests.RequestException as e:
        raise RuntimeError(f"OpenAlex request failed ({type(e).__name__}) for {field}:{query}") from None
    if not r.ok:
        raise RuntimeError(f"OpenAlex {r.status_code} for {field}:{query}")
    d = r.json()
    counts = {int(g["key"]): g["count"] for g in d["group_by"] if g["key"].isdigit()}
    return counts, d["meta"].get("cost_usd", 0)


def run():
    out, cost = {}, 0.0
    for tech_id, tech in load_taxonomies().items():
        methods, plats = {}, {}
        for term in tech["method_terms"]:
            methods[term], usd = by_year("title_and_abstract.search", term)
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

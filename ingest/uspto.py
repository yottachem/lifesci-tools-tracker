"""USPTO PatentsView bulk data (Open Data Portal): patents and published applications per technology.

PatentsView's search API moved into ODP on 2026-03-20; the data now comes as bulk TSV products:
  PVGPATDIS   granted patents (g_*), 1976+
  PVPGPUBDIS  pre-grant publications (pg_*), 2001+; ~18 months after filing, so fresher than grants
Files are cached in data/cache/uspto/ (gitignored) and re-downloaded only when ODP's
fileLastModifiedDateTime changes. Matching is ours, not a search engine's: exact phrases from
each technology's method_terms and nih_terms, case-insensitive, hyphen/space tolerant, in title or
abstract, within a shared life-science CPC scope (SCOPE_*). The scoped corpus is cached as
parquet so term changes re-run in seconds. CPC classes and assignees are attached to every match.

Key: USPTO_ODP_API_KEY or Keychain item uspto-odp-api-key (X-API-Key header).

Run: uv run python -m ingest.uspto            (download if changed, then match)
     uv run python -m ingest.uspto --download (download only)
"""
import csv
import io
import json
import re
import sys
import zipfile

import pandas as pd
import requests

from ingest.common import ROOT, load_taxonomies, secret, write_snapshot

API = "https://api.uspto.gov/api/v1/datasets/products/{pid}"
CACHE = ROOT / "data" / "cache" / "uspto"
SINCE = "2010-01-01"
csv.field_size_limit(sys.maxsize)

# Life-science CPC scope: nucleic acids/enzymes, proteins, assays and analysis, bioinformatics,
# lab devices, and image analysis of cells/microscopic objects. Keeps radar, MRI and optics out.
SCOPE_SUBCLASSES = {"C12Q", "C12N", "C12M", "C07K", "C07H", "G01N", "G16B", "B01L"}
SCOPE_GROUPS = ("G06V20/69", "G06T2207/30024")

DATASETS = {
    "granted": {
        "product": "PVGPATDIS",
        "files": {"doc": "g_patent.tsv.zip", "abstract": "g_patent_abstract.tsv.zip", "app": "g_application.tsv.zip",
                  "cpc": "g_cpc_current.tsv.zip", "assignee": "g_assignee_disambiguated.tsv.zip"},
        "id": "patent_id",
    },
    "pregrant": {
        "product": "PVPGPUBDIS",
        "files": {"doc": "pg_published_application.tsv.zip", "abstract": "pg_published_application_abstract.tsv.zip",
                  "cpc": "pg_cpc_current.tsv.zip", "assignee": "pg_assignee_disambiguated.tsv.zip"},
        "id": "pgpub_id",
    },
}


def headers():
    key = secret("USPTO_ODP_API_KEY", "uspto-odp-api-key")
    if not key:
        raise RuntimeError("USPTO key missing: set USPTO_ODP_API_KEY or Keychain item uspto-odp-api-key")
    return {"X-API-Key": key}


def file_index(product):
    r = requests.get(API.format(pid=product), headers=headers(), timeout=60)
    if not r.ok:
        raise RuntimeError(f"USPTO ODP {r.status_code} for {product}")
    files = r.json()["bulkDataProductBag"][0]["productFileBag"]["fileDataBag"]
    return {f["fileName"]: f for f in files}


def download(name, meta):
    """Fetch one file unless the cached copy matches ODP's last-modified stamp."""
    CACHE.mkdir(parents=True, exist_ok=True)
    path, stamp = CACHE / name, CACHE / f"{name}.modified"
    if path.exists() and stamp.exists() and stamp.read_text() == meta["fileLastModifiedDateTime"]:
        return path, False
    print(f"downloading {name} ({meta['fileSize'] / 1e6:,.0f} MB)", flush=True)
    try:
        with requests.get(meta["fileDownloadURI"], headers=headers(), stream=True, timeout=600) as r:
            if not r.ok:
                raise RuntimeError(f"USPTO download {r.status_code} for {name}")
            tmp = path.with_suffix(".part")
            with open(tmp, "wb") as f:
                for chunk in r.iter_content(1 << 20):
                    f.write(chunk)
    except requests.RequestException as e:
        raise RuntimeError(f"USPTO download failed ({type(e).__name__}) for {name}") from None
    tmp.rename(path)
    stamp.write_text(meta["fileLastModifiedDateTime"])
    return path, True


def rows(path):
    """Stream rows of the single TSV inside a PatentsView zip."""
    with zipfile.ZipFile(path) as z:
        inner = next(n for n in z.namelist() if n.endswith(".tsv"))
        with z.open(inner) as fh:
            yield from csv.DictReader(io.TextIOWrapper(fh, encoding="utf-8", errors="replace"), delimiter="\t")


def phrases(tech):
    """(phrase, context) pairs from method_terms (quoted parts), nih_terms, and patent_terms.

    A patent_terms entry is a phrase, or {phrase, with: [...]} when the phrase only counts next to
    one of the context words (generic wording such as "spatial analysis").
    """
    out = {}
    for t in tech.get("method_terms", []):
        out.update((q, ()) for q in re.findall(r'"([^"]+)"', t))
    out.update((q, ()) for q in tech.get("nih_terms", []))
    for e in tech.get("patent_terms", []):
        out.update([(e["phrase"], tuple(e["with"]))] if isinstance(e, dict) else [(e, ())])
    return sorted(out.items(), key=lambda kv: kv[0].lower())


def phrase_regex(p):
    words = [re.escape(w) for w in re.split(r"[\s-]+", p.strip())]
    return re.compile(r"(?<![\w-])" + r"[\s-]+".join(words) + r"(?:s|es)?(?![\w-])", re.IGNORECASE)


def pick(row, *names):
    for n in names:
        if row.get(n):
            return row[n]
    return ""


def in_scope(group):
    return group.split("/")[0][:4] in SCOPE_SUBCLASSES or group.startswith(SCOPE_GROUPS)


def build_corpus(kind, cfg, paths):
    """Life-science subset (CPC scope) with title, abstract, CPC and assignees; cached as parquet.

    Rebuilt only when an input zip is newer than the cached corpus. Every technology matches against it.
    """
    out = CACHE / f"corpus_{kind}.parquet"
    if out.exists() and all(out.stat().st_mtime > paths[r].stat().st_mtime for r in paths):
        return pd.read_parquet(out)
    idcol = cfg["id"]
    docs = {}
    for r in rows(paths["doc"]):
        d = pick(r, "patent_date", "published_date")
        if d >= SINCE:
            docs[r[idcol]] = {"date": d, "title": pick(r, "patent_title", "application_title"),
                              "filing_date": r.get("filing_date", "")}
    cpc = {}
    for r in rows(paths["cpc"]):
        if r[idcol] in docs:
            cpc.setdefault(r[idcol], []).append(r["cpc_group"])
    keep = {i for i, groups in cpc.items() if any(in_scope(g) for g in groups)}
    print(f"{kind}: {len(docs):,} documents since {SINCE}, {len(keep):,} in life-science CPC scope", flush=True)
    abstracts = {r[idcol]: pick(r, "patent_abstract", "application_abstract") for r in rows(paths["abstract"]) if r[idcol] in keep}
    filing = {r[idcol]: r.get("filing_date", "") for r in rows(paths["app"]) if r[idcol] in keep} if "app" in paths else {}
    assignees = {}
    for r in rows(paths["assignee"]):
        if r[idcol] in keep:
            assignees.setdefault(r[idcol], []).append(pick(r, "disambig_assignee_organization") or " ".join(
                x for x in (r.get("disambig_assignee_individual_name_first"), r.get("disambig_assignee_individual_name_last")) if x))
    df = pd.DataFrame([{"id": i, **docs[i], "filing_date": filing.get(i) or docs[i]["filing_date"],
                        "abstract": abstracts.get(i, ""), "cpc": cpc[i], "assignees": assignees.get(i, [])}
                       for i in sorted(keep)])
    df.to_parquet(out, index=False)
    return df


def match(corpus, techs):
    """Per technology, the phrases each document's title + abstract contains."""
    text = (corpus["title"] + " " + corpus["abstract"]).str.lower()
    found = {}
    for t, tech in techs.items():
        for p, context in phrases(tech):
            # cheap substring prefilter on the first word, then the exact phrase regex on the survivors
            first = re.split(r"[\s-]+", p.strip())[0].lower()
            cand = text[text.str.contains(first, regex=False)]
            hit = cand.str.contains(phrase_regex(p), regex=True)
            if context:
                hit &= cand.str.contains("|".join(phrase_regex(w).pattern for w in context), regex=True)
            for i in cand.index[hit]:
                found.setdefault(i, {}).setdefault(t, []).append(p)
    hits = []
    for i, by_tech in found.items():
        r = corpus.loc[i]
        hits.append({"id": r["id"], "date": r["date"], "filing_date": r["filing_date"], "title": r["title"],
                     "technologies": by_tech, "cpc": list(r["cpc"]), "assignees": list(r["assignees"])})
    return sorted(hits, key=lambda h: (h["date"], h["id"]))


def run(download_only=False):
    techs = load_taxonomies()
    out, meta = {}, {}
    for kind, cfg in DATASETS.items():
        index = file_index(cfg["product"])
        paths = {}
        for role, name in cfg["files"].items():
            paths[role], _ = download(name, index[name])
            meta[name] = {k: index[name].get(k) for k in ("fileLastModifiedDateTime", "fileDataFromDate", "fileDataToDate")}
        if not download_only:
            out[kind] = match(build_corpus(kind, cfg, paths), techs)
            print(f"{kind}: {len(out[kind]):,} matches", flush=True)
    if download_only:
        print(json.dumps(meta, indent=1))
        return None
    return write_snapshot("uspto", {"files": meta, "since": SINCE, **out})


if __name__ == "__main__":
    print(run(download_only="--download" in sys.argv))

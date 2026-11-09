"""Signal panel per technology from the latest raw snapshots, written to site/data/panel.json.

Per technology: publications (union of method terms, per 100K works), platform mentions and share,
NIH project-years and dollars, patent grants and applications with top assignees, the vendor roster,
pure-play financials and market value, measured precision, and (where curated) platform economics
and commercial traction (transform.economics). Home-page metrics use the latest full
year (FULL_YEAR) for every signal so the technologies are compared on the same period.

Run: uv run python -m transform.panel
"""
import csv
import json
from collections import Counter
from datetime import date

from ingest.common import RAW, ROOT, YEARS, fiscal_year, load_taxonomies, load_vendors, platforms
from transform import economics

OUT = ROOT / "site" / "data" / "panel.json"
FULL_YEAR = date.today().year - 1      # latest complete publication, patent and NIH year
GROWTH_FROM = FULL_YEAR - 5            # 5-year growth window
ASSIGNEE_YEARS = range(FULL_YEAR - 4, FULL_YEAR + 1)


def latest(source):
    path = max((RAW / source).glob("*.json"))
    return path.stem, json.loads(path.read_text())


def series(counts):
    """{year: n} with int years inside the counting window, missing years as 0."""
    c = {int(k): v for k, v in counts.items()}
    return {y: c.get(y, 0) for y in YEARS}


def cagr(s, a, b):
    if not s.get(a) or not s.get(b):
        return None
    return (s[b] / s[a]) ** (1 / (b - a)) - 1


def precision_rows():
    with open(ROOT / "precision" / "summary.csv") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        r["precision"] = float(r["precision"]) if r["precision"] else None
        r["ci_low"] = float(r["ci_low"]) if r["ci_low"] else None
        r["low"] = r["low"] == "True"
    return rows


def patent_use():
    with open(ROOT / "precision" / "patent_use.csv") as f:
        return {r["technology"]: float(r["use_share"]) if r["use_share"] else None for r in csv.DictReader(f)}


def publications(oa, total):
    if "union" in oa:
        counts, basis = series(oa["union"]), "union"
    else:  # older snapshots: the largest single term is a lower bound on the union
        per_term = [series(c) for c in oa["methods"].values()]
        counts, basis = {y: max(s[y] for s in per_term) for y in YEARS}, "largest_term"
    per100k = {y: counts[y] / total[y] * 1e5 for y in YEARS if total.get(y)} if total else {}
    return {"counts": counts, "per100k": per100k, "basis": basis}


def platform_panel(tech, oa, prec):
    out = []
    for fam_id, p in platforms(tech):
        counts = series(oa["platforms"].get(p["name"], {}).get("counts", {}))
        pr = prec.get(p["name"], {})
        out.append({"name": p["name"], "family": fam_id, "family_label": tech["families"][fam_id]["label"],
                    "vendor": p["vendor"], "launched": p.get("launched"), "parent": p.get("parent"),
                    "pending": p.get("pending"), "counts": counts,
                    "precision": pr.get("precision"), "low": pr.get("low", False)})
    top = [p for p in out if not p["parent"]]  # sub-brands are inside their parent's count
    for y in YEARS:
        tot = sum(p["counts"][y] for p in top)
        for p in out:
            p.setdefault("share", {})[y] = p["counts"][y] / tot if tot else None
    return out


def vendor_share(plats, year):
    s = Counter()
    for p in plats:
        if not p["parent"] and p["share"].get(year):
            s[p["vendor"]] += p["share"][year]
    return dict(s.most_common())


def nih_panel(rows):
    n, usd, ic = Counter(), Counter(), Counter()
    for r in rows:
        n[r["fiscal_year"]] += 1
        usd[r["fiscal_year"]] += r["award_amount"] or 0
        if r["fiscal_year"] in ASSIGNEE_YEARS and r.get("ic"):
            ic[r["ic"]] += r["award_amount"] or 0
    return {"project_years": {y: n.get(y, 0) for y in YEARS}, "dollars": {y: usd.get(y, 0) for y in YEARS},
            "top_ics": ic.most_common(5)}


def patent_panel(tech_id, uspto):
    out = {}
    for kind in ("granted", "pregrant"):
        by_year, assignees = Counter(), Counter()
        for r in uspto[kind]:
            if tech_id not in r["technologies"]:
                continue
            y = int(r["date"][:4])
            by_year[y] += 1
            if y in ASSIGNEE_YEARS:
                for a in r["assignees"]:
                    assignees[a] += 1
        out[kind] = {"counts": {y: by_year.get(y, 0) for y in YEARS}, "top_assignees": assignees.most_common(8)}
    return out


def financials(name, edgar, mix, mv):
    """Pure-play revenue by fiscal year, revenue mix, reported technology revenue, latest market value."""
    e = edgar.get(name, {})
    rev = {fiscal_year(k): v for k, v in (e.get("revenue") or {}).items()}
    m = {}
    for end, x in (mix.get(name) or {}).items():
        if x["reconciled"]:
            m[fiscal_year(end)] = {"mix": x["mix"], "technology": {t: sum(g.values()) for t, g in x["technology"].items()}}
    v = (mv.get(name) or {}).get("market_value_usd") or {}
    month = max(v) if v else None
    return {"revenue": dict(sorted(rev.items())), "mix": dict(sorted(m.items())),
            "market_value": {"month": month, "usd": v.get(month)} if month else None,
            "market_value_series": dict(sorted(v.items()))}


def pending_deals(techs, vendors):
    """Announced deals that would move a platform or vendor, until their close is confirmed."""
    out = [{"what": name, "pending": v["pending"]} for name, v in vendors.items() if v.get("pending")]
    for tech in techs.values():
        for _, p in platforms(tech):
            if p.get("pending") and not any(d["what"] == p["name"] for d in out):
                out.append({"what": p["name"], "pending": p["pending"]})
    return out


def run():
    techs, vendors = load_taxonomies(), load_vendors()
    snaps = {s: latest(s) for s in ("openalex", "reporter", "uspto", "edgar", "edgar_mix", "market_value")}
    oa_all, reporter, uspto = snaps["openalex"][1], snaps["reporter"][1], snaps["uspto"][1]
    edgar, mix, mv = snaps["edgar"][1], snaps["edgar_mix"][1], snaps["market_value"][1]
    awards_snap = latest("usaspending") if (RAW / "usaspending").exists() else (None, {})
    total = {int(k): v for k, v in oa_all.get("_total", {}).items()}
    prec_all = precision_rows()
    use = patent_use()

    panel = {}
    for tech_id, tech in techs.items():
        prec = {r["entry"]: r for r in prec_all if r["technology"] == tech_id and r["signal"] == "platforms"}
        plats = platform_panel(tech, oa_all[tech_id], prec)
        pubs = publications(oa_all[tech_id], total)
        nih = nih_panel(reporter.get(tech_id, []))
        pats = patent_panel(tech_id, uspto)
        names = {p["name"] for p in plats}
        roster = []
        for vname, v in vendors.items():
            mine = [p for p in v.get("platforms", []) if p in names]
            if not mine and not any(p["vendor"] == vname for p in plats):
                continue
            row = {"name": vname, "status": v["status"], "role": v.get("role"), "ticker": v.get("ticker"),
                   "platforms": mine or sorted(p["name"] for p in plats if p["vendor"] == vname)}
            if v.get("cik"):
                row["financials"] = financials(vname, edgar, mix, mv)
            roster.append(row)
        top = [p for p in plats if not p["parent"] and p["share"].get(FULL_YEAR) is not None]
        lead = max(top, key=lambda p: p["share"][FULL_YEAR], default=None)
        rate = pubs["per100k"] or pubs["counts"]
        panel[tech_id] = {
            "id": tech_id, "label": tech["label"], "short_label": tech.get("short_label"), "stage": tech.get("stage"),
            "definition": " ".join(tech["definition"].split()), "note": tech.get("note"),
            "publications": pubs, "platforms": plats, "vendor_share": vendor_share(plats, FULL_YEAR),
            "nih": nih, "patents": pats, "patent_use_share": use.get(tech_id), "vendors": roster,
            "precision": [r for r in prec_all if r["technology"] == tech_id],
            "economics": economics.technology(tech_id, tech, mix, awards_snap[1]),
            "metrics": {
                "publications": pubs["counts"][FULL_YEAR],
                "per100k": pubs["per100k"].get(FULL_YEAR),
                "growth_5y": cagr(rate, GROWTH_FROM, FULL_YEAR),
                "growth_basis": "per100k" if pubs["per100k"] else "counts",
                "nih_dollars": nih["dollars"][FULL_YEAR],
                "nih_project_years": nih["project_years"][FULL_YEAR],
                "patent_applications": pats["pregrant"]["counts"][FULL_YEAR],
                "patent_grants": pats["granted"]["counts"][FULL_YEAR],
                "top_brand": lead["name"] if lead else None,
                "top_brand_share": lead["share"][FULL_YEAR] if lead else None,
            },
        }
    meta = {"built": date.today().isoformat(), "full_year": FULL_YEAR, "growth_from": GROWTH_FROM,
            "years": [YEARS.start, YEARS.stop - 1], "snapshots": {s: d for s, (d, _) in snaps.items()},
            "patents_through": max(r["date"] for r in uspto["pregrant"]), "has_total": bool(total),
            "uspto_files": uspto.get("files", {}), "pending_deals": pending_deals(techs, vendors),
            "usaspending": {"snap": awards_snap[0], "awards": sum(len(v) for v in awards_snap[1].values())}}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"meta": meta, "technologies": panel}, indent=1, sort_keys=False))
    for t in panel.values():
        m = t["metrics"]
        g = f"{m['growth_5y']:+.0%}" if m["growth_5y"] is not None else "n/a"
        print(f"{t['id']:24} pubs {m['publications']:>6,} growth {g:>5} NIH ${m['nih_dollars'] / 1e6:>6,.0f}M "
              f"apps {m['patent_applications']:>4} top {m['top_brand']} {m['top_brand_share'] or 0:.0%}")
    return OUT


if __name__ == "__main__":
    print(run())

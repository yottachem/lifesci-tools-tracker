"""Build the site into site/dist from site/data/panel.json (transform.panel).
Pages: home (maturity scatter + signal table), one page per technology, methods, status.

Usage: uv run python site/build.py [--preview]
--preview marks every page as an internal preview and shows sections still waiting on Josh
(Product takes, checked statements) as placeholders instead of leaving them out.
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import charts  # noqa: E402  (site/charts.py)

DIST = HERE / "dist"
REPO = "https://github.com/yottachem/lifesci-tools-tracker"
SITE_URL = "https://yottachem.github.io/lifesci-tools-tracker/"
# Cloudflare Web Analytics site token for yottachem.github.io (public; it ships in the page). Empty = no beacon.
ANALYTICS_TOKEN = ""
SHARE_MIN = 50  # platform mentions a year before shares are charted
STAGE_ORDER = {"emerging": 0, "growth": 1, "mature": 2}
SIGNAL_LABELS = {"platforms": "Platform brands", "methods": "Method terms", "nih": "NIH terms", "patents": "Patent terms"}
# Hand-curated inputs listed on the status page: everything else is fetched each run
CURATED = [("taxonomy/", "Technologies, method and grant terms, platforms, vendors per platform, launch years"),
           ("reference/vendors.yaml", "Vendor roster: ownership history, listings, pending deals"),
           ("reference/launch_sources.yaml", "Source for every platform launch year"),
           ("reference/revenue_members.yaml", "Which XBRL product lines map to instruments, consumables and services"),
           ("precision/summary.csv", "Measured precision per brand and term, from labeled samples"),
           ("precision/patent_use.csv", "Share of matched patents that only use the technology")]
MIX_LABELS = {"instrument": "Instruments", "consumable": "Consumables", "product": "Products", "service": "Services",
              "other": "Other", "related_party": "Related party"}


def years(meta):
    return list(range(meta["years"][0], meta["years"][1] + 1))


def tech_context(t, meta):
    ys, full, partial = years(meta), meta["full_year"], meta["years"][1]
    pubs = t["publications"]
    rate = bool(pubs["per100k"])
    pub_values = {int(k): v for k, v in (pubs["per100k"] if rate else pubs["counts"]).items()}
    pub_chart = charts.line_chart([{"name": "Papers" if not rate else "Per 100K works", "values": pub_values, "emph": 1, "label": False}],
                                  ys, partial=partial, kind="rate" if rate else "n", label_w=12, width=1000, height=280,
                                  desc=f"{t['label']} papers by year")
    tops = sorted((p for p in t["platforms"] if not p["parent"]), key=lambda p: -(p["share"].get(str(full)) or 0))

    def share(p):
        return {int(k): v for k, v in p["share"].items()}

    emph = {p["name"]: i + 1 for i, p in enumerate(tops[:4])}
    # shares swing wildly on a handful of mentions, so the chart starts once the platforms total SHARE_MIN a year
    totals = {y: sum(p["counts"].get(str(y), 0) for p in tops) for y in ys}
    share_from = next((y for y in ys if totals[y] >= SHARE_MIN), ys[0])
    share_years = [y for y in ys if y >= share_from]
    share_chart = charts.line_chart(
        [{"name": p["name"], "values": {y: v for y, v in share(p).items() if y >= share_from}, "emph": emph.get(p["name"], 0),
          "label": p["name"] in emph} for p in tops],
        share_years, partial=partial, kind="pct", ymax=None, label_w=190, width=1000, height=300,
        desc=f"Share of platform mentions, {t['label']}")
    nih = {int(k): v for k, v in t["nih"]["dollars"].items()}
    nih_chart = charts.bar_chart(nih, ys, partial=None, kind="usd", desc=f"NIH dollars by fiscal year, {t['label']}")
    pat = t["patents"]
    pat_chart = charts.line_chart(
        [{"name": "Applications", "values": {int(k): v for k, v in pat["pregrant"]["counts"].items() if int(k) <= full}, "emph": 1},
         {"name": "Grants", "values": {int(k): v for k, v in pat["granted"]["counts"].items() if int(k) <= full}, "emph": 2}],
        [y for y in ys if y <= full], kind="n", label_w=120, desc=f"Patents by year, {t['label']}")
    plat_rows = []
    for p in sorted(t["platforms"], key=lambda p: (p["family_label"], -(p["counts"].get(str(full)) or 0))):
        plat_rows.append({**p, "n_full": p["counts"].get(str(full), 0), "share_full": p["share"].get(str(full))})
    pure = [v for v in t["vendors"] if v.get("role") == "pure_play" and v.get("financials")]
    fin_rows = []
    for v in pure:
        f = v["financials"]
        fys = sorted(f["revenue"], key=int)[-5:]
        rows = []
        for fy in fys:
            m = f["mix"].get(fy) or {}
            rows.append({"fy": fy, "revenue": f["revenue"][fy], "mix": m.get("mix", {}), "technology": m.get("technology", {})})
        fin_rows.append({"name": v["name"], "ticker": v["ticker"], "rows": rows, "market_value": f["market_value"],
                         "mix_keys": [k for k in MIX_LABELS if any(k in r["mix"] for r in rows)],
                         "tech_keys": sorted({k for r in rows for k in r["technology"]})})
    return {"t": t, "share_from": share_from, "pub_chart": pub_chart, "share_chart": share_chart, "nih_chart": nih_chart, "pat_chart": pat_chart,
            "plat_rows": plat_rows, "fin": fin_rows, "rate": rate, "mix_labels": MIX_LABELS}


def home_context(panel, meta):
    ys, full, partial = years(meta), meta["full_year"], meta["years"][1]
    techs = sorted(panel.values(), key=lambda t: (STAGE_ORDER.get(t["stage"], 9), -(t["metrics"]["publications"] or 0)))
    pts = []
    for t in techs:
        m = t["metrics"]
        tip = (f"{t['label']}: {m['publications']:,} papers in {full}, {charts.fmt(m['growth_5y'], 'pct')} a year since "
               f"{meta['growth_from']}, NIH {charts.fmt(m['nih_dollars'], 'usd')}, top platform {m['top_brand']} "
               f"{charts.fmt(m['top_brand_share'], 'pct')}")
        pts.append({"label": t.get("short_label") or t["label"], "href": f"{t['id']}.html", "x": m["publications"], "y": m["growth_5y"],
                    "r_value": m["nih_dollars"], "color_value": m["top_brand_share"], "tip": tip})
    scatter = charts.scatter(pts, desc="Technologies by paper volume and five-year growth")
    rows = []
    for t in techs:
        pub = {int(k): v for k, v in (t["publications"]["per100k"] or t["publications"]["counts"]).items()}
        nih = {int(k): v for k, v in t["nih"]["dollars"].items()}
        pat = {int(k): v for k, v in t["patents"]["pregrant"]["counts"].items() if int(k) <= full}
        lead = t["metrics"]["top_brand"]
        lead_p = next((p for p in t["platforms"] if p["name"] == lead), None)
        conc = {int(k): v for k, v in lead_p["share"].items()} if lead_p else {}
        rows.append({"t": t, "m": t["metrics"],
                     "s_pub": charts.spark(pub, ys, partial=partial), "s_nih": charts.spark(nih, ys),
                     "s_pat": charts.spark(pat, [y for y in ys if y <= full]), "s_conc": charts.spark(conc, ys, partial=partial)})
    return {"scatter": scatter, "rows": rows, "points": pts}


def methods_context(panel):
    """One row per technology: per signal, entries scored and the lowest precision (brand or term named when < 0.95)."""
    rows = []
    for t in panel.values():
        cells = {}
        for sig in SIGNAL_LABELS:
            ps = [r for r in t["precision"] if r["signal"] == sig and r["precision"] is not None]
            if ps:
                worst = min(ps, key=lambda r: r["precision"])
                named = worst["entry"] if len(ps) > 1 and worst["precision"] < 0.95 else None
                cells[sig] = {"n": len(ps), "min": worst["precision"], "entry": named, "low": worst["low"]}
        rows.append({"t": t, "cells": cells, "labeled": sum(int(r["sampled"]) for r in t["precision"] if r["precision"] is not None)})
    entries = [r for t in panel.values() for r in t["precision"] if r["precision"] is not None]
    return {"prec_rows": rows, "signal_labels": SIGNAL_LABELS, "n_entries": len(entries),
            "n_labeled": sum(int(r["sampled"]) for r in entries), "n_low": sum(r["low"] for r in entries),
            "low_entries": [r for r in entries if r["low"]]}


def last_changed(path):
    """Date of the last commit touching path (needs full history in CI: checkout fetch-depth 0)."""
    r = subprocess.run(["git", "log", "-1", "--format=%cs", "--", path], cwd=ROOT, capture_output=True, text=True)
    return r.stdout.strip() or None


def status_context(panel, meta):
    uspto = meta.get("uspto_files", {})
    through = {k: max((f["fileDataToDate"] for n, f in uspto.items() if n.startswith(k)), default=None) for k in ("g_", "pg_")}
    modified = max((f["fileLastModifiedDateTime"][:10] for f in uspto.values()), default=None)
    snaps = meta["snapshots"]
    sources = [
        {"name": "OpenAlex", "what": "Papers and platform mentions", "snap": snaps["openalex"], "cover": "Indexing lags publication by weeks to months"},
        {"name": "NIH RePORTER", "what": "Grant project-years and award dollars", "snap": snaps["reporter"], "cover": "Current fiscal year (October to September) is partial"},
        {"name": "USPTO PatentsView bulk", "what": "Patent grants and published applications", "snap": snaps["uspto"],
         "cover": f"Grants through {through['g_']}, applications through {through['pg_']}; files last updated {modified}"},
        {"name": "SEC EDGAR", "what": "Revenue and revenue mix (XBRL)", "snap": snaps["edgar"], "cover": "Latest 10-K or 20-F per company"},
        {"name": "Month-end close", "what": "Market value (with EDGAR shares outstanding)", "snap": snaps["market_value"], "cover": "Monthly; only the derived market value is stored"},
    ]
    curated = [{"path": p, "what": w, "changed": last_changed(p)} for p, w in CURATED]
    return {"sources": sources, "curated": curated, "pending_deals": meta.get("pending_deals", [])}


def main(preview=False):
    data = json.loads((HERE / "data" / "panel.json").read_text())
    meta, panel = data["meta"], data["technologies"]
    env = Environment(loader=FileSystemLoader(HERE / "templates"), autoescape=select_autoescape(["html", "j2"]),
                      trim_blocks=True, lstrip_blocks=True)
    env.filters["fmt"] = charts.fmt
    claims_path = HERE / "data" / "claims.json"
    claims = json.loads(claims_path.read_text()) if claims_path.exists() else {}
    nav = sorted(panel.values(), key=lambda t: (STAGE_ORDER.get(t["stage"], 9), t["label"]))
    common = {"meta": meta, "nav": nav, "repo": REPO, "preview": preview, "site_url": SITE_URL,
              "analytics_token": "" if preview else ANALYTICS_TOKEN}
    if DIST.exists():
        shutil.rmtree(DIST)
    DIST.mkdir(parents=True)
    (DIST / "index.html").write_text(env.get_template("index.html.j2").render(**common, page="index", **home_context(panel, meta)))
    for t in panel.values():
        html = env.get_template("technology.html.j2").render(**common, page=t["id"], claims=claims.get(t["id"], []), **tech_context(t, meta))
        (DIST / f"{t['id']}.html").write_text(html)
    for name, ctx in (("methods", methods_context(panel)), ("status", status_context(panel, meta))):
        (DIST / f"{name}.html").write_text(env.get_template(f"{name}.html.j2").render(**common, page=name, **ctx))
    shutil.copy(HERE / "data" / "panel.json", DIST / "panel.json")
    for f in (HERE / "static").iterdir():
        shutil.copy(f, DIST / f.name)
    (DIST / ".nojekyll").touch()
    print(f"site/dist: {sum(1 for _ in DIST.rglob('*') if _.is_file())} files{' (preview)' if preview else ''}")


if __name__ == "__main__":
    main(preview="--preview" in sys.argv)

"""Build the site into site/dist from site/data/panel.json (transform.panel).
Pages: home (maturity scatter + signal table) and one page per technology.

Usage: uv run python site/build.py [--preview]
--preview marks every page as an internal preview and shows sections still waiting on Josh
(Product takes, checked statements) as placeholders instead of leaving them out.
"""
import json
import shutil
import sys
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import charts  # noqa: E402  (site/charts.py)

DIST = HERE / "dist"
REPO = "https://github.com/yottachem/lifesci-tools-tracker"
SHARE_MIN = 50  # platform mentions a year before shares are charted
STAGE_ORDER = {"emerging": 0, "growth": 1, "mature": 2}
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
    return {"scatter": scatter, "rows": rows}


def main(preview=False):
    data = json.loads((HERE / "data" / "panel.json").read_text())
    meta, panel = data["meta"], data["technologies"]
    env = Environment(loader=FileSystemLoader(HERE / "templates"), autoescape=select_autoescape(["html", "j2"]),
                      trim_blocks=True, lstrip_blocks=True)
    env.filters["fmt"] = charts.fmt
    nav = sorted(panel.values(), key=lambda t: (STAGE_ORDER.get(t["stage"], 9), t["label"]))
    common = {"meta": meta, "nav": nav, "repo": REPO, "preview": preview}
    if DIST.exists():
        shutil.rmtree(DIST)
    DIST.mkdir(parents=True)
    (DIST / "index.html").write_text(env.get_template("index.html.j2").render(**common, page="index", **home_context(panel, meta)))
    for t in panel.values():
        html = env.get_template("technology.html.j2").render(**common, page=t["id"], **tech_context(t, meta))
        (DIST / f"{t['id']}.html").write_text(html)
    shutil.copy(HERE / "data" / "panel.json", DIST / "panel.json")
    (DIST / ".nojekyll").touch()
    print(f"site/dist: {sum(1 for _ in DIST.rglob('*') if _.is_file())} files{' (preview)' if preview else ''}")


if __name__ == "__main__":
    main(preview="--preview" in sys.argv)

"""Checked statements: every interpretive sentence on a technology page is a claim in
analysis/claims.yaml, stored with the condition it rests on. Each run re-checks every claim:

- condition holds: the text is shown, numbers filled in from this run
- condition fails: the neutral fallback is shown (or nothing, if it's ""), marked under review
- a value the claim uses is outside its plausibility range: treated as a possible data problem,
  shown the same way, and reported separately as a data check

Writes site/data/claims.json (per technology) and analysis/results/review.json (claims under
review, which the monthly run turns into a GitHub issue). Rewriting a claim is a reviewed, manual
step: change `text` and `holds` together so the condition states exactly what the text asserts.

Values: per technology, built from site/data/panel.json. In a claim, `t` is the claim's own
technology and every technology is also reachable by id (single_cell.growth). Fractions are
fractions (0.31, shown with :pct as 31%).

Usage: uv run python -m analysis.claims            # write claims.json, print claims under review
       uv run python -m analysis.claims --values   # also print every value claims can use
       uv run python -m analysis.claims --markdown # claims under review as an issue body
"""
import html
import json
import re
import sys
from types import SimpleNamespace

import yaml

from ingest.common import ROOT

PANEL = ROOT / "site" / "data" / "panel.json"
SPEC = ROOT / "analysis" / "claims.yaml"
OUT = ROOT / "site" / "data" / "claims.json"
REVIEW = ROOT / "analysis" / "results" / "review.json"
PLACEHOLDER = re.compile(r"\{([a-z][a-z0-9_]*\.[a-z0-9_]+)(?::([a-z0-9_]+))?\}")
NAME = re.compile(r"\b([a-z][a-z0-9_]*)\.([a-z][a-z0-9_]*)\b")
SAFE = {"abs": abs, "min": min, "max": max, "round": round, "None": None, "True": True, "False": False}


def cagr(a, b, years):
    return (b / a) ** (1 / years) - 1 if a and b else None


def values(panel=None):
    """{technology id: {name: value}} for every value a claim can use."""
    data = panel or json.loads(PANEL.read_text())
    meta, techs = data["meta"], data["technologies"]
    full, start = str(meta["full_year"]), str(meta["growth_from"])
    n_years = meta["full_year"] - meta["growth_from"]
    out = {}
    for tid, t in techs.items():
        m, pubs = t["metrics"], t["publications"]
        rate = pubs["per100k"] or pubs["counts"]
        tops = sorted((p for p in t["platforms"] if not p["parent"]), key=lambda p: -(p["share"].get(full) or 0))
        lead, second = (tops + [None, None])[:2]
        vendors = list(t["vendor_share"].items())
        nih, apps = t["nih"]["dollars"], t["patents"]["pregrant"]["counts"]
        assignees = t["patents"]["pregrant"]["top_assignees"]
        gainer = max(tops, key=lambda p: (p["share"].get(full) or 0) - (p["share"].get(start) or 0), default=None)
        out[tid] = {
            "label": t["label"], "stage": t["stage"], "year": int(full), "from_year": int(start),
            "papers": m["publications"], "papers_from": pubs["counts"].get(start),
            "per100k": m["per100k"], "per100k_from": (pubs["per100k"] or {}).get(start),
            "growth": m["growth_5y"], "multiple": rate[full] / rate[start] if rate.get(start) else None,
            "nih": nih[full], "nih_from": nih[start], "nih_growth": cagr(nih[start], nih[full], n_years),
            "nih_projects": m["nih_project_years"],
            "apps": apps[full], "apps_from": apps[start], "apps_growth": cagr(apps[start], apps[full], n_years),
            "grants": m["patent_grants"],
            "top_assignee": assignees[0][0].title() if assignees else None,
            "top_assignee_n": assignees[0][1] if assignees else None,
            "use_share": t["patent_use_share"],
            "lead": lead["name"] if lead else None, "lead_share": lead["share"].get(full) if lead else None,
            "lead_share_from": lead["share"].get(start) if lead else None,
            "second": second["name"] if second else None, "second_share": second["share"].get(full) if second else None,
            "top2_share": sum(p["share"].get(full) or 0 for p in tops[:2]),
            "gainer": gainer["name"] if gainer else None,
            "gainer_share": gainer["share"].get(full) if gainer else None,
            "gainer_share_from": gainer["share"].get(start) if gainer else None,
            "gainer_launched": gainer["launched"] if gainer else None,
            "top_vendor": vendors[0][0] if vendors else None, "top_vendor_share": vendors[0][1] if vendors else None,
            "n_platforms": len(tops),
        }
    staged = sorted((k for k in out if out[k]["stage"] in ("emerging", "growth")), key=lambda k: -(out[k]["papers"] or 0))
    for k in out:
        out[k]["stage_papers_rank"] = staged.index(k) + 1 if k in staged else None
    for key, field in (("growth_rank", "growth"), ("papers_rank", "papers"), ("nih_rank", "nih"), ("apps_rank", "apps")):
        order = sorted(out, key=lambda k: -(out[k][field] or float("-inf")))
        for i, k in enumerate(order):
            out[k][key] = i + 1
    # concentration: rank of the leading platform's share, among technologies with more than one platform
    multi = sorted((k for k in out if out[k]["n_platforms"] > 1), key=lambda k: -(out[k]["lead_share"] or 0))
    use = sorted((k for k in out if out[k]["use_share"] is not None), key=lambda k: -out[k]["use_share"])
    for k, v in out.items():
        v["n_tech"], v["conc_n"] = len(out), len(multi)
        v["conc_rank"] = multi.index(k) + 1 if k in multi else None
        v["use_rank"] = use.index(k) + 1 if k in use else None
    return out


def fmt(x, spec=None):
    if x is None:
        return "n/a"
    match spec:
        case "pct":  # fraction -> whole percent
            return f"{x:.0%}"
        case "spct":  # signed
            return f"{'+' if round(x * 100) > 0 else '−' if round(x * 100) < 0 else ''}{abs(x):.0%}"
        case "apct":  # magnitude only, for "fell 5% a year"
            return f"{abs(x):.0%}"
        case "x":  # multiple
            return f"{x:.1f}x" if x < 10 else f"{x:.0f}x"
        case "usd":
            return f"${x / 1e9:,.1f} billion" if abs(x) >= 1e9 else f"${x / 1e6:,.0f} million"
        case "int":
            return f"{x:,.0f}"
        case "f1":
            return f"{x:,.1f}"
        case "ord":
            return {1: "first", 2: "second", 3: "third", 4: "fourth", 5: "fifth"}.get(x, f"{x}th")
        case "lower":
            return str(x).lower()
        case _:
            return str(x)


def namespace(v, tid):
    env = {k: SimpleNamespace(**d) for k, d in v.items()}
    env["t"] = env[tid]
    return env


def fill(text, env, as_html=True):
    def sub(m):
        obj, attr = m.group(1).split(".")
        return fmt(getattr(env[obj], attr), m.group(2))
    s = PLACEHOLDER.sub(sub, text.strip())
    if as_html:
        s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", html.escape(s, quote=False))
    return s


def holds(expr, env):
    return bool(eval(str(expr), {"__builtins__": {}}, {**SAFE, **env}))  # noqa: S307  expressions are repo-authored


def names_in(claim):
    """(object, attribute) pairs a claim reads, from its text, fallback and condition."""
    found = set()
    for s in (claim.get("text", ""), claim.get("fallback", "")):
        found |= {tuple(m.group(1).split(".")) for m in PLACEHOLDER.finditer(s or "")}
    found |= set(NAME.findall(re.sub(r"(['\"]).*?\1", "", str(claim.get("holds", "True")))))
    return found


def evaluate(v=None, spec=None):
    v = v or values()
    spec = spec or yaml.safe_load(SPEC.read_text())
    checks = spec.get("checks", {})
    out, review = {}, []
    for tid, claims in spec["claims"].items():
        env = namespace(v, tid)
        rows = []
        for c in claims:
            used = names_in(c)
            bad = sorted(f"{tid if o == 't' else o}.{a}" for o, a in used
                         if a in checks and (x := v[tid if o == "t" else o].get(a)) is not None
                         and not checks[a][0] <= x <= checks[a][1])
            ok = not bad and holds(c.get("holds", True), env)
            text = fill(c["text"], env) if ok else fill(c.get("fallback", ""), env)
            status = "ok" if ok else "data_check" if bad else "review"
            rows.append({"id": c["id"], "status": status, "html": text, "holds": str(c.get("holds", True))})
            if not ok:
                review.append({"technology": tid, "id": c["id"], "status": status, "holds": str(c.get("holds")),
                               "text": fill(c["text"], env, as_html=False), "out_of_range": bad})
        out[tid] = rows
    return out, review


def run():
    out, review = evaluate()
    OUT.write_text(json.dumps(out, indent=1))
    REVIEW.parent.mkdir(parents=True, exist_ok=True)
    REVIEW.write_text(json.dumps(review, indent=1))
    n = sum(len(r) for r in out.values())
    print(f"{n} claims, {n - len(review)} hold, {len(review)} under review")
    for r in review:
        print(f"  {r['status']:10} {r['technology']}.{r['id']}: {r['holds']}  {r['out_of_range'] or ''}")
    return out


def markdown():
    """Claims under review as a GitHub issue body (empty when none)."""
    rows = json.loads(REVIEW.read_text()) if REVIEW.exists() else []
    return "".join(f"- **{r['technology']}.{r['id']}** ({r['status']}): {r['text']}\n  holds: `{r['holds']}`"
                   f"{'  out of range: ' + ', '.join(r['out_of_range']) if r['out_of_range'] else ''}\n" for r in rows)


if __name__ == "__main__":
    if "--markdown" in sys.argv:
        sys.exit(print(markdown(), end=""))
    if "--values" in sys.argv:
        for tid, d in values().items():
            print(f"== {tid}")
            for k, x in d.items():
                print(f"  {k:18} {x if not isinstance(x, float) else round(x, 4)}")
    run() if SPEC.exists() else None

"""Platform economics and commercial traction per technology (v2), attached to the panel by transform.panel.

Economics (reference/economics.yaml): for each costed configuration, a modeled cost per unit at each
utilization level:
  cost = consumables + sequencing + (instrument / amortize_years + service) / (capacity x utilization)
Instrument and service inputs are the most recent value of the best-ranked source type (RANK).
Where nothing about consumables is public, only the instrument share is modeled.

Traction (reference/traction.yaml): unit disclosures from filings, plus ratios where the filer's
technology revenue (edgar_mix snapshot) covers the same product lines: units shipped, implied instrument
ASP, consumable revenue per instrument, reactions per instrument and consumable revenue per reaction.
Reactions per instrument also give each configuration a cost at observed use.
"""
from functools import cache

import yaml

from ingest.common import ROOT, fiscal_year, platforms

RANK = ["paid", "gsa", "list", "filing", "s10", "press"]   # model preference for instrument and service prices


@cache
def load():
    econ = yaml.safe_load((ROOT / "reference" / "economics.yaml").read_text())
    trac = yaml.safe_load((ROOT / "reference" / "traction.yaml").read_text())
    return econ, trac


def pick(values):
    """Most recent value of the best-ranked source type; ties take the lower price. None if nothing usable."""
    usable = [v for v in values or [] if v.get("model", True) and v["source_type"] in RANK]
    if not usable:
        return None
    best = min(RANK.index(v["source_type"]) for v in usable)
    top = [v for v in usable if RANK.index(v["source_type"]) == best]
    latest = max(str(v["as_of"]) for v in top)
    return min((v for v in top if str(v["as_of"]) == latest), key=lambda v: v["usd"])


def sequencing_cost(cfg, seq):
    r = cfg.get("read_pairs_m")
    return r["value"] * seq["gb_per_m_pairs"] * seq["usd_per_gb"] if r else None


def fixed_cost(entry, model):
    """Instrument and service inputs and the yearly fixed cost they imply."""
    inst = pick(entry.get("instrument"))
    if not inst:
        return None, None, None
    svc = pick(entry.get("service"))
    svc_usd, svc_default = (svc["usd"], False) if svc else (inst["usd"] * model["default_service_share"], True)
    fixed = inst["usd"] / model["amortize_years"] + svc_usd
    return inst, {"usd": svc_usd, "default": svc_default, "source": svc}, fixed


def per_unit(fixed, variable, units):
    share = fixed / units if fixed is not None and units else None
    total = share + variable if share is not None and variable is not None else None
    return {"fixed_share": share, "total": total}


def config_rows(name, entry, econ, observed):
    model, seq = econ["model"], econ["sequencing"]
    inst, svc, fixed = fixed_cost(entry, model)
    rows = []
    for cfg in entry.get("configs", []):
        cons = cfg.get("consumables")
        seq_usd = sequencing_cost(cfg, seq)
        variable = (cons["usd"] + (seq_usd or 0)) if cons else None
        cap = (cfg.get("capacity") or {}).get("per_year")
        levels = {k: per_unit(fixed, variable, cap * u if cap else None) for k, u in model["utilization"].items()}
        obs = observed.get(name)
        rows.append({
            "platform": name, "config": cfg["name"], "unit": cfg["unit"], "area_mm2": cfg.get("area_mm2"), "plex": cfg.get("plex"),
            "consumables": cons, "sequencing_usd": seq_usd, "read_pairs_m": cfg.get("read_pairs_m"), "variable": variable,
            "capacity": cfg.get("capacity"), "instrument": inst, "service": svc, "fixed_per_year": fixed, "levels": levels,
            "observed": {**obs, **per_unit(fixed, variable, obs["units_per_year"])} if obs else None,
            "core": cfg.get("core", []),
        })
    return rows


def traction(tech_id, trac, mix):
    """Unit disclosures and revenue ratios for each vendor with product lines in this technology."""
    out = []
    for vendor, v in trac["vendors"].items():
        lines = [ln for ln, d in v["lines"].items() if d["technology"] == tech_id]
        if not lines:
            continue
        metrics = {m["metric"]: m for m in v["metrics"]}
        disclosures = [{"metric": m["metric"], "line": ln, "values": {int(y): n for y, n in m["values"][ln].items()},
                        "url": m["url"], "quote": m.get("quote")}
                       for m in v["metrics"] for ln in lines if ln in m["values"]]
        rev = {}
        for end, x in (mix.get(vendor) or {}).items():
            t = x["technology"].get(tech_id) if x.get("reconciled") else None
            if t and "instrument" in t and "consumable" in t:
                rev[fiscal_year(end)] = t
        ratios = []
        cum = metrics.get("cumulative_instruments_sold")
        if cum and rev:
            c = {ln: {int(y): n for y, n in cum["values"][ln].items()} for ln in lines}
            rx = metrics.get("consumable_reactions_sold")
            r = {ln: {int(y): n for y, n in rx["values"][ln].items()} for ln in lines} if rx else {}
            for y in sorted(rev):
                if not all(y in c[ln] and y - 1 in c[ln] for ln in lines):
                    continue
                shipped = sum(c[ln][y] - c[ln][y - 1] for ln in lines)
                avg = {ln: (c[ln][y] + c[ln][y - 1]) / 2 for ln in lines}
                reactions = {ln: r[ln][y] for ln in lines if y in r.get(ln, {})}
                ratios.append({
                    "fy": y, "instrument_revenue": rev[y]["instrument"], "consumable_revenue": rev[y]["consumable"],
                    "shipped": shipped, "asp": rev[y]["instrument"] / shipped if shipped else None,
                    "per_instrument": rev[y]["consumable"] / sum(avg.values()),
                    "reactions_per_instrument": {ln: reactions[ln] / avg[ln] for ln in reactions},
                    "per_reaction": rev[y]["consumable"] / sum(reactions.values()) if len(reactions) == len(lines) else None,
                })
        out.append({"vendor": vendor, "lines": {ln: v["lines"][ln] for ln in lines}, "disclosures": disclosures, "ratios": ratios})
    return out


def observed_use(trac_rows):
    """Platform -> reactions per instrument in the latest year with ratios, from its product line."""
    out = {}
    for t in trac_rows:
        if not t["ratios"]:
            continue
        last = t["ratios"][-1]
        for ln, n in last["reactions_per_instrument"].items():
            for p in t["lines"][ln]["platforms"]:
                out[p] = {"units_per_year": n, "fy": last["fy"], "vendor": t["vendor"], "line": ln}
    return out


def award_counts(entry, awards):
    """Federal awards naming the platform in the latest USAspending snapshot, and how many are cited here."""
    found = awards.get(entry["name"]) if "name" in entry else None
    if found is None:
        return None
    values = (entry.get("instrument") or []) + (entry.get("service") or []) + [
        v for c in entry.get("configs", []) for v in c.get("core", [])]
    cited = {v["url"].rsplit("/", 1)[-1] for v in values if v["source_type"] == "paid"}
    return {"found": len(found), "cited": sum(a["id"] in cited for a in found)}


def technology(tech_id, tech, mix, awards=None):
    """Economics and traction for one technology, or None when nothing is curated for it."""
    econ, trac = load()
    names = [p["name"] for _, p in platforms(tech)]
    trac_rows = traction(tech_id, trac, mix)
    observed = observed_use(trac_rows)
    entries = [(n, econ["platforms"][n]) for n in names if n in econ["platforms"]]
    if not entries and not trac_rows:
        return None
    rows, prices = [], []
    for name, entry in entries:
        rows += config_rows(name, entry, econ, observed)
        prices.append({"platform": name, "instrument": entry.get("instrument", []), "service": entry.get("service", []),
                       "needs": entry.get("needs"), "note": entry.get("note"),
                       "awards": award_counts({**entry, "name": name}, awards or {})})
    return {"cost_unit": tech.get("cost_unit"), "model": econ["model"], "sequencing": econ["sequencing"],
            "configs": rows, "prices": prices, "traction": trac_rows}

"""Economics and traction inputs (reference/economics.yaml, reference/traction.yaml).

Every number needs a source; names must match the taxonomy; the cost model must reproduce a hand-checked
case; and implied instrument prices from filings must sit within 0.5x-2x of the schedule price.
"""
import re

import pytest

from ingest.common import load_taxonomies, load_vendors, platforms
from transform import economics

ECON, TRAC = economics.load()
TECHS = load_taxonomies()
PLATFORM_TECH = {p["name"]: t for t, tech in TECHS.items() for _, p in platforms(tech)}
SOURCE_TYPES = set(economics.RANK) | {"spec", "derived", "model", "core"}
AS_OF = re.compile(r"^\d{4}(-\d{2})?$")
ENTRIES = list(ECON["platforms"].items())


def priced(entry):
    """Every sourced value in a platform entry, with where it sits."""
    for kind in ("instrument", "service"):
        for v in entry.get(kind) or []:
            yield kind, v
    for c in entry.get("configs", []):
        for kind in ("consumables", "read_pairs_m"):
            if c.get(kind):
                yield kind, c[kind]
        for v in c.get("core", []):
            yield "core", v


@pytest.mark.parametrize("name,entry", ENTRIES, ids=[n for n, _ in ENTRIES])
def test_platform_is_in_taxonomy(name, entry):
    assert name in PLATFORM_TECH, f"{name}: not a taxonomy platform name"
    assert TECHS[PLATFORM_TECH[name]].get("cost_unit"), f"{PLATFORM_TECH[name]}: needs cost_unit in its taxonomy"


@pytest.mark.parametrize("name,entry", ENTRIES, ids=[n for n, _ in ENTRIES])
def test_every_value_is_sourced(name, entry):
    for kind, v in priced(entry):
        assert v.get("source_type") in SOURCE_TYPES, f"{name} {kind}: source_type {v.get('source_type')!r}"
        assert str(v.get("url", "")).startswith("https://"), f"{name} {kind}: needs an https url"
        if kind != "read_pairs_m":
            assert v.get("usd", 0) > 0, f"{name} {kind}: usd must be positive"
            assert AS_OF.match(str(v.get("as_of", ""))), f"{name} {kind}: as_of must be YYYY or YYYY-MM"
        if kind in ("instrument", "service"):
            assert v["source_type"] in economics.RANK, f"{name} {kind}: {v['source_type']} can't price hardware"
        if kind == "core":
            assert v["source_type"] in ("core", "paid") and v.get("facility"), f"{name}: core rates need a facility"


@pytest.mark.parametrize("name,entry", ENTRIES, ids=[n for n, _ in ENTRIES])
def test_configs_state_their_assumptions(name, entry):
    for c in entry.get("configs", []):
        assert c.get("unit"), f"{name} {c.get('name')}: needs a unit"
        if cap := c.get("capacity"):
            assert cap["per_year"] > 0 and cap.get("basis") and cap["source_type"] == "model", f"{name} {c['name']}: capacity needs a basis"


def test_pick_prefers_rank_then_recency_then_lower():
    vals = [{"usd": 3, "source_type": "gsa", "as_of": "2026-11"},
            {"usd": 2, "source_type": "paid", "as_of": "2024-01"},
            {"usd": 1, "source_type": "paid", "as_of": "2024-01"},
            {"usd": 9, "source_type": "paid", "as_of": "2025-01", "model": False}]
    assert economics.pick(vals)["usd"] == 1


def test_xenium_v1_matches_hand_check():
    # $343,144 / 5 + $38,500 = $107,129 a year; 104 slides at 50% of 208; + $4,201 consumables = $5,231
    rows = economics.config_rows("Xenium", ECON["platforms"]["Xenium"], ECON, {})
    v1 = next(r for r in rows if r["config"].startswith("v1"))
    assert v1["levels"]["typical"]["total"] == pytest.approx(5231, abs=1)


def test_traction_names_resolve():
    vendors = load_vendors()
    for vendor, v in TRAC["vendors"].items():
        assert vendor in vendors, f"{vendor}: not in reference/vendors.yaml"
        for ln, d in v["lines"].items():
            assert d["technology"] in TECHS, f"{vendor} {ln}: unknown technology"
            for p in d["platforms"]:
                assert PLATFORM_TECH.get(p) == d["technology"], f"{vendor} {ln}: {p} is not a {d['technology']} platform"
        for m in v["metrics"]:
            assert m["source_type"] == "filing" and m["url"].startswith("https://")
            assert set(m["values"]) <= set(v["lines"]), f"{vendor} {m['metric']}: unknown line"
            if m["metric"] == "cumulative_instruments_sold":
                for ln, s in m["values"].items():
                    ys = sorted(s)
                    assert all(s[a] <= s[b] for a, b in zip(ys, ys[1:])), f"{vendor} {ln}: cumulative count fell"


def schedule_price(platform_names):
    for n in platform_names:
        vals = [v for v in ECON["platforms"].get(n, {}).get("instrument") or [] if v["source_type"] in ("gsa", "list")]
        if vals:
            return max(vals, key=lambda v: str(v["as_of"]))["usd"]
    return None


def test_implied_price_reconciles_with_schedule_price():
    """Units-weighted schedule price vs implied ASP from the 10-K: outside 0.5x-2x means a data problem."""
    from transform.panel import latest
    _, mix = latest("edgar_mix")
    checked = 0
    for tech_id in {d["technology"] for v in TRAC["vendors"].values() for d in v["lines"].values()}:
        for t in economics.traction(tech_id, TRAC, mix):
            if not t["ratios"]:
                continue
            cum = next(m for m in TRAC["vendors"][t["vendor"]]["metrics"] if m["metric"] == "cumulative_instruments_sold")
            for r in t["ratios"]:
                prices = {ln: schedule_price(d["platforms"]) for ln, d in t["lines"].items()}
                if None in prices.values():
                    continue
                shipped = {ln: cum["values"][ln][r["fy"]] - cum["values"][ln][r["fy"] - 1] for ln in prices}
                weighted = sum(prices[ln] * shipped[ln] for ln in prices) / sum(shipped.values())
                assert 0.5 <= r["asp"] / weighted <= 2, f"{t['vendor']} {tech_id} FY{r['fy']}: ASP {r['asp']:,.0f} vs schedule {weighted:,.0f}"
                checked += 1
    assert checked, "no implied price was checked"


def test_paid_citations_are_in_the_award_snapshot():
    """A cited federal purchase should still be found by its platform's award_terms."""
    from ingest.common import RAW
    if not (RAW / "usaspending").exists():
        pytest.skip("no USAspending snapshot")
    from transform.panel import latest
    _, snap = latest("usaspending")
    found = {a["id"] for v in snap.values() for a in v}
    for name, entry in ENTRIES:
        for kind, v in priced(entry):
            if v["source_type"] == "paid":
                assert v["url"].rsplit("/", 1)[-1] in found, f"{name} {kind}: {v['url']} not in the snapshot"

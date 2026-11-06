"""SEC EDGAR company facts for vendors with a CIK: annual revenue, gross profit, shares outstanding.

Instrument vs consumable revenue is dimensional XBRL (ProductOrServiceAxis), which companyfacts
omits; that split needs per-filing parsing (not built yet).

Run: uv run python -m ingest.edgar
"""
import time

import requests

from ingest.common import load_vendors, user_agent, write_snapshot

URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
REVENUE = ["RevenueFromContractWithCustomerExcludingAssessedTax", "RevenueFromContractWithCustomerIncludingAssessedTax",
           "Revenues", "SalesRevenueNet"]  # PacBio tags the "including" concept from 2019
ANNUAL = ("10-K", "20-F")  # QIAGEN files a US-GAAP 20-F as a foreign private issuer
GROSS = ["GrossProfit"]
COST = ["CostOfRevenue", "CostOfGoodsAndServicesSold"]
SHARES = ["CommonStockSharesOutstanding", "WeightedAverageNumberOfSharesOutstandingBasic"]


def annual(facts, concepts, unit="USD"):
    """Fiscal-year values from 10-K (or 20-F) filings: {fy_end_date: value}. First concept with data wins per year;
    later filings override earlier ones (restatements)."""
    out = {}
    for c in concepts:
        for x in facts.get(c, {}).get("units", {}).get(unit, []):
            if x.get("form") not in ANNUAL or x.get("fp") != "FY":
                continue
            if "start" in x and not _full_year(x["start"], x["end"]):
                continue
            out.setdefault(c, {})[x["end"]] = x["val"]
    merged = {}
    for c in concepts:  # priority order
        for end, v in out.get(c, {}).items():
            merged.setdefault(end, v)
    return dict(sorted(merged.items()))


def _full_year(start, end):
    from datetime import date
    days = (date.fromisoformat(end) - date.fromisoformat(start)).days
    return 350 <= days <= 380


def run():
    out = {}
    for name, v in load_vendors().items():
        if not v.get("cik"):
            continue
        r = requests.get(URL.format(cik=v["cik"]), headers={"User-Agent": user_agent()}, timeout=90)
        r.raise_for_status()
        g = r.json()["facts"].get("us-gaap", {})
        revenue, gross, cost = annual(g, REVENUE), annual(g, GROSS), annual(g, COST)
        for end, rev in revenue.items():  # derive gross profit where only cost of revenue is tagged
            if end not in gross and end in cost:
                gross[end] = rev - cost[end]
        out[name] = {"cik": v["cik"], "ticker": v.get("ticker"), "role": v.get("role"),
                     "revenue": revenue, "gross_profit": dict(sorted(gross.items())),
                     "shares": annual(g, SHARES, unit="shares")}
        latest = max(revenue) if revenue else None
        print(f"{name}: FY ending {latest} revenue {revenue.get(latest, 0) / 1e6:,.0f}M" if latest else f"{name}: no revenue")
        time.sleep(0.2)
    return write_snapshot("edgar", out)


if __name__ == "__main__":
    print(run())

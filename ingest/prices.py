"""Monthly market value for public vendors with a CIK: month-end close x shares outstanding.

Close: Yahoo chart API (unofficial). Raw prices are never written to the repo; only the derived
monthly market value is stored. Yahoo closes are split-adjusted while EDGAR share counts are as
reported, so closes are un-adjusted with Yahoo's split events before multiplying.
Shares: latest 10-K/10-Q CommonStockSharesOutstanding at or before each month end.

Run: uv run python -m ingest.prices
"""
import time
from bisect import bisect_right
from datetime import datetime, timezone

import requests

from ingest.common import load_vendors, user_agent, write_snapshot

CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
FACTS = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
SHARE_CONCEPTS = ["CommonStockSharesOutstanding"]


def month_end_closes(ticker):
    """{YYYY-MM: unadjusted close} from 2010 on."""
    r = requests.get(CHART.format(ticker=ticker), params={"period1": 1262304000, "period2": int(time.time()),
                     "interval": "1mo", "events": "split"}, headers={"User-Agent": "Mozilla/5.0"}, timeout=60)
    r.raise_for_status()
    res = r.json()["chart"]["result"][0]
    stamps, closes = res["timestamp"], res["indicators"]["quote"][0]["close"]
    splits = sorted((s["date"], s["numerator"] / s["denominator"]) for s in res.get("events", {}).get("splits", {}).values())
    out = {}
    for t, c in zip(stamps, closes):
        if c is None:
            continue
        factor = 1.0
        for when, ratio in splits:  # undo splits that happened after this bar
            if when > t:
                factor *= ratio
        out[datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m")] = c * factor
    return out


def share_history(cik):
    """Sorted [(period_end, shares)] from 10-K and 10-Q balance sheets; latest filing wins per date."""
    r = requests.get(FACTS.format(cik=cik), headers={"User-Agent": user_agent()}, timeout=90)
    r.raise_for_status()
    g = r.json()["facts"].get("us-gaap", {})
    by_end = {}
    for c in SHARE_CONCEPTS:
        for x in g.get(c, {}).get("units", {}).get("shares", []):
            if x.get("form") in ("10-K", "10-Q"):
                by_end[x["end"]] = x["val"]
    return sorted(by_end.items())


def market_value(closes, shares):
    ends = [e for e, _ in shares]
    out = {}
    for month, close in closes.items():
        i = bisect_right(ends, f"{month}-31") - 1
        if i >= 0:
            out[month] = round(close * shares[i][1])
    return out


def run():
    out = {}
    for name, v in load_vendors().items():
        if not (v.get("cik") and v.get("ticker")):
            continue
        mv = market_value(month_end_closes(v["ticker"]), share_history(v["cik"]))
        out[name] = {"ticker": v["ticker"], "market_value_usd": mv,
                     "method": "month-end close (Yahoo, split-unadjusted) x latest reported shares outstanding (EDGAR)"}
        last = max(mv) if mv else None
        print(f"{name}: {last} ${mv[last] / 1e9:,.2f}B" if last else f"{name}: no data")
        time.sleep(0.5)
    return write_snapshot("market_value", out)


if __name__ == "__main__":
    print(run())

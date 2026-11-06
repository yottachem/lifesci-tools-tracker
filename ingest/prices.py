"""Monthly market value for public vendors with a CIK: month-end close x shares outstanding.

Close: Yahoo chart API (unofficial). Raw prices are never written to the repo; only the derived
monthly market value is stored. Yahoo closes are split-adjusted while EDGAR share counts are as
reported, so closes are un-adjusted with Yahoo's split events before multiplying.
Shares: latest reported shares outstanding at or before each month end (share_history has the fallbacks).

Run: uv run python -m ingest.prices
"""
import time
from bisect import bisect_right
from datetime import date, datetime, timezone

import requests

from ingest.common import load_vendors, user_agent, write_snapshot
from ingest.edgar_mix import NS, instance, ten_ks

CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
FACTS = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
SHARE_CONCEPTS = ["CommonStockSharesOutstanding"]
FORMS = ("10-K", "10-Q", "20-F")  # 20-F: annual share counts only (QIAGEN)
STALE_DAYS = 400  # latest share count older than this: read per-class cover pages instead


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
    """Sorted [(date, shares)]; latest filing wins per date. Balance-sheet CommonStockSharesOutstanding
    merged with the cover-page dei:EntityCommonStockSharesOutstanding (Thermo Fisher and Alamar tag
    only the cover page). When that series is stale (Bio-Rad, QIAGEN, Seer, Quantum-Si report per
    share class, which companyfacts omits), the cover page is read from each filing and summed across
    classes; every class is valued at the listed price."""
    r = requests.get(FACTS.format(cik=cik), headers={"User-Agent": user_agent()}, timeout=90)
    r.raise_for_status()
    facts = r.json()["facts"]
    by_end = {}
    for tax, concepts in (("us-gaap", SHARE_CONCEPTS), ("dei", ["EntityCommonStockSharesOutstanding"])):
        for c in concepts:
            for x in facts.get(tax, {}).get(c, {}).get("units", {}).get("shares", []):
                if x.get("form") in FORMS:
                    by_end[x["end"]] = x["val"]
    series = sorted(by_end.items())
    if series and (date.today() - date.fromisoformat(series[-1][0])).days <= STALE_DAYS:
        return series
    by_class = class_share_history(cik)
    if not by_class:
        return series
    return [x for x in series if x[0] < by_class[0][0]] + by_class


def class_share_history(cik, since=2019):
    """Cover-page share counts summed over classes, one point per 10-K/10-Q/20-F (inline XBRL, 2019 on)."""
    out = {}
    for acc, doc, _ in ten_ks(cik, since=since, forms=FORMS):
        root = instance(cik, acc, doc)
        time.sleep(0.2)
        if root is None:
            continue
        ends = {}
        for c in root.findall("xbrli:context", NS):
            inst = c.find("xbrli:period/xbrli:instant", NS)
            if inst is not None:
                ends[c.get("id")] = inst.text
        by_date = {}
        for el in root:
            if el.tag.endswith("}EntityCommonStockSharesOutstanding") and el.text and el.get("contextRef") in ends:
                d = ends[el.get("contextRef")]
                by_date[d] = by_date.get(d, 0) + float(el.text)
        for d, n in by_date.items():
            out[d] = round(n)
    return sorted(out.items())


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

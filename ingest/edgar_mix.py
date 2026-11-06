"""Revenue mix (instruments / consumables / services) from each 10-K's XBRL instance.

companyfacts omits dimensional facts, so this reads the filing itself: revenue facts whose only
dimension is srt:ProductOrServiceAxis, over a full fiscal year. Member names are company-specific
(e.g. txg:InstrumentsMember) and nested (consumables, then spatial consumables), so
reference/revenue_members.yaml maps leaf members to the mix and to technologies. Each year's mix
is reconciled against the undimensioned total revenue in the same filing.

Run: uv run python -m ingest.edgar_mix
"""
import re
import time
import xml.etree.ElementTree as ET
from datetime import date

import requests
import yaml

from ingest.common import ROOT, fiscal_year, load_vendors, user_agent, write_snapshot

SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
ARCHIVE = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{doc}"
REVENUE = {"RevenueFromContractWithCustomerExcludingAssessedTax", "RevenueFromContractWithCustomerIncludingAssessedTax", "Revenues"}
AXIS = "srt:ProductOrServiceAxis"
SPLIT = "us-gaap:RelatedPartyTransactionsByRelatedPartyAxis"  # Seer splits each product line by related party from FY2023
NS = {"xbrli": "http://www.xbrl.org/2003/instance", "xbrldi": "http://xbrl.org/2006/xbrldi"}


def ten_ks(cik, since=2018, forms=("10-K", "20-F")):
    d = requests.get(SUBMISSIONS.format(cik=cik), headers={"User-Agent": user_agent()}, timeout=60).json()["filings"]["recent"]
    for form, acc, doc, rdate in zip(d["form"], d["accessionNumber"], d["primaryDocument"], d["reportDate"]):
        if form in forms and rdate and int(rdate[:4]) >= since:
            yield acc.replace("-", ""), doc, rdate


def instance(cik, acc, doc):
    """Extracted XBRL instance for an inline-XBRL filing: <doc>_htm.xml."""
    url = ARCHIVE.format(cik=cik, acc=acc, doc=re.sub(r"\.htm$", "_htm.xml", doc))
    r = requests.get(url, headers={"User-Agent": user_agent()}, timeout=120)
    return ET.fromstring(r.content) if r.ok else None


def mix_from_instance(root, report_date):
    contexts = {}
    for c in root.findall("xbrli:context", NS):
        members = c.findall("xbrli:entity/xbrli:segment/xbrldi:explicitMember", NS)
        start, end = c.find("xbrli:period/xbrli:startDate", NS), c.find("xbrli:period/xbrli:endDate", NS)
        if start is None or end is None or end.text != report_date:
            continue
        if not 350 <= (date.fromisoformat(end.text) - date.fromisoformat(start.text)).days <= 380:
            continue
        if not members:
            contexts[c.get("id")] = "total"
        elif len(members) == 1 and members[0].get("dimension") == AXIS:
            contexts[c.get("id")] = members[0].text.strip()
        elif len(members) == 2 and {m.get("dimension") for m in members} == {AXIS, SPLIT}:
            contexts[c.get("id")] = ("split", next(m.text.strip() for m in members if m.get("dimension") == AXIS))
    out, split = {}, {}
    for el in root:
        if el.tag.split("}")[-1] in REVENUE and el.get("contextRef") in contexts and el.text:
            key = contexts[el.get("contextRef")]
            if isinstance(key, tuple):
                split.setdefault(key[1], {}).setdefault(el.get("contextRef"), float(el.text))
            else:
                out.setdefault(key, float(el.text))
    for member, parts in split.items():   # used only when the filing has no unsplit fact for the member
        out.setdefault(member, sum(parts.values()))
    return out


def apply_map(raw, groups):
    return {k: sum(raw.get(m, 0) for m in members) for k, members in groups.items()
            if any(m in raw for m in members)}


def run():
    maps = yaml.safe_load((ROOT / "reference" / "revenue_members.yaml").read_text())
    result = {}
    for name, v in load_vendors().items():
        if not v.get("cik") or v.get("role") != "pure_play" or name not in maps:
            continue
        years = {}
        for acc, doc, rdate in ten_ks(v["cik"]):
            root = instance(v["cik"], acc, doc)
            time.sleep(0.3)
            if root is None:
                continue
            raw = mix_from_instance(root, rdate)
            if not raw:
                continue
            total = raw.pop("total", None)
            mix = apply_map(raw, maps[name]["mix"])
            tech = {t: apply_map(raw, g) for t, g in maps[name].get("technology", {}).items()}
            tech = {t: g for t, g in tech.items() if g}
            ok = bool(total) and abs(sum(mix.values()) - total) <= 0.01 * total
            unmapped = sorted(set(raw) - {m for g in maps[name]["mix"].values() for m in g}
                              - {m for t in maps[name].get("technology", {}).values() for g in t.values() for m in g})
            years[rdate] = {"total": total, "mix": mix, "technology": tech, "reconciled": ok,
                            "unmapped_members": unmapped, "members": raw}
        result[name] = dict(sorted(years.items()))
        for y, x in result[name].items():
            flag = "ok" if x["reconciled"] else "MISMATCH"
            mix = ", ".join(f"{k} {val / 1e6:,.0f}M" for k, val in sorted(x["mix"].items()))
            tech = "; ".join(f"{t} {sum(g.values()) / 1e6:,.0f}M" for t, g in x["technology"].items())
            print(f"{name} FY{fiscal_year(y)} [{flag}] total {(x['total'] or 0) / 1e6:,.0f}M | {mix}" + (f" | {tech}" if tech else ""))
    return write_snapshot("edgar_mix", result)


if __name__ == "__main__":
    print(run())

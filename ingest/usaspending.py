"""USAspending.gov: federal contract awards that name a spatial (or other) platform, for price evidence.

Searches each platform's award_terms (reference/economics.yaml) over federal contracts (types A-D) since
FY2019, deduplicated per platform. Awards are evidence, not prices: each one is read and, if it is a
clean instrument, service or kit purchase, cited by hand in economics.yaml as source_type paid.

Run: uv run python -m ingest.usaspending
"""
import time

import requests
import yaml

from ingest.common import ROOT, write_snapshot

URL = "https://api.usaspending.gov/api/v2/search/spending_by_award/"
FIELDS = ["Award ID", "Recipient Name", "Award Amount", "Awarding Agency", "Description", "Start Date", "generated_internal_id"]
START = "2018-10-01"
PAGE, MAX_PAGES = 100, 5


def search(term):
    rows, page = [], 1
    while page <= MAX_PAGES:
        body = {"filters": {"keywords": [term], "award_type_codes": ["A", "B", "C", "D"],
                            "time_period": [{"start_date": START, "end_date": time.strftime("%Y-%m-%d")}]},
                "fields": FIELDS, "limit": PAGE, "page": page, "sort": "Award Amount", "order": "desc"}
        r = requests.post(URL, json=body, timeout=90)
        r.raise_for_status()
        d = r.json()
        rows += d["results"]
        time.sleep(0.5)
        if not d.get("page_metadata", {}).get("hasNext"):
            return rows
        page += 1
    return rows


def run():
    econ = yaml.safe_load((ROOT / "reference" / "economics.yaml").read_text())
    out = {}
    for name, entry in econ["platforms"].items():
        awards = {}
        for term in entry.get("award_terms", []):
            for a in search(term):
                rec = awards.setdefault(a["generated_internal_id"], {
                    "id": a["generated_internal_id"], "award_id": a["Award ID"], "recipient": a["Recipient Name"],
                    "agency": a["Awarding Agency"], "usd": a["Award Amount"], "start": a["Start Date"],
                    "description": a["Description"], "terms": []})
                rec["terms"].append(term)
        out[name] = sorted(awards.values(), key=lambda a: (a["start"] or "", a["id"]))
        print(f"{name}: {len(out[name])} awards")
    return write_snapshot("usaspending", out)


if __name__ == "__main__":
    print(run())

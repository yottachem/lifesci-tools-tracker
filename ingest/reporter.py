"""NIH RePORTER: projects matching each technology's nih_terms, by fiscal year.

One record per project-year (appl_id), deduplicated across a technology's terms, with award
amount and the terms it matched. Totals per technology x fiscal year come from these rows.

Run: uv run python -m ingest.reporter
"""
import time

import requests

from ingest.common import YEARS, load_taxonomies, write_snapshot

URL = "https://api.reporter.nih.gov/v2/projects/search"
PAGE = 500
FIELDS = ["ApplId", "CoreProjectNum", "FiscalYear", "AwardAmount", "ActivityCode", "AgencyIcAdmin",
          "Organization", "ProjectTitle"]


def search(term, fy):
    """All projects for one quoted term in one fiscal year (pages of 500, 1 request/second)."""
    rows, offset = [], 0
    while True:
        body = {"criteria": {"advanced_text_search": {"operator": "and", "search_field": "projecttitle,abstracttext",
                                                      "search_text": f'"{term}"'},
                             "fiscal_years": [fy]},
                "include_fields": FIELDS, "offset": offset, "limit": PAGE}
        r = requests.post(URL, json=body, timeout=90)
        r.raise_for_status()
        d = r.json()
        rows += d["results"]
        time.sleep(1)
        offset += PAGE
        if offset >= d["meta"]["total"] or offset >= 15000:  # API caps offset at 14,999
            return rows


def run():
    out = {}
    for tech_id, tech in load_taxonomies().items():
        projects = {}
        for term in tech.get("nih_terms", []):
            for fy in YEARS:
                for p in search(term, fy):
                    rec = projects.setdefault(p["appl_id"], {
                        "appl_id": p["appl_id"], "core_project_num": p.get("core_project_num"),
                        "fiscal_year": p["fiscal_year"], "award_amount": p.get("award_amount") or 0,
                        "activity_code": p.get("activity_code"),
                        "ic": (p.get("agency_ic_admin") or {}).get("abbreviation"),
                        "org": (p.get("organization") or {}).get("org_name"),
                        "title": p.get("project_title"), "terms": []})
                    rec["terms"].append(term)
        out[tech_id] = sorted(projects.values(), key=lambda r: (r["fiscal_year"], r["appl_id"]))
        print(f"{tech_id}: {len(out[tech_id])} project-years")
    return write_snapshot("reporter", out)


if __name__ == "__main__":
    print(run())

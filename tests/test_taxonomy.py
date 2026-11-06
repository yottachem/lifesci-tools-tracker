"""Taxonomy and vendor roster rules. Failures here mean a query would silently miscount.

LAUNCH_GATE=1 also runs the launch-only checks (no verify: true left).
"""
import os
import re

import pytest
import yaml

from ingest.common import ROOT, load_taxonomies, load_vendors, platforms

TECHS = load_taxonomies()
VENDORS = load_vendors()
PLATFORMS = [(t, f, p) for t, tech in TECHS.items() for f, p in platforms(tech)]
OPERATORS = {"AND", "OR", "NOT"}


def bare_words(query):
    """Words outside quotes, minus boolean operators and parentheses."""
    unquoted = re.sub(r'"[^"]*"', " ", query)
    return [w for w in re.findall(r"[^\s()]+", unquoted) if w not in OPERATORS]


@pytest.mark.parametrize("tech_id,fam,p", PLATFORMS, ids=[p["name"] for _, _, p in PLATFORMS])
def test_brand_queries_are_fully_quoted(tech_id, fam, p):
    # Bare words are stemmed by OpenAlex: "STOmics" matched ostomy papers, "CODEX" matched codices
    assert bare_words(p["query"]) == [], f"{p['name']}: quote every term in {p['query']!r}"


@pytest.mark.parametrize("tech_id,fam,p", PLATFORMS, ids=[p["name"] for _, _, p in PLATFORMS])
def test_brands_ending_in_s_need_context(tech_id, fam, p):
    # OpenAlex strips a trailing "s" even inside quotes ("GenePS" matched "Genep")
    terms = re.findall(r'"([^"]*)"', p["query"])
    s_terms = [w for w in terms if re.search(r"[sS]$", w) and " " not in w]
    if s_terms:
        assert " AND " in p["query"] or p.get("context"), f"{p['name']}: {s_terms} need an AND context term"


@pytest.mark.parametrize("tech_id,fam,p", PLATFORMS, ids=[p["name"] for _, _, p in PLATFORMS])
def test_platform_has_known_vendor(tech_id, fam, p):
    assert p["vendor"] in VENDORS or any(p["name"] in v.get("platforms", []) for v in VENDORS.values())


def test_every_roster_platform_exists_in_taxonomy():
    names = {p["name"] for _, _, p in PLATFORMS}
    missing = {pl for v in VENDORS.values() for pl in v.get("platforms", []) if pl not in names}
    assert not missing, missing


def test_platform_names_unique_per_technology():
    for tech_id, tech in TECHS.items():
        names = [p["name"] for _, p in platforms(tech)]
        assert len(names) == len(set(names)), tech_id


@pytest.mark.skipif(os.environ.get("LAUNCH_GATE") != "1", reason="launch gate: run with LAUNCH_GATE=1")
def test_no_unverified_facts_at_launch():
    flagged = [p["name"] for _, _, p in PLATFORMS if p.get("verify")]
    assert not flagged, f"verify: true still set on {flagged}"


def test_launch_sources_match_taxonomy():
    sources = yaml.safe_load((ROOT / "reference" / "launch_sources.yaml").read_text())
    by_name = {p["name"]: p for _, _, p in PLATFORMS}
    for name, src in sources.items():
        assert name in by_name, f"launch_sources.yaml names unknown platform {name}"
        assert by_name[name].get("launched") == src["launched"], f"{name}: taxonomy and launch_sources.yaml disagree"
        assert src["source"].startswith("https://"), name


def test_public_vendors_have_cik_or_foreign_listing():
    for name, v in VENDORS.items():
        if v["status"] == "public":
            assert isinstance(v.get("cik"), int), name
        if v["status"] == "public_non_us":
            assert v.get("listing"), name


@pytest.mark.parametrize("tech_id", sorted(TECHS))
def test_patent_terms_well_formed(tech_id):
    # a patent term is a phrase, or {phrase, with: [context words]} for generic wording
    for e in TECHS[tech_id].get("patent_terms", []):
        if isinstance(e, dict):
            assert set(e) == {"phrase", "with"} and e["with"], f"{tech_id}: {e}"
        else:
            assert isinstance(e, str) and e.strip(), f"{tech_id}: {e!r}"


@pytest.mark.parametrize("tech_id", sorted(TECHS))
def test_method_terms_well_formed(tech_id):
    # a method term is an OpenAlex string, or {term, context: [words]} for generic wording
    for m in TECHS[tech_id]["method_terms"]:
        if isinstance(m, dict):
            assert set(m) == {"term", "context"} and m["context"] and '"' in m["term"], f"{tech_id}: {m}"
        else:
            assert isinstance(m, str) and m.strip(), f"{tech_id}: {m!r}"

"""Claims rules: every statement reads only values that exist, every condition evaluates, ids are unique,
and a failed condition or out-of-range value swaps in the fallback."""
import json

import pytest
import yaml

from analysis import claims

SPEC = yaml.safe_load(claims.SPEC.read_text())


@pytest.fixture(scope="module")
def vals():
    if not claims.PANEL.exists():
        pytest.skip("no panel.json")
    return claims.values()


def test_ids_unique_and_technologies_known(vals):
    for tid, cs in SPEC["claims"].items():
        assert tid in vals, tid
        ids = [c["id"] for c in cs]
        assert len(ids) == len(set(ids)), tid


def test_every_name_exists_and_condition_evaluates(vals):
    for tid, cs in SPEC["claims"].items():
        env = claims.namespace(vals, tid)
        for c in cs:
            for obj, attr in claims.names_in(c):
                assert obj in env and hasattr(env[obj], attr), f"{tid}.{c['id']}: {obj}.{attr}"
            claims.holds(c.get("holds", True), env)
            claims.fill(c["text"], env)
            claims.fill(c.get("fallback", ""), env)


def test_checks_name_real_values(vals):
    some = next(iter(vals.values()))
    for name, (lo, hi) in SPEC["checks"].items():
        assert name in some and lo < hi, name


def test_failed_condition_shows_fallback(vals):
    spec = {"checks": {"growth": [-0.3, 1.5]}, "claims": {"single_cell": [
        {"id": "a", "holds": "t.growth > 10", "text": "big", "fallback": "plain {t.papers:int}"},
        {"id": "b", "holds": "True", "text": "{t.growth:pct}", "fallback": ""}]}}
    v = json.loads(json.dumps(vals))
    out, review = claims.evaluate(v, spec)
    assert out["single_cell"][0]["status"] == "review" and out["single_cell"][0]["html"].startswith("plain")
    v["single_cell"]["growth"] = 5.0
    out, review = claims.evaluate(v, spec)
    assert out["single_cell"][1]["status"] == "data_check" and out["single_cell"][1]["html"] == ""

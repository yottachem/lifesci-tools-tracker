"""Sanity checks on the latest raw snapshots (skipped when there are none, e.g. a fresh clone)."""
import json

import pytest

from ingest.common import RAW


def latest(source):
    files = sorted((RAW / source).glob("*.json"))
    if not files:
        pytest.skip(f"no {source} snapshot")
    return json.loads(files[-1].read_text())


def test_openalex_union_between_largest_term_and_sum():
    """A union can't be below its largest term or above the sum of its terms. OpenAlex's boolean OR broke
    this for phrases with stopwords (2026-11-06), so the union uses pipe-joined filter values."""
    d = latest("openalex")
    for tech, v in d.items():
        if tech == "_total" or "union" not in v:
            continue
        for y, u in v["union"].items():
            terms = [c.get(y, 0) for c in v["methods"].values()]
            assert max(terms) <= u <= sum(terms), f"{tech} {y}: union {u}, terms {terms}"

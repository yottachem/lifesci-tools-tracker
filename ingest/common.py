"""Shared ingest helpers: taxonomy loading, secrets, raw snapshots."""
import json
import os
import subprocess
from datetime import date, timedelta
from functools import cache
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
YEARS = range(2010, date.today().year + 1)


def load_taxonomies():
    """All technologies across taxonomy/*.yaml, keyed by technology id."""
    techs = {}
    for f in sorted((ROOT / "taxonomy").glob("*.yaml")):
        techs.update(yaml.safe_load(f.read_text())["technologies"])
    return techs


def load_vendors():
    return yaml.safe_load((ROOT / "reference" / "vendors.yaml").read_text())["vendors"]


def platforms(tech):
    for fam_id, fam in tech["families"].items():
        for p in fam["platforms"]:
            yield fam_id, p


@cache
def secret(name, keychain_service):
    """Env var first, then macOS Keychain. Never log the return value."""
    if v := os.environ.get(name):
        return v
    try:
        r = subprocess.run(["security", "find-generic-password", "-s", keychain_service, "-w"],
                           capture_output=True, text=True)
    except FileNotFoundError:  # not macOS (CI uses env vars)
        return None
    return r.stdout.strip() or None


def method_term(m):
    """A method_terms entry is an OpenAlex term string, or {term, context: [...]} when the term only
    counts next to one of the context words (generic wording such as "spatial proteomics")."""
    return m["term"] if isinstance(m, dict) else m


def contact():
    """Contact email for SEC and OpenAlex: CONTACT_EMAIL, else Keychain item lifesci-tracker-contact.

    Kept out of the repo. SEC requires one; OpenAlex only uses it for its polite pool.
    """
    return secret("CONTACT_EMAIL", "lifesci-tracker-contact")


def user_agent():
    """SEC wants "name email"; URLs in the UA get 403."""
    if not (email := contact()):
        raise RuntimeError("SEC needs a contact email: set CONTACT_EMAIL or Keychain item lifesci-tracker-contact")
    return f"lifesci-tools-tracker {email}"


def write_snapshot(source, payload):
    """Raw snapshot per run: data/raw/<source>/<YYYY-MM-DD>.json."""
    d = RAW / source
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{date.today().isoformat()}.json"
    path.write_text(json.dumps(payload, indent=1, sort_keys=True))
    return path


def fiscal_year(end):
    """Fiscal year named the way the filer names it: a 52/53-week year ending 2023-01-01 is fiscal 2022."""
    return (date.fromisoformat(end) - timedelta(days=31)).year

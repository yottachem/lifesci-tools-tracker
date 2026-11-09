"""Fetch every source in turn. A source that fails keeps its last snapshot (transform reads the
latest one per source), is listed in data/raw/_failed.txt, and doesn't stop the others.

OpenAlex and USPTO raise without their keys; failure text is also scrubbed of secret values.

Run: uv run python -m ingest.run [source ...]
"""
import importlib
import os
import sys
import traceback

from ingest.common import RAW

SOURCES = ["reporter", "openalex", "uspto", "edgar", "edgar_mix", "prices", "usaspending"]
FAILED = RAW / "_failed.txt"
SECRETS = ("OPENALEX_API_KEY", "USPTO_ODP_API_KEY", "CONTACT_EMAIL")


def redact(msg):
    """Failure messages go into a public GitHub issue, which Actions doesn't mask."""
    for name in SECRETS:
        if v := os.environ.get(name):
            msg = msg.replace(v, f"<{name}>")
    return msg


def main(names):
    RAW.mkdir(parents=True, exist_ok=True)
    failed = []
    for name in names or SOURCES:
        print(f"== {name}", flush=True)
        try:
            print(importlib.import_module(f"ingest.{name}").run(), flush=True)
        except Exception as e:  # noqa: BLE001  one source failing must not stop the rest
            failed.append(redact(f"{name}: {type(e).__name__}: {e}"))
            print(f"FAILED {failed[-1]}", flush=True)
            print(redact(traceback.format_exc(limit=2)), flush=True)
    FAILED.write_text("".join(f"{f}\n" for f in failed))
    print(f"{len(failed)} source(s) failed" if failed else "all sources fetched")


if __name__ == "__main__":
    main(sys.argv[1:])

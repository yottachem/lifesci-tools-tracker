"""Carry snapshots between monthly CI runs in one rolling GitHub Release (tag `snapshots`).

data/raw and data/cache are gitignored and the Actions cache evicts entries unused for 7 days,
too short for a monthly run. The release holds:
  raw-latest.tar.gz     the latest snapshot per source (what transform reads); replaced each run
  raw-YYYY-MM.tar.gz    the same, kept per month as history
  uspto-corpus.tar.gz   the scoped USPTO corpora + the ODP stamps they were built from (~250 MB);
                        replaced only when ODP publishes new files, so CI skips the ~6 GB download
Raw snapshots hold public-source data only: no contact email, no prices.

Run: uv run python -m ingest.archive restore    (before ingest)
     uv run python -m ingest.archive save       (after ingest)
"""
import json
import os
import subprocess
import sys
import tarfile
import tempfile
from datetime import date
from pathlib import Path

from ingest.common import RAW, ROOT

TAG = "snapshots"
REPO = os.environ.get("GITHUB_REPOSITORY", "yottachem/lifesci-tools-tracker")
CORPUS = ROOT / "data" / "cache" / "uspto"
CORPUS_FILES = [f"corpus_{k}.{ext}" for k in ("granted", "pregrant") for ext in ("parquet", "stamp.json")]
RESTORED = CORPUS / "_restored_stamps.json"  # stamps as restored, to tell whether this run rebuilt a corpus


def gh(*args, check=True):
    return subprocess.run(["gh", *args, "-R", REPO], capture_output=True, text=True, check=check)


def latest_snapshots():
    return [max(d.glob("*.json")) for d in sorted(RAW.iterdir()) if d.is_dir() and any(d.glob("*.json"))]


def pack(path, files, base):
    with tarfile.open(path, "w:gz") as tar:
        for f in files:
            tar.add(f, arcname=str(f.relative_to(base)))
    return path


def corpus_stamps():
    return {k: json.loads(p.read_text()) for k in ("granted", "pregrant") if (p := CORPUS / f"corpus_{k}.stamp.json").exists()}


def restore():
    with tempfile.TemporaryDirectory() as tmp:
        r = gh("release", "download", TAG, "-p", "raw-latest.tar.gz", "-p", "uspto-corpus.tar.gz", "-D", tmp, check=False)
        if r.returncode:
            print(f"restore: no {TAG} release assets ({r.stderr.strip()[:200]})")
            return
        for name, dest in (("raw-latest.tar.gz", RAW), ("uspto-corpus.tar.gz", CORPUS)):
            p = Path(tmp) / name
            if p.exists():
                dest.mkdir(parents=True, exist_ok=True)
                with tarfile.open(p) as tar:
                    tar.extractall(dest, filter="data")
                print(f"restore: {name} -> {dest.relative_to(ROOT)}")
    CORPUS.mkdir(parents=True, exist_ok=True)
    RESTORED.write_text(json.dumps(corpus_stamps()))


def save():
    if gh("release", "view", TAG, check=False).returncode:
        gh("release", "create", TAG, "--title", "Snapshots", "--notes",
           "Raw snapshots carried between monthly runs (see ingest/archive.py). Not a software release.", "--latest=false")
    with tempfile.TemporaryDirectory() as tmp:
        snaps = latest_snapshots()
        latest = pack(Path(tmp) / "raw-latest.tar.gz", snaps, RAW)
        month = pack(Path(tmp) / f"raw-{date.today():%Y-%m}.tar.gz", snaps, RAW)
        assets = [str(latest), str(month)]
        restored = json.loads(RESTORED.read_text()) if RESTORED.exists() else None
        corpus = [CORPUS / f for f in CORPUS_FILES]
        if (restored != corpus_stamps() or "--corpus" in sys.argv) and all(f.exists() for f in corpus):
            assets.append(str(pack(Path(tmp) / "uspto-corpus.tar.gz", corpus, CORPUS)))
        gh("release", "upload", TAG, *assets, "--clobber")
        print("save: " + ", ".join(Path(a).name for a in assets))


if __name__ == "__main__":
    {"restore": restore, "save": save}[sys.argv[1]]()

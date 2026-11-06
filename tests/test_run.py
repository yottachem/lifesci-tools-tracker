import json

from ingest import run, uspto


def test_redact_scrubs_secret_values(monkeypatch):
    monkeypatch.setenv("CONTACT_EMAIL", "someone@example.org")
    monkeypatch.setenv("OPENALEX_API_KEY", "k3y")
    msg = run.redact("HTTPError: 403 for https://x?api_key=k3y&mailto=someone@example.org")
    assert "k3y" not in msg and "someone@example.org" not in msg


def test_corpus_current_needs_matching_stamps(tmp_path, monkeypatch):
    monkeypatch.setattr(uspto, "CACHE", tmp_path)
    cfg = {"files": {"doc": "a.zip", "cpc": "b.zip"}}
    index = {"a.zip": {"fileLastModifiedDateTime": "2026-03-12"}, "b.zip": {"fileLastModifiedDateTime": "2026-03-12"}}
    assert not uspto.corpus_current("granted", cfg, index)
    (tmp_path / "corpus_granted.parquet").write_bytes(b"")
    uspto.corpus_stamp("granted").write_text(json.dumps(uspto.stamps(cfg, index)))
    assert uspto.corpus_current("granted", cfg, index)
    index["b.zip"]["fileLastModifiedDateTime"] = "2026-06-01"
    assert not uspto.corpus_current("granted", cfg, index)

# lifesci-tools-tracker

Where is each life science tools technology on its adoption curve? This project places about ten technologies (spatial biology first) on a maturity map built from four public signals, refreshed monthly:

| Signal | Source | Measures |
|--------|--------|----------|
| Publications | OpenAlex | Adoption: papers using the method, and share of mentions by commercial platform |
| NIH grants | NIH RePORTER | Funded demand: projects and dollars |
| Patents | USPTO Open Data Portal (PatentsView bulk files) | Innovation: grants and published applications, top assignees |
| Companies | SEC EDGAR, month-end market data | Who sells it: revenue mix, technology segment revenue, market value |

**Status: pre-launch.** The spatial pilot is in progress; the public site is not published yet.

## Run

```sh
uv sync
uv run python -m ingest.openalex     # publications by year per method and platform
uv run python -m ingest.reporter     # NIH projects per technology
uv run python -m ingest.uspto        # patent bulk files (downloads ~6 GB on first run, then cached)
uv run python -m ingest.edgar        # annual revenue, gross profit, shares
uv run python -m ingest.edgar_mix    # revenue by product/service member from 10-K XBRL instances
uv run python -m ingest.prices       # derived monthly market value
uv run pytest
```

Keys and contact are read from environment variables, or from the macOS Keychain locally:

| Variable | Keychain item | Needed for |
|----------|---------------|------------|
| `OPENALEX_API_KEY` | `openalex-api-key` | OpenAlex ($1/day free budget; without it, a small shared per-IP budget) |
| `USPTO_ODP_API_KEY` | `uspto-odp-api-key` | USPTO bulk downloads (free; USPTO.gov account + ID.me) |
| `CONTACT_EMAIL` | `lifesci-tracker-contact` | SEC EDGAR User-Agent (required), OpenAlex polite pool |

## Layout

| Path | Purpose |
|------|---------|
| `taxonomy/` | Per technology: definition, method terms, NIH terms, patent terms, platforms and their queries |
| `reference/` | Vendor roster with ownership history and sources; curated XBRL revenue-member map |
| `sources.yaml` | Source registry: URL, auth, license |
| `ingest/` | One module per source; writes dated raw snapshots to `data/raw/` |
| `tests/` | Taxonomy and roster rules (query quoting, vendor coverage, term format) |
| `data/` | `raw/` snapshots and `cache/` bulk files; not committed |

## Counting rules

- Brand queries are fully quoted. OpenAlex stems bare words and strips a trailing "s" even inside quotes, so brands ending in "s" carry an AND context term. Stemming also joins a brand to a common word ("BioMark" matches "biomarker"), so such brands are queried with the vendor or model name.
- Terms shared with another field carry an AND context term that keeps the tracked meaning ("data-independent acquisition" needs a protein word; "single-cell proteomics" needs a mass-spec word).
- Platform share is share of mentions: a paper naming two platforms counts once for each.
- Academic methods (Slide-seq, seqFISH, DBiT-seq) count toward technology adoption, never toward a vendor's share.
- Each platform is counted under one technology.
- Only scholarly work types count (articles, reviews, preprints, conference papers and abstracts, chapters, books, dissertations, editorials, letters). Dataset, software and peer-review records are excluded.
- Spatial proteomics means antibody-based tissue imaging. Subcellular protein-localization and mass-spec tissue proteomics also call themselves "spatial proteomics" but use different tools, so they are not counted.
- Mass-spec proteomics includes mass-spec tissue and single-cell proteomics. Affinity proteomics excludes low-plex immunoassays (ELISA, bead panels). CyTOF and imaging flow count under flow cytometry.
- NIH grants for affinity proteomics are counted from method terms only (proximity extension, aptamer, single molecule array). In grant text "affinity proteomics" mostly means affinity purification-mass spec, so it is left out and the series undercounts.
- Every brand query and search term has a measured precision from a labeled random sample (`precision/`); platforms under 0.8 are flagged on the site. Counts are compared across years per 100K OpenAlex works, and the current year is marked partial.
- Patents count when a title or abstract term matches within a life-science CPC scope. Claims are not searched, so counts are a consistent lower bound. There is no title or CPC rule: some matches name the technology only as a readout or list item, and that use-only share is measured per technology in `precision/patent_use.csv` (2 to 20% across technologies; protein sequencing 32%, where nanopore patents list it as an application) and shown as a footnote.
- Market value is derived (month-end close x latest reported shares outstanding). Raw prices are not stored or published.

## License

Code: MIT. Derived data: CC-BY-4.0, subject to upstream licenses listed in `sources.yaml`.

# GSWA HyLogger Explorer

A web tool for looking at NVCL HyLogger drill-hole data across WA: where the
holes are, what minerals the scanner called down each hole, which readings we
don't trust, and which intervals a model marks as statistically unusual.

Built for CITS5553. Stack: **Next.js (`frontend/`) → FastAPI (`backend/`) →
the ETL4 PostgreSQL/PostGIS database + its Parquet, spectra and image assets**.
The ETL4 database is built from raw NVCL packages by the pipeline in `files/`;
the anomaly flags come from the root-level `anomaly.py`.

**Prototype:** https://main.d25z3xff1lg1mi.amplifyapp.com/ - this repo's
`frontend/`, hosted on AWS Amplify, calling the FastAPI backend on AWS Lambda.
On 16 Oct 2026 that API served ETL4 release `1156d65b-384b-539f-882c-c43d5d08fa79`:
405 boreholes.

---

## What the site shows

| Page | What it does |
|------|--------------|
| **Explore** (`/`) | Satellite map of every hole's collar plus a searchable hole list. Click a hole for its details: reported length, logged depth range, its mineral log one metre at a time, its core trays (SWIR and TIR mineral per tray), a real VSWIR/TIR spectrum from one sample, and the nearest holes. |
| **3D** (`/viewer3d`) | The map until you click a hole; then the hole opens beside it as a drill core - its ETL4 core-row photos wrapped round a cylinder at their true depths, with its mineral log alongside. Scroll to go down the hole; click a second hole to stand the two side by side at the same depth. `/compare?a=..&b=..` redirects here. |

The top bar shows the hole, interval and flag counts, and a **Refresh data**
button: the site keeps API results in the browser (catalogue 5 minutes,
everything else 1 hour - `frontend/lib/dataCache.js`) and that button clears them.

The mineral log is the heart of it. Three things are drawn differently on purpose:

- **solid colour** — the metre's most common mineral group, from the hole's
  `Grp1` SWIR log (TSA `sTSAS`, else VNIR `sTSAV`), when at least half of the
  metre's samples agree on it
- **grey hatching** — fewer than half agree, or no sample has a mineral call
  (`Invalid`, `Aspectral` and `NULL` readings count as no call). We show the
  gap rather than hiding it. ETL4 has no confidence value of its own yet, so
  this agreement share stands in for one (`CONFIDENCE_THRESHOLD` in `frontend/config.js`).
- **amber tick** — the anomaly model flagged this metre `high`

---

## How the data gets there

```text
NVCL web services
   │  download.py                        raw package per hole -> data/raw/<hole>/
   ▼
files/etl4/processing (steps 0-6)       validate, normalise, align samples, build Parquet
files/etl4/database   (steps 7-41)      PostgreSQL/PostGIS schema (migrations/001-006),
   │                                     import, release, export/upload to AWS
   ▼
etl4_core database + assets              local handoff, or Aurora + S3 on AWS
   ▼
backend/  FastAPI, read-only            /v1 routes; anomalies from backend/data/anomalies.csv
   ▼
frontend/lib/api.js                      raw samples -> one-metre intervals, trays,
   ▼                                     core-photo rows, spectra; cached in the browser
frontend/app + components               the pages
```

`anomaly.py` runs offline on per-hole measurement CSVs (`etl.py` writes that
format) and its interval table, with the ETL4 release/revision/axis ids, is
copied to `backend/data/anomalies.csv` - see CUSTOMISE.md, "Refresh the
anomaly flags".

---

## Run it

You need Python 3.12+ (the backend pins numpy 2.5), Node.js 18+, and read
access to an ETL4 database and its assets.

```bash
git clone https://github.com/nhuannguyen87/hylogger.git
cd hylogger
./setup.sh          # once: backend/.venv, backend/.env, frontend packages
```

Fill in `backend/.env` - the local-handoff or AWS Aurora/S3 values are
explained in `backend/README.md`, "Configuration". Then two terminal tabs:

```bash
./run-backend.sh    # FastAPI on http://localhost:8000 (try /docs and /api/health)
./run-frontend.sh   # website on http://localhost:3000
```

To use an API that is already running instead of starting your own, put
`NEXT_PUBLIC_API_BASE=https://<host>` in `frontend/.env.local` and restart the
frontend. The prototype's Lambda API only answers browsers on the prototype's
own address (CORS), so a local frontend can't borrow it unless its owner adds
your origin.

---

## Repository map

```
hylogger/
├── setup.sh, run-backend.sh, run-frontend.sh    set up and start the site
├── README.md, CUSTOMISE.md                       this file; recipes for common changes
│
├── frontend/                 Next.js site
│   ├── config.js             API address, colours, map style, thresholds - start here
│   ├── lib/api.js            every API call; turns raw ETL4 samples into what the pages draw
│   ├── lib/dataCache.js      browser cache behind it (IndexedDB)
│   ├── lib/desurvey.js, coreMesh.js   3D geometry
│   ├── app/                  pages: / (Explore), /viewer3d (3D), /compare (redirect)
│   └── components/           HoleMap (MapLibre + deck.gl), HoleDetail, StripLog,
│                             CoreTrays, CoreStripPanel, Hole3D, MapPanel, Nav
│
├── backend/                  FastAPI over ETL4, read-only - see backend/README.md
│   ├── app/main.py           every endpoint
│   ├── app/repository.py     the SQL behind them
│   ├── app/media_reader.py, s3_storage.py   Parquet / spectra / images, local or S3
│   ├── data/anomalies.csv    anomaly-model output served by /anomalies
│   ├── docs/FRONTEND_API.md  the API contract the site is built on
│   └── tests/test_api.py     integration tests against the pilot release
│
├── files/                    the ETL4 pipeline - see files/README.md
│   ├── etl4/processing/      steps 0-6: raw package checks -> prepared Parquet
│   ├── etl4/database/        steps 7-41 + migrations/: database, release, AWS batches
│   ├── special_processing/   fixes for raw holes the normal steps reject
│   └── examples/             database and batch config templates
│
├── download.py, common.py    NVCL raw-package downloader (input to files/etl4)
├── anomaly.py                cross-hole anomaly model (see below)
├── etl.py, nvcl_datasets.py  raw package -> per-hole CSVs (what anomaly.py reads)
│
└── legacy - not used by the site, see "Legacy" below:
    backend-django-old/, run-django.sh, reload-data.sh, docker-compose.yml,
    .env.example, data/, core_strip.py, mineral_strip.py,
    files/extract.py, files/site.py, files/serve.py
```

---

## The API

FastAPI documents itself at http://localhost:8000/docs; the frontend contract is
`backend/docs/FRONTEND_API.md`. The routes the site calls:

```
GET /v1/boreholes                                     map + hole list (one row per dataset)
GET /v1/boreholes/nearby                              ?latitude= &longitude= &radius_km=
GET /v1/boreholes/{hole_id}/datasets                  dataset revisions + sample axes
GET /v1/datasets/{revision_id}/logs                   log catalogue
GET /v1/datasets/{revision_id}/logs/{log_id}/values   ?axis_id= &after_sample= (mineral log, 5,000 a page)
GET /v1/datasets/{revision_id}/samples/{sample_no}    ?axis_id= &log_ids= (spectra, core-row photo)
GET /v1/datasets/{revision_id}/intervals              ?axis_id= &kind=tray|section
GET /v1/datasets/{revision_id}/anomalies              ?axis_id= &flag=high
GET /v1/image-assets/{asset_id}/content               core-row photo
```

The site sends at most 16 `log_ids` per sample - the deployed API's limit.
`/api/health` reports the database, role and active release. The older
`/api/holes...` routes are still served but the site doesn't use them.

---

## Known gaps (16 Oct 2026)

- **No anomaly flags on the current release.** `backend/data/anomalies.csv`
  holds the 1,200 intervals scored for the 5-hole pilot release
  (`fd02659c-...`); `/anomalies` only returns rows for the active release, so
  under `1156d65b-...` every hole comes back unflagged (no amber ticks, and
  "Only holes with unusual readings" lists none) until the model is re-run.
- **Holes with several datasets show one.** 15EIS001 (main, `_wedge`,
  `_wedge2`, `_wedge3`), 12CADD001 and 14KDD001 have more than one ETL4
  dataset; `lib/api.js` uses the one with the most samples (for 15EIS001,
  `_wedge3`, 495-1,702 m). Splicing them by depth is PR #7, which needs
  rebasing onto the browser-cache changes.
- **No drill-hole trajectories.** ETL4 marks every hole's trajectory
  `unavailable` (mostly withheld for confidentiality), so the map draws
  collars only and the 3D view stands cores upright.
- **The deployed API is ahead of `backend/`.** The Lambda API runs the
  S3/Lambda version of this backend from raxfxmx-svg/hylogger
  (`backend/deployment/`, mineral-log values read from S3, `ETL4_ALLOWED_ORIGINS`
  CORS, the 16-`log_ids` limit). PR #9 brings it in; until then `backend/` here
  reads log values from local files only.
- **Explore is heavy on first load.** It fetches every hole's full `Grp1` log
  (one request per 5,000 samples) to colour the map; later visits within the
  hour come from the browser cache.
- **Some non-mineral labels still count as minerals** - e.g. `NOTAROK`, `WOOD`,
  `WHITE MARKER` (they draw in the grey `UNKNOWN_COLOUR`).
- `backend/tests/test_api.py` checks ids from the pilot release, so it fails
  against `1156d65b-...`; `files/README.md` links three `files/docs/*.md` pages
  that aren't in the repo yet.

---

## About the anomaly model

The root-level `anomaly.py` implements a cross-hole anomaly model. It identifies unusual depth intervals and compares whole-hole measurement profiles. The model is unsupervised because the ETL4 pilot has no independently verified anomaly labels. Its flags are candidates for expert review, not predictions of ore or confirmed geological anomalies.

### Interval-level method

The model groups samples into one-metre intervals, builds numeric and categorical features, normalises category spelling and case, filters invalid or redundant mineral channels, and applies median/IQR-based robust scaling. It combines percentile ranks from robust distance, PCA Mahalanobis distance, PCA reconstruction error and, when available, Isolation Forest. PCA retains enough components to represent 95% of the fitted variation. LOF is recorded as a diagnostic score but is not included in the combined interval score.

Outputs include depth and sample-number ranges, available release, dataset-revision and axis identifiers, a score, flag, coverage/reliability, and a `why` field listing leading contributing features. Scores are relative to the model batch, not calibrated probabilities.

```text
ETL4 samples from multiple holes
        ↓
one-metre interval features
        ↓
robust scaling and feature filtering
        ↓
robust distance + PCA scores + optional Isolation Forest
        ↓
combined percentile score, flag and feature explanations
```

### ETL4 pilot result

The model was tested on 261,892 samples from five pilot holes: 05KCD001, 07THD002, 07THD003, 09ATD015 and 09ATD019. It produced 1,200 one-metre intervals and flagged 18 (1.5%): 10 in 07THD002, 6 in 07THD003 and 2 in 05KCD001. No intervals were flagged in the other two holes in this run. One flagged interval had low reliability; a prominent candidate was 07THD003 at 251–252 m.

These results show that the model runs on the ETL4 pilot data; they do not prove that its flags are correct. Without expert-reviewed labels, accuracy, precision and recall are unknown. Mahalanobis distance and reconstruction error are both PCA-derived, so PCA may receive extra influence in the combined score. Next steps are expert review and tests comparing the scoring signals and their weights.

---

## Legacy

Before ETL4 and FastAPI the site ran on a Django REST prototype fed by CSVs.
It is kept for reference and is not used by the site or by `./setup.sh`:

- `backend-django-old/` - the Django + DRF API, with `run-django.sh`,
  `reload-data.sh`, `docker-compose.yml` (its PostGIS database) and the root
  `.env.example`. `data/holes.csv` and `data/measurements.csv` are the real
  GSWA/NVCL rows it loads; `data/make_sample_data.py` only makes made-up
  test data. To run it:

  ```bash
  cp .env.example .env && docker compose up -d
  python3 -m venv .venv && .venv/bin/pip install -r backend-django-old/requirements.txt
  cd backend-django-old && ../.venv/bin/python manage.py migrate
  ../.venv/bin/python manage.py load_data --replace && ../.venv/bin/python manage.py detect_anomalies
  cd ..
  ./run-django.sh   # http://localhost:8000/api/holes/
  ```

- `core_strip.py`, `mineral_strip.py` - build full-resolution core-photo
  strips from NVCL tray photos for the Django API's core-strip endpoint.
- `files/extract.py`, `files/site.py`, `files/serve.py` - the first,
  static local viewer, built from `etl.py`'s CSVs.

---

## Where to take it next

- Re-run the anomaly model on the current ETL4 release
- Splice multi-dataset holes by depth (PR #7)
- Downhole survey stations once ETL4 exposes hole orientation
- Cluster holes by mineral pattern (KMeans on the PCA components) so the map can
  colour holes by group
- Predict lab chemistry from spectra — that's the third capstone brief
- Export a selection to CSV or QGIS

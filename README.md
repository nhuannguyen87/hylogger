# GSWA HyLogger Explorer

A web tool for looking at NVCL drill-hole mineral logs across WA: where the holes
are, what the minerals look like down each hole, which readings we don't trust,
and which intervals are statistically unusual.

Built for CITS5553. Stack matches the project brief: **Next.js → FastAPI→
PostgreSQL/PostGIS**, with a scikit-learn anomaly step in between. The site runs
on the FastAPI backend in `backend/`.

---

## Run it

You need Python 3.10+ and Node.js 18+, plus read access to the ETL4 database
(`etl4_core`) and its assets - see `backend/README.md` for the `backend/.env`
settings (local handoff or AWS Aurora/S3).

```bash
git clone https://github.com/nhuannguyen87/hylogger.git
cd hylogger
./run-backend.sh    # FastAPI on http://localhost:8000 (docs at /docs)
./run-frontend.sh   # website on http://localhost:3000
```

Open **http://localhost:3000**. If the API runs somewhere else, put
`NEXT_PUBLIC_API_BASE=http://<host>:8000` in `frontend/.env.local`.

---

## What's in the box

| Page | What it does |
|------|--------------|
| **Explore** | Map of every hole. Click one, get its details and its mineral log. |
| **3D** | The map until you click a hole; then it opens beside the map as a real drill core - its ETL4 core-row photos wrapped round a cylinder at their true depths, with its mineral log alongside. Scroll to go down the hole; click a second hole to compare the two at the same depth. |

The mineral log is the heart of it. Three things are drawn differently on purpose:

- **solid colour** — a mineral we're reasonably confident about
- **grey hatching** — low confidence or missing data. We show the gap rather than hiding it.
- **amber tick** — the model thinks this interval is unusual

---

## Folder map

```
hylogger/
├── run-backend.sh            start FastAPI (backend/)
├── run-frontend.sh           start Next.js
│
├── backend/                  FastAPI over ETL4 - see backend/README.md
│   ├── app/main.py           every endpoint
│   ├── app/repository.py     the SQL behind them
│   ├── app/media_reader.py   Parquet / spectra / image assets
│   ├── data/anomalies.csv    anomaly-model output served by /anomalies
│   └── docs/FRONTEND_API.md  the API contract the site is built on
│
├── backend-django-old/       the earlier Django prototype, no longer used by the site
│
└── frontend/                 Next.js
    ├── config.js             API address, colours, map style, thresholds
    ├── lib/api.js            every API call; turns raw ETL4 samples into
    │                         per-metre intervals and splices a hole's datasets by depth
    ├── app/                  the pages
    └── components/
        ├── HoleMap.jsx       MapLibre
        ├── StripLog.jsx      the mineral barcode
        ├── HoleDetail.jsx    right-hand panel
        └── Hole3D.jsx        deck.gl
```

---

## The API

FastAPI's own docs are at http://localhost:8000/docs; the frontend contract is
`backend/docs/FRONTEND_API.md`. The routes the site uses:

```
GET /v1/boreholes                              map + hole list
GET /v1/boreholes/nearby                       ?latitude= &longitude= &radius_km=
GET /v1/boreholes/{hole_id}/datasets           dataset revisions + sample axes, in splice order
GET /v1/datasets/{revision_id}/logs            log catalogue
GET /v1/datasets/{revision_id}/logs/{log_id}/values   ?axis_id= (mineral log)
GET /v1/datasets/{revision_id}/samples/{sample_no}    ?axis_id= &log_ids= (spectra, photos)
GET /v1/datasets/{revision_id}/intervals       ?axis_id= &kind=tray|section
GET /v1/datasets/{revision_id}/anomalies       ?axis_id= &flag=high
GET /v1/image-assets/{asset_id}/content        core-row photo
```

---


## About the anomaly model

The root-level `anomaly.py` implements Nitya Arya’s cross-hole anomaly model. It identifies unusual depth intervals and compares whole-hole measurement profiles. The model is unsupervised because the ETL4 pilot has no independently verified anomaly labels. Its flags are candidates for expert review, not predictions of ore or confirmed geological anomalies.

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

## Where to take it next

- Downhole survey stations once ETL4 exposes hole orientation
- Cluster holes by mineral pattern (KMeans on the PCA components) so the map can
  colour holes by group
- Predict lab chemistry from spectra — that's the third capstone brief
- Export a selection to CSV or QGIS

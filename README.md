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

The anomaly pipeline identifies statistically unusual depth intervals and drill holes
across the current HyLogger dataset.

Because there are no labelled examples of geological anomalies, the model is
unsupervised. It compares each interval with the rest of the available data rather
than attempting to predict ore or economic mineralisation.

### Interval-level detection

Measurements are first aggregated into fixed depth intervals so holes with different
sampling densities can be compared consistently.

The current pipeline uses several complementary anomaly signals:

```text
HyLogger measurements
        ↓
fixed-depth interval aggregation
        ↓
robust scaling
        ↓
├── robust distance
├── PCA Mahalanobis distance
├── PCA reconstruction error
└── Isolation Forest
        ↓
combined percentile anomaly score
```

## Where to take it next

- Downhole survey stations once ETL4 exposes hole orientation
- Cluster holes by mineral pattern (KMeans on the PCA components) so the map can
  colour holes by group
- Predict lab chemistry from spectra — that's the third capstone brief
- Export a selection to CSV or QGIS

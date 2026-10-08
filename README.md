# GSWA HyLogger Explorer

A web tool for exploring NVCL/HyLogger drill-hole data: locations, measurements,
mineral logs, core assets and statistically unusual intervals.

<<<<<<< HEAD
The current backend is **FastAPI → PostgreSQL/PostGIS and ETL4 data/assets**;
the frontend is Next.js. The earlier Django prototype is preserved separately
under `backend-django-old/`.
=======
Built for CITS5553. Stack matches the project brief: **Next.js → FastAPI→
PostgreSQL/PostGIS**, with a scikit-learn anomaly step in between. The site runs
on the FastAPI backend in `backend/`.
>>>>>>> baa66348aa4e4572af96cefa41b964299725fe13

---

## Run it

<<<<<<< HEAD
For current FastAPI setup, environment variables, and backend startup, follow
[`backend/README.md`](backend/README.md). The root `setup.sh` and Django-specific
instructions in this README belong to the earlier prototype, not the current
FastAPI backend. Start the Next.js frontend from `frontend/` using its package
scripts; its API client still needs to match the current FastAPI contract before
the two are fully integrated.
=======
You need [Docker Desktop](https://www.docker.com/products/docker-desktop/),
Python 3.10+, and Node.js 18+ on your Mac.

```bash
cd CITS5553/team
./setup.sh          # one time, takes a few minutes
```

Open **http://localhost:3000**. A hole's core photos are fetched from NVCL the
first time you open it (~1 min); `python core_strip.py --hole <id> ...` fetches
some up front. `./run-backend.sh` starts the FastAPI backend instead (same port).
>>>>>>> baa66348aa4e4572af96cefa41b964299725fe13

---

## What's in the box

| Page | What it does |
|------|--------------|
| **Explore** | Map of every hole. Click one, get its details and its mineral log. |
<<<<<<< HEAD
| **Compare** | Two holes side by side on the same depth scale, with the distance between them from PostGIS. |
| **3D** | Hole traces underground; displayed data depends on frontend/API integration. |
=======
| **3D** | The map until you click a hole; then it opens beside the map as a real drill core - its NVCL tray photos wrapped round a cylinder at their true depths, with its mineral log alongside. Scroll to go down the hole; click a second hole to compare the two at the same depth. |
>>>>>>> baa66348aa4e4572af96cefa41b964299725fe13

The mineral log is the heart of it. Three things are drawn differently on purpose:

- **solid colour** — a mineral we're reasonably confident about
- **grey hatching** — low confidence or missing data. We show the gap rather than hiding it.
- **amber tick** — the model thinks this interval is unusual

---

## Folder map

```
<<<<<<< HEAD
hylogger/
├── anomaly.py                cross-hole interval and hole anomaly model
├── backend/                   FastAPI service, tests and ETL4 anomaly CSV
├── backend/docs/              frontend API handoff documentation
├── backend-django-old/        preserved Django prototype
├── files/data/                 local ETL/model inputs and generated outputs
└── frontend/                  Next.js website and API client
=======
CITS5553/team/
├── setup.sh                  one-time setup
├── run-backend.sh            start FastAPI (backend/)
├── run-django.sh             start the Django API the site uses
├── run-frontend.sh           start Next.js
├── reload-data.sh            after new ETL output: reload + retrain
├── docker-compose.yml        Postgres + PostGIS
├── core_strip.py             NVCL tray photos -> core-photo strips (+ mineral_strip.py)
│
├── data/
│   ├── make_sample_data.py   
│   ├── holes.csv             real GSWA/NVCL holes (301)
│   └── measurements.csv      their per-metre mineral calls (275 logged)
│
├── backend-django-old/       Django + DRF - the API the site uses
│   ├── hylogger/settings.py  database, CORS, paths
│   └── holes/
│       ├── models.py         Hole, Measurement, CoreTray
│       ├── views.py          every API endpoint, one function each
│       ├── serializers.py    the exact JSON shape the site receives
│       ├── geo.py            hole trajectory maths for the 3D view
│       ├── ml/anomaly.py     scale -> PCA -> Isolation Forest
│       └── management/commands/
│           ├── load_data.py         CSV -> database
│           └── detect_anomalies.py  train + score
│
├── backend/                  FastAPI, replacing Django (see backend/README.md)
│
└── frontend/                 Next.js
    ├── config.js             colours, map style, thresholds
    ├── lib/api.js            every API call
    ├── app/                  the three pages
    └── components/
        ├── HoleMap.jsx       MapLibre
        ├── StripLog.jsx      the mineral barcode
        ├── HoleDetail.jsx    right-hand panel
        └── Hole3D.jsx        deck.gl
>>>>>>> baa66348aa4e4572af96cefa41b964299725fe13
```

---

## The API

The current FastAPI service exposes interactive documentation at
`http://127.0.0.1:8000/docs` after startup. See
[`backend/docs/FRONTEND_API.md`](backend/docs/FRONTEND_API.md) for the API contract.

```
<<<<<<< HEAD
GET /api/health
GET /v1/boreholes
GET /v1/boreholes/{hole_id}/datasets
GET /v1/datasets/{revision_id}/logs
GET /v1/datasets/{revision_id}/samples
GET /v1/datasets/{revision_id}/intervals
GET /v1/datasets/{revision_id}/anomalies?axis_id={axis_id}
```

The anomaly endpoint reads release-, dataset-revision- and axis-associated rows
from `backend/data/anomalies.csv`. The endpoint exists, but the current frontend
API client still calls the older Django-style routes, so this README does not
claim that the website is already displaying these FastAPI results.

---

## Plugging in your real data

The old two-CSV loading instructions from the Django prototype no longer describe
the ETL4/FastAPI handoff. The cross-hole model reads per-hole measurement CSVs
from a CSV root containing a `measurements/` folder. For example:

```powershell
python anomaly.py --csv-root "files\data\etl4\csv" --out "files\data\etl4-results"
```

The model writes local analysis outputs. They are not automatically copied into
the API data file or displayed by the website; those are separate integration
steps.

---
=======
GET /api/holes/                    ?search= &anomalies_only=1 &limit=
GET /api/holes/H001/
GET /api/holes/H001/measurements/  ?with_features=1
GET /api/holes/H001/anomalies/
GET /api/holes/H001/trace/         ?step_m=5
GET /api/holes/H001/nearby/        ?km=25
GET /api/distance/                 ?a=H001&b=H002
GET /api/stats/
GET /api/mineral-logs/             dominant mineral per hole, for map colours
GET /api/holes/H001/core-strip/    core-photo strip (202 while it's being built)
GET /api/holes/H001/trays/
GET /api/holes/H001/spectral-sample/  ?depth_m=
```


---

>>>>>>> baa66348aa4e4572af96cefa41b964299725fe13

## About the anomaly model

The root [`anomaly.py`](anomaly.py) model is Nitya Arya's individual anomaly-
detection contribution. It compares statistically unusual depth intervals and
whole-hole measurement profiles. The tested ETL4 pilot used five holes:
05KCD001, 07THD002, 07THD003, 09ATD015 and 09ATD019.

The model is unsupervised because the pilot data has no trusted anomaly labels.
Its flags identify candidates for review; they do not predict ore or confirm
mineralisation.

### Interval-level method

Measurements are aggregated into one-metre intervals. Numeric features are
summarised by interval, and categorical mineral calls are normalised without
case distinctions and represented as frequency deviations. Invalid and
redundant classification features are filtered. Numeric features use
median/IQR-based robust scaling. Sample-number ranges are retained, as are
`release_id`, `dataset_revision_id` and `axis_id` when supplied in the input.

```text
Measurements
     ↓
one-metre interval features
     ↓
robust scaling
<<<<<<< HEAD
     ↓
robust distance + PCA Mahalanobis + PCA reconstruction error
     + optional Isolation Forest
     ↓
average percentile-rank anomaly score
```

PCA retains components representing 95% of fitted variation. LOF is produced as
a diagnostic score when available, but is not included in the combined interval
score. The model also scores whole-hole profiles and can use geographic
neighbours as context. Outputs include interval bounds, sample-number range,
score, flag, coverage/reliability and a `why` explanation listing leading
original features.

### ETL4 pilot result

The tested input contained 261,892 samples and produced 1,200 one-metre
intervals. The run flagged 18 intervals (1.5%): 10 in 07THD002, 6 in 07THD003
and 2 in 05KCD001; none in 09ATD015 or 09ATD019. One flagged interval had low
reliability. A prominent candidate was 07THD003 at 251–252 m.

These are statistical screening results, not confirmed geological findings.
There are no independently verified labels, so accuracy, precision and recall
are not established. Scores are relative to the model batch and are not
probabilities. Expert geological review is needed before interpretation.

### Limitations and next steps

PCA can make feature relationships harder to interpret, and low-variance but
geologically meaningful signals may be missed. Mahalanobis distance and
reconstruction error are both PCA-derived, so their two contributions may give
PCA extra influence. Equal rank weighting is a baseline choice, not a validated
optimum; compare signals and weights through ablation tests and expert review.
The five-hole pilot is not enough to establish performance on all project data.

The FastAPI backend has an anomaly endpoint backed by
`backend/data/anomalies.csv`. The current frontend API client still uses the
older Django-style routes, so frontend connection to the FastAPI anomaly
endpoint remains to be completed or verified.
=======
        ↓
├── robust distance
├── PCA Mahalanobis distance
├── PCA reconstruction error
└── Isolation Forest
        ↓
combined percentile anomaly score
```
>>>>>>> baa66348aa4e4572af96cefa41b964299725fe13

## Where to take it next

- Ask geologists to review flagged and unflagged intervals, then measure agreement.
- Run ablation tests to assess the influence of PCA signals and alternative weights.
- Connect and verify the frontend against the FastAPI anomaly endpoint.

# GSWA HyLogger Explorer

A web tool for looking at NVCL drill-hole mineral logs across WA: where the holes
are, what the minerals look like down each hole, which readings we don't trust,
and which intervals are statistically unusual.

Built for CITS5553. Stack matches the project brief: **Next.js → FastAPI→
PostgreSQL/PostGIS**, with a scikit-learn anomaly step in between. The site runs
on the FastAPI backend in `backend/`.

---

## Run it

You need [Docker Desktop](https://www.docker.com/products/docker-desktop/),
Python 3.10+, and Node.js 18+ on your Mac.

```bash
cd CITS5553/team
./setup.sh          # one time, takes a few minutes
```

Open **http://localhost:3000**. A hole's core photos are fetched from NVCL the
first time you open it (~1 min); `python core_strip.py --hole <id> ...` fetches
some up front. `./run-backend.sh` starts the FastAPI backend instead (same port).

---

## What's in the box

| Page | What it does |
|------|--------------|
| **Explore** | Map of every hole. Click one, get its details and its mineral log. |
| **3D** | The map until you click a hole; then it opens beside the map as a real drill core - its NVCL tray photos wrapped round a cylinder at their true depths, with its mineral log alongside. Scroll to go down the hole; click a second hole to compare the two at the same depth. |

The mineral log is the heart of it. Three things are drawn differently on purpose:

- **solid colour** — a mineral we're reasonably confident about
- **grey hatching** — low confidence or missing data. We show the gap rather than hiding it.
- **amber tick** — the model thinks this interval is unusual

---

## Folder map

```
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
```

---

## The API

Browse it in your browser at http://localhost:8000/api/holes/ — DRF renders a
clickable version.

```
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

- Downhole survey stations instead of a single inclination/azimuth (`geo.py`)
- Cluster holes by mineral pattern (KMeans on the PCA components) so the map can
  colour holes by group
- Predict lab chemistry from spectra — that's the third capstone brief
- Export a selection to CSV or QGIS

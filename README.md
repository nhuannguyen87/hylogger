# GSWA HyLogger Explorer

A web tool for looking at NVCL drill-hole mineral logs across WA: where the holes
are, what the minerals look like down each hole, which readings we don't trust,
and which intervals are statistically unusual.

Built for CITS5553. Stack matches the project brief: **Next.js → Django REST →
PostgreSQL/PostGIS**, with a scikit-learn anomaly step in between. The site runs
on the Django API in `backend-django-old/`; the FastAPI backend in `backend/` is
replacing it (see `backend/README.md`).

---

## Run it

You need [Docker Desktop](https://www.docker.com/products/docker-desktop/),
Python 3.10+, and Node.js 18+ on your Mac.

```bash
cd CITS5553/team
./setup.sh          # one time, takes a few minutes
```

Then two terminal tabs:

```bash
./run-django.sh     # Django API on http://localhost:8000
./run-frontend.sh   # website on http://localhost:3000
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

Show this list to whoever is building the backend on your team — it's the
contract. As long as the JSON keeps this shape, the frontend doesn't care what
happens behind it.

---

## Plugging in your real data

`data/holes.csv` and `data/measurements.csv` are real GSWA/NVCL data, so
`./setup.sh` works straight away. To load more holes:

1. Make `download.py` / `etl.py` write two files into `data/`:

   **holes.csv**
   ```
   hole_id, hole_name, latitude, longitude, easting, northing,
   elevation_m, borehole_length_m, inclination_deg, azimuth_deg
   ```

   **measurements.csv**
   ```
   hole_id, sample_no, depth_from_m, depth_to_m,
   mineral_1, mineral_1_pct, mineral_2, mineral_2_pct,
   confidence, quality_flag, band_<anything>...
   ```

   Any column starting with `band_` is picked up automatically as a model
   feature. Ten bands or four hundred — nothing else needs to change.

2. `./reload-data.sh`

`quality_flag` should be one of `ok`, `low_signal`, `missing`. Rows marked
`missing` are excluded from model training, because a hole in the data isn't a
geological oddity.

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

- Downhole survey stations instead of a single inclination/azimuth (`geo.py`)
- Cluster holes by mineral pattern (KMeans on the PCA components) so the map can
  colour holes by group
- Predict lab chemistry from spectra — that's the third capstone brief
- Export a selection to CSV or QGIS

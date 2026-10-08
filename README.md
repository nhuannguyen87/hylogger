# GSWA HyLogger Explorer

A web tool for exploring NVCL/HyLogger drill-hole data: locations, measurements,
mineral logs, core assets and statistically unusual intervals.

The current backend is **FastAPI → PostgreSQL/PostGIS and ETL4 data/assets**;
the frontend is Next.js. The earlier Django prototype is preserved separately
under `backend-django-old/`.

---

## Run it

For current FastAPI setup, environment variables, and backend startup, follow
[`backend/README.md`](backend/README.md). The root `setup.sh` and Django-specific
instructions in this README belong to the earlier prototype, not the current
FastAPI backend. Start the Next.js frontend from `frontend/` using its package
scripts; its API client still needs to match the current FastAPI contract before
the two are fully integrated.

---

## What's in the box

| Page | What it does |
|------|--------------|
| **Explore** | Map of every hole. Click one, get its details and its mineral log. |
| **Compare** | Two holes side by side on the same depth scale, with the distance between them from PostGIS. |
| **3D** | Hole traces underground; displayed data depends on frontend/API integration. |

The mineral log is the heart of it. Three things are drawn differently on purpose:

- **solid colour** — a mineral we're reasonably confident about
- **grey hatching** — low confidence or missing data. We show the gap rather than hiding it.
- **amber tick** — the model thinks this interval is unusual

---

## Folder map

```
hylogger/
├── anomaly.py                cross-hole interval and hole anomaly model
├── backend/                   FastAPI service, tests and ETL4 anomaly CSV
├── backend/docs/              frontend API handoff documentation
├── backend-django-old/        preserved Django prototype
├── files/data/                 local ETL/model inputs and generated outputs
└── frontend/                  Next.js website and API client
```

---

## The API

The current FastAPI service exposes interactive documentation at
`http://127.0.0.1:8000/docs` after startup. See
[`backend/docs/FRONTEND_API.md`](backend/docs/FRONTEND_API.md) for the API contract.

```
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

## Where to take it next

- Ask geologists to review flagged and unflagged intervals, then measure agreement.
- Run ablation tests to assess the influence of PCA signals and alternative weights.
- Connect and verify the frontend against the FastAPI anomaly endpoint.

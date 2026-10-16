# How to change things

Recipes for the edits you're most likely to want. Each one names the file and
the whole change. The website is `frontend/` (Next.js) on top of `backend/`
(FastAPI over the ETL4 database) - see README.md for how they fit together.

---

## Add a mineral colour

`frontend/config.js`. Keys are mineral GROUP names exactly as the ETL4 `Grp1`
logs spell them, upper-cased with spaces turned into hyphens (`lib/api.js`
`groupName()` does that), e.g. `"White Mica"` -> `WHITE-MICA`.

```js
export const MINERAL_COLOURS = {
  CHLORITE: "#22c55e",
  "PAL-SEP": "#e879f9",   // <- new
};
```

Anything not listed falls back to `UNKNOWN_COLOUR`, so nothing breaks if you
forget one.

---

## Change the whole colour scheme

`frontend/app/globals.css`, the `:root` block at the top. Every panel, border
and text colour comes from those variables.

---

## Change what counts as "low confidence"

`frontend/config.js`

```js
export const CONFIDENCE_THRESHOLD = 0.5;   // try 0.7 to be stricter
```

Below this, intervals draw as grey hatching instead of a mineral colour.
"Confidence" here is the share of a metre's samples that agree with its most
common mineral group (`lib/api.js` `getMeasurements`) - ETL4 does not supply
a confidence value yet.

---

## Use a different basemap

`frontend/config.js`, `MAP_STYLE`. The default (Esri World Imagery) needs no
account. For topography, sign up for a free MapTiler key and use:

```js
export const MAP_STYLE = "https://api.maptiler.com/maps/topo-v2/style.json?key=YOUR_KEY";
```

---

## Show another API field in the site

1. If FastAPI doesn't return it yet, add the column to the matching query in
   `backend/app/repository.py` (e.g. `get_boreholes_v1` for `/v1/boreholes`).
   It appears in the JSON straight away - there are no serializers.
2. Map it into the shape the components use in `frontend/lib/api.js` (e.g.
   `toHole()` for hole fields), then read it in the component.

---

## Add an API endpoint

1. Write the query as a function in `backend/app/repository.py`.
2. Add a route in `backend/app/main.py`:

   ```python
   @app.get("/v1/boreholes/deepest")
   def deepest_boreholes():
       return {"items": get_deepest_boreholes()}
   ```

3. Add a matching call to `frontend/lib/api.js`:

   ```js
   export const getDeepest = () => get("/v1/boreholes/deepest");
   ```

FastAPI lists every route at http://localhost:8000/docs. Keep
`backend/docs/FRONTEND_API.md` in step - it is the contract the site is built on.

---

## Refresh the anomaly flags

The amber ticks come from `backend/data/anomalies.csv`, which `/anomalies`
filters to the active ETL4 release. It is the output of the root-level
`anomaly.py`:

```bash
python anomaly.py --out <data-root>   # reads <data-root>/csv/measurements/*.csv
cp <data-root>/csv/anomalies.csv backend/data/anomalies.csv
```

The per-hole CSVs need `release_id`, `dataset_revision_id` and `axis_id`
columns from the release being served, or the backend matches none of the
rows. An interval is flagged `high` when its combined score is in the top 5%
and at least one feature is 6+ robust SDs out (`flag_of()` in `anomaly.py`).

---

## Add a page

Create `frontend/app/clusters/page.js` with `"use client"` at the top and a
default-exported component. Then add it to `VIEWS` in
`frontend/components/Nav.jsx`. Next.js picks up the route from the folder name.

---

## Look at the raw data

http://localhost:8000/docs - FastAPI's interactive docs; every `/v1` route
can be tried from there against the live database.

---

## Saved data in the browser

The site keeps successful API results in the browser (IndexedDB, via
`frontend/lib/dataCache.js`): the hole catalogue for 5 minutes, everything
else for 1 hour. **Refresh data** in the top bar clears it. If you change the
shape of what `lib/api.js` returns, bump `DB_NAME` in `dataCache.js` (or the
`"adapter-v1"` key in `api.js`) so old saved results aren't reused.

---

## Reset everything

```bash
rm -rf backend/.venv frontend/node_modules frontend/.next
./setup.sh
```

`backend/.env` is kept; delete it too to start from `backend/.env.example`.

---

## When something breaks

**The site loads but the hole list is empty** — the API isn't running, or
can't reach its database. Open http://localhost:8000/api/health: connected, it
names the database, role and active release; otherwise it answers 503 and the
`./run-backend.sh` terminal shows why.

**`ETL4_READ_DSN is not configured`** (in that terminal) — `backend/.env` is
missing or still the template. Fill it in (README.md, "Run it").

**Map is blank but holes appear in the list** — the basemap URL in `config.js`
is unreachable. Check your network, or switch `MAP_STYLE` to
`https://demotiles.maplibre.org/style.json`.

**Port 3000 or 8000 already in use** — something else is on it.
`lsof -i :8000` shows what. Run the frontend on another port with
`cd frontend && npm run dev -- -p 3002`; for the backend, change `--port` in
`run-backend.sh` and set `NEXT_PUBLIC_API_BASE` in `frontend/.env.local` to match.

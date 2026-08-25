# How to change things

Recipes for the edits you're most likely to want. Each one names the file and
the whole change.

---

## Add a mineral colour

`frontend/config.js`

```js
export const MINERAL_COLOURS = {
  Quartz: "#e8e3d8",
  Chalcedony: "#9ad1e0",   // <- new
};
```

Anything not listed falls back to grey, so nothing breaks if you forget one.

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

---

## Use a nicer basemap

`frontend/config.js`. The default needs no account. For topography, sign up for
a free MapTiler key and use:

```js
export const MAP_STYLE = "https://api.maptiler.com/maps/topo-v2/style.json?key=YOUR_KEY";
```

---

## Add a field to the API

Three steps, always in this order:

1. `backend/holes/models.py` — add the field to the model
2. `cd backend && python manage.py makemigrations && python manage.py migrate`
3. `backend/holes/serializers.py` — add the field name to `fields`

Then it appears in the JSON and you can use it in the frontend.

---

## Add an API endpoint

1. Write a function in `backend/holes/views.py`:

   ```python
   @api_view(["GET"])
   def deepest_holes(request):
       rows = Hole.objects.order_by("-borehole_length_m")[:10]
       return Response(HoleListSerializer(rows, many=True).data)
   ```

2. Add one line to `backend/holes/urls.py`:

   ```python
   path("deepest/", views.deepest_holes),
   ```

3. Add a matching line to `frontend/lib/api.js`:

   ```js
   export const getDeepest = () => get("/deepest/");
   ```

---

## Make the model flag more or fewer intervals

```bash
cd backend
python manage.py detect_anomalies --contamination 0.10   # flag ~10%
python manage.py detect_anomalies --threshold 0.85       # or set the cutoff directly
```

To change the defaults permanently, edit `CONTAMINATION` and `N_COMPONENTS` at
the top of `backend/holes/ml/anomaly.py`.

---

## Swap Isolation Forest for something else

`backend/holes/ml/anomaly.py`, the `fit` function. Keep the same return shape —
`(model, scores_0_to_1, reconstruction_errors)` — and nothing else has to change.

For example, a LocalOutlierFactor version:

```python
from sklearn.neighbors import LocalOutlierFactor
forest = LocalOutlierFactor(n_neighbors=25, novelty=True, contamination=contamination)
forest.fit(components)
```

---

## Add a fourth page

Create `frontend/app/clusters/page.js` with `"use client"` at the top and a
default-exported component. Then add it to `VIEWS` in
`frontend/components/Nav.jsx`. Next.js picks up the route from the folder name.

---

## Look at the raw data

```bash
cd backend
python manage.py createsuperuser
```

Then http://localhost:8000/admin/ — searchable tables of holes and measurements.
Useful for checking your ETL output landed properly.

---

## Reset everything

```bash
docker compose down -v    # deletes the database and its data
./setup.sh                # rebuild from scratch
```

---

## When something breaks

**`connection refused` on port 5432** — Docker isn't running. Open Docker
Desktop, wait, then `docker compose up -d`.

**The site loads but the hole list is empty** — the API isn't running, or has no
data. Check http://localhost:8000/api/stats/ in a browser.

**`CREATE EXTENSION postgis` fails** — you're pointing at a plain Postgres, not
the PostGIS image. `docker compose down -v` then `./setup.sh`.

**Map is blank but holes appear in the list** — the basemap URL in `config.js`
is unreachable. Check your network, or switch `MAP_STYLE` to
`https://demotiles.maplibre.org/style.json`.

**Port 3000 or 8000 already in use** — something else is on it.
`lsof -i :8000` shows what, or run Django on another port:
`python manage.py runserver 8001` and set `NEXT_PUBLIC_API_BASE` in
`frontend/.env.local` to match.

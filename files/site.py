#!/usr/bin/env python
"""Step 4 - data/csv/wells/*.csv + holes.csv + collars.xlsx  ->  data/site/

Builds a local 3D drill-core viewer: a MapLibre scene with one extruded column
per hole and a stripe-log panel beside it. Intervals the HyLogger was unsure
about are drawn grey instead of a mineral colour, so a reader can never mistake
a guess for a fact.

Hole data is written as one small JSON file per hole and fetched on demand, so
the site stays usable with thousands of holes. That means it must be served over
http - serve.py does that. Opening index.html straight off disk will show a
"start the local server" message instead of a blank map.

    python site.py --out data
    python serve.py --out data
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


HERE = Path(__file__).resolve().parent
BIN_FALLBACK = 1.0
WIDTH_M = 12.0  # width of a drawn column, in metres

# The TSA mineral groups, in the GSWA colours. Colour is the only saturated
# thing in this interface, so a colour on screen always means a mineral.
MINERAL_COLOURS = {
    "CHLORITE": "#3cb44b", "AMPHIBOLE": "#4363d8", "KAOLIN": "#f58231",
    "WHITE-MICA": "#e6194b", "DARK-MICA": "#911eb4", "CARBONATE": "#46f0f0",
    "SULPHATE": "#ffe119", "EPIDOTE": "#bcf60c", "SMECTITE": "#f032e6",
    "OTHER-MGOH": "#008080", "SERPENTINE": "#9a6324", "TOURMALINE": "#800000",
    "OTHER": "#7f7f7f", "NO DATA": "#5f6368",
}
EXTRA_COLOURS = ["#e6194b", "#3cb44b", "#ffe119", "#4363d8", "#f58231", "#911eb4"]

COLLAR_NAME_COLUMN = "Well Name and Info"
COLLAR_FIELDS = {
    "lat": "Latitude (degrees)",
    "lon": "Longitude (degrees)",
    "elevation": "Elevation",
    "dip": "Inclination (dip)",
    "azimuth": "Azimuth",
    "total": "Total (m)",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def safe_stem(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", str(value)).strip("._") or "hole"


def match_key(value: object) -> str:
    """Fold a hole name for joining. 'MPWD73 [chips]' -> 'mpwd73'."""
    text = re.sub(r"\[[^\]]*\]", " ", str(value or ""))
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def number(value: object) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def load_collars(path: Path | None) -> dict[str, dict]:
    """Read the GSWA collar spreadsheet, keyed by folded well name."""
    if not path or not path.is_file():
        return {}
    table = pd.read_excel(path)
    if COLLAR_NAME_COLUMN not in table.columns:
        print(f"  collars: no '{COLLAR_NAME_COLUMN}' column in {path.name}, ignoring it")
        return {}
    result: dict[str, dict] = {}
    for _, row in table.iterrows():
        key = match_key(row[COLLAR_NAME_COLUMN])
        if not key or key in result:
            continue
        result[key] = {
            field: number(row.get(column))
            for field, column in COLLAR_FIELDS.items()
            if column in table.columns
        }
    return result


def load_holes_table(csv_root: Path) -> dict[str, dict]:
    """Read etl.py's holes.csv, keyed by hole_id. This is the primary source."""
    path = csv_root / "holes.csv"
    if not path.is_file():
        return {}
    table = pd.read_csv(path, dtype=str).fillna("")
    result = {}
    for _, row in table.iterrows():
        hole_id = str(row.get("hole_id") or "").strip()
        if not hole_id:
            continue
        result[hole_id] = {
            "name": str(row.get("hole_name") or "").strip() or hole_id,
            "lon": number(row.get("longitude")),
            "lat": number(row.get("latitude")),
            "elevation": number(row.get("elevation_m")),
            "total": number(row.get("borehole_length_m")),
        }
    return result


def resolve_geometry(hole_id: str, holes: dict, collars: dict) -> tuple[dict, list[str]]:
    """holes.csv wins; the spreadsheet fills the gaps it cannot cover."""
    base = dict(holes.get(hole_id) or {})
    name = base.get("name") or hole_id
    collar = collars.get(match_key(name)) or collars.get(match_key(hole_id)) or {}
    filled = []
    place = {"name": name, "lon": base.get("lon"), "lat": base.get("lat"),
             "elevation": base.get("elevation"), "total": base.get("total"),
             "dip": None, "azimuth": None}
    for field in ("lon", "lat", "elevation", "total"):
        if place[field] is None and collar.get(field) is not None:
            place[field] = collar[field]
            filled.append(field)
    # holes.csv carries no survey angles at all, so these always come from the sheet.
    place["dip"] = collar.get("dip")
    place["azimuth"] = collar.get("azimuth")
    if place["dip"] is None:
        place["dip"] = -90.0
        filled.append("dip=vertical")
    if place["azimuth"] is None:
        place["azimuth"] = 0.0
    return place, filled


def run_length(rows: pd.DataFrame, bin_m: float, reasons: list[str]) -> list[list]:
    """Merge neighbouring metres that say the same thing.

    One polygon per metre is millions of features across a whole survey; runs
    cut that by one to two orders of magnitude with no loss of detail.
    """
    runs: list[list] = []
    for row in rows.itertuples():
        depth = float(row.depth)
        mineral = str(row.mineral)
        conf = str(row.conf_class)
        quality = number(row.confidence)
        quality = 0.0 if quality is None else round(quality, 2)
        why = "" if pd.isna(row.why) else str(row.why)
        bits = 0
        for part in (piece.strip() for piece in why.split(";") if piece.strip()):
            if part not in reasons:
                reasons.append(part)
            bits |= 1 << reasons.index(part)
        if runs and runs[-1][3] == mineral and runs[-1][4] == conf \
                and runs[-1][5] == quality and runs[-1][6] == bits \
                and abs(runs[-1][1] - depth) < 1e-6:
            runs[-1][1] = depth + bin_m
            runs[-1][2] += 1
            continue
        runs.append([depth, depth + bin_m, 1, mineral, conf, quality, bits])
    return runs


def colour_table(minerals: set[str]) -> dict[str, str]:
    table = dict(MINERAL_COLOURS)
    for index, name in enumerate(sorted(minerals - set(table))):
        table[name] = EXTRA_COLOURS[index % len(EXTRA_COLOURS)]
    return {name: table[name] for name in sorted(minerals | {"NO DATA"})}


def build(csv_root: Path, collars_path: Path | None, site_root: Path) -> dict:
    wells_dir = csv_root / "wells"
    sources = sorted(wells_dir.glob("*.csv"))
    if not sources:
        raise FileNotFoundError(
            f"no per-hole tables at {wells_dir}\nRun extract.py first."
        )

    extract_summary = {}
    summary_path = csv_root / "extract-summary.json"
    if summary_path.is_file():
        try:
            extract_summary = json.loads(summary_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            print(f"  could not read {summary_path.name}; using a {BIN_FALLBACK} m bin")
    bin_m = float(extract_summary.get("bin_m") or BIN_FALLBACK)

    holes = load_holes_table(csv_root)
    collars = load_collars(collars_path)
    print(f"  {len(holes)} holes in holes.csv, {len(collars)} rows in the collar sheet")

    holes_dir = site_root / "holes"
    holes_dir.mkdir(parents=True, exist_ok=True)
    for stale in holes_dir.glob("*.json"):
        stale.unlink()

    reasons: list[str] = []
    minerals: set[str] = set()
    collar_features, skipped = [], []
    totals = {"high": 0, "medium": 0, "low": 0}
    interval_total = 0

    for index, path in enumerate(sources, start=1):
        rows = pd.read_csv(path)
        if rows.empty:
            skipped.append({"hole": path.stem, "reason": "no intervals"})
            continue
        hole_id = str(rows["hole_id"].iloc[0]) if "hole_id" in rows else path.stem
        rows = rows.sort_values("depth")
        place, filled = resolve_geometry(hole_id, holes, collars)
        if place["lon"] is None or place["lat"] is None:
            skipped.append({"hole": hole_id, "reason": "no collar position"})
            print(f"  [{index}/{len(sources)}] {hole_id}: no collar position, not mapped")
            continue

        runs = run_length(rows, bin_m, reasons)
        minerals.update(rows["mineral"].astype(str).unique())
        counts = rows["conf_class"].value_counts().to_dict()
        for key in totals:
            totals[key] += int(counts.get(key, 0))
        interval_total += len(rows)
        depth_max = float(rows["depth"].max()) + bin_m
        total = place["total"] if place["total"] and place["total"] >= depth_max else depth_max

        payload = {
            "id": hole_id,
            "name": place["name"],
            "lon": place["lon"],
            "lat": place["lat"],
            "elevation": place["elevation"],
            "dip": place["dip"],
            "azimuth": place["azimuth"],
            "total": round(total, 1),
            "logged_to": round(depth_max, 1),
            "counts": {key: int(counts.get(key, 0)) for key in ("high", "medium", "low")},
            "filled_from_sheet": filled,
            "runs": [[round(a, 2), round(b, 2), n, m, c, q, bits]
                     for a, b, n, m, c, q, bits in runs],
        }
        (holes_dir / f"{safe_stem(hole_id)}.json").write_text(
            json.dumps(payload, separators=(",", ":")), encoding="utf-8"
        )
        collar_features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [place["lon"], place["lat"]]},
            "properties": {
                "id": hole_id,
                "file": f"{safe_stem(hole_id)}.json",
                "name": place["name"],
                "total": round(total, 1),
                "n": int(len(rows)),
                "low": int(counts.get("low", 0)),
                "med": int(counts.get("medium", 0)),
            },
        })

    graded = sum(totals.values()) or 1
    manifest = {
        "generated_at": utc_now(),
        "bin_m": bin_m,
        "width_m": WIDTH_M,
        "colours": colour_table(minerals),
        "reasons": reasons,
        "checks_used": extract_summary.get("checks_used", []),
        "checks_unavailable": extract_summary.get("checks_unavailable", []),
        "stats": {
            "holes": len(collar_features),
            "intervals": interval_total,
            "high": totals["high"],
            "medium": totals["medium"],
            "low": totals["low"],
            "low_share": round(totals["low"] / graded, 4),
        },
        "skipped": skipped,
        "collars": {"type": "FeatureCollection", "features": collar_features},
    }
    (site_root / "manifest.json").write_text(
        json.dumps(manifest, separators=(",", ":")), encoding="utf-8"
    )
    (site_root / "index.html").write_text(INDEX_HTML, encoding="utf-8")
    return manifest


INDEX_HTML = r"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>GSWA HyLogger drill-core viewer</title>
<link href="https://unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl.css" rel="stylesheet">
<script src="https://unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl.js"></script>
<style>
:root{
  --ink:#0d1117; --ink-2:#161c24; --line:#2a3340;
  --fg:#e8edf4; --fg-2:#9fb0c4; --fg-3:#6b7c91;
  --grey:#9aa0a6; --grey-mid:#c8ccd1;
  --mono:ui-monospace,SFMono-Regular,Menlo,Consolas,"Liberation Mono",monospace;
}
*{box-sizing:border-box}
body{margin:0;font:14px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif;color:var(--fg);
     background:var(--ink);-webkit-font-smoothing:antialiased}
#map{position:absolute;inset:0}
.panel{position:absolute;background:rgba(13,17,23,.92);border:1px solid var(--line);
       border-radius:10px;padding:14px 16px;backdrop-filter:blur(8px)}
#chrome{top:14px;left:14px;width:264px;max-height:calc(100vh - 28px);overflow:auto}
#title{top:14px;right:14px;max-width:340px}
#title b{display:block;font-weight:600;font-size:15px;letter-spacing:-.01em}
#title p{margin:6px 0 0;color:var(--fg-2);font-size:12.5px;line-height:1.5}
#strip{bottom:14px;right:14px;display:none;max-width:min(62vw,760px)}
h4{margin:16px 0 7px;font:500 10.5px/1 system-ui;letter-spacing:.1em;
   color:var(--fg-3);text-transform:uppercase}
h4:first-child{margin-top:0}
label{display:flex;align-items:center;gap:9px;padding:3px 0;cursor:pointer;font-size:13px}
label:hover{color:#fff}
input[type=checkbox]{accent-color:#5b7ea8;width:14px;height:14px;cursor:pointer}
input[type=range]{width:100%;accent-color:#5b7ea8}
input[type=search]{width:100%;padding:6px 8px;background:var(--ink-2);color:var(--fg);
  border:1px solid var(--line);border-radius:6px;font:13px system-ui}
input:focus-visible,label:focus-within{outline:2px solid #5b7ea8;outline-offset:2px}
.sw{width:12px;height:12px;border-radius:2px;flex:none;box-shadow:inset 0 0 0 1px rgba(255,255,255,.18)}
.hint{margin:7px 0 0;font-size:12px;line-height:1.5;color:var(--fg-3)}
.num{font-family:var(--mono);font-variant-numeric:tabular-nums}
.warn{margin-top:8px;padding:8px 10px;border-left:2px solid #c9a227;
      background:rgba(201,162,39,.09);font-size:12px;line-height:1.5;color:#e8d9a8}
.logwrap{display:flex;gap:20px;align-items:flex-start}
.log h5{margin:0 0 3px;font:600 13px system-ui}
.log .meta{margin:0 0 8px;font-size:11px;color:var(--fg-3);font-family:var(--mono)}
canvas{cursor:crosshair;display:block}
.readout{margin-top:8px;min-height:40px;max-width:210px;font-size:12px;line-height:1.5;color:var(--fg-2)}
.tag{border-radius:3px;padding:1px 5px;font-size:11px;font-weight:500}
.tag.low{background:var(--grey);color:#14181d}
.tag.medium{background:var(--grey-mid);color:#14181d}
.tag.high{background:#2f6f3a;color:#eafbef}
.maplibregl-popup-content{background:#0d1117;color:var(--fg);border:1px solid var(--line);
  border-radius:8px;font:13px system-ui;line-height:1.55;padding:11px 13px}
.maplibregl-popup-tip{border-top-color:#0d1117!important;border-bottom-color:#0d1117!important}
#gate{position:absolute;inset:0;display:none;place-items:center;background:var(--ink);z-index:9;padding:24px}
#gate div{max-width:520px}
#gate code{display:block;margin:10px 0;padding:10px 12px;background:var(--ink-2);
  border:1px solid var(--line);border-radius:6px;font-family:var(--mono);font-size:13px;color:#9fd0a8}
#status{margin-top:9px;font-size:11.5px;color:var(--fg-3);font-family:var(--mono)}
@media (prefers-reduced-motion:reduce){*{transition:none!important;animation:none!important}}
</style></head><body>

<div id="map"></div>

<div id="gate"><div>
  <b style="font-size:16px">This viewer needs a local server</b>
  <p style="color:var(--fg-2);line-height:1.6">It loads one file per hole as you move
  around the map, and browsers block that when a page is opened straight off disk.
  Start the server from your project folder, then open the address it prints.</p>
  <code>python serve.py --out data</code>
</div></div>

<div class="panel" id="title">
  <b>GSWA HyLogger drill-core viewer</b>
  <p id="headline">Loading&hellip;</p>
  <div id="checkwarn"></div>
</div>

<div class="panel" id="chrome">
  <h4>Honesty</h4>
  <label><input type="checkbox" id="honest" checked> Grey out uncertain data</label>
  <p class="hint">Turn this off to see the version that hides the guesswork &mdash;
     then turn it back on.</p>

  <h4>Find a hole</h4>
  <input type="search" id="find" list="holelist" placeholder="Hole name or id" autocomplete="off">
  <datalist id="holelist"></datalist>
  <p class="hint" id="status">&nbsp;</p>

  <h4>Basemap</h4>
  <label><input type="checkbox" id="sat" checked> Satellite imagery</label>
  <label><input type="checkbox" id="terr" checked> 3D terrain</label>

  <h4>Drillholes</h4>
  <label><input type="checkbox" id="holes" checked> Mineral logs</label>
  <label><input type="checkbox" id="marks" checked> Collar markers</label>
  <label><input type="checkbox" id="lbls" checked> Hole names</label>

  <h4>Vertical exaggeration <span class="num" id="vxlabel">1&times;</span></h4>
  <input type="range" id="vx" min="1" max="20" step="1" value="1">
  <p class="hint">Columns stand above the collar: the top of a column is 0&nbsp;m,
     the bottom is total depth.</p>

  <h4>Mineral group</h4>
  <div id="legend"></div>

  <h4>Confidence</h4>
  <label><span class="sw" style="background:#9aa0a6"></span>low &mdash; do not rely on it</label>
  <label><span class="sw" style="background:#c8ccd1"></span>medium &mdash; faded colour</label>
  <label><span class="sw" style="background:#3cb44b"></span>high &mdash; full colour</label>
</div>

<div class="panel" id="strip">
  <div class="logwrap" id="logwrap"></div>
  <p class="hint">Click another hole to compare side by side &middot;
    <a href="#" id="clear" style="color:#7ea8d4">clear</a></p>
</div>

<script>
const MIN_LOAD_ZOOM = 9;      // below this, only pinned holes are drawn
const MAX_ACTIVE = 60;        // hard cap on columns held in the scene at once
const M_PER_DEG_LAT = 110540;
const GREY = '#9aa0a6', GREY_MID = '#c8ccd1';

let M = null;                 // manifest
let map = null;
const cache = new Map();      // hole id -> payload
const pinned = new Set();     // holes kept loaded because the reader opened them
let shown = [];               // holes with an open stripe log (max 2)
let active = [];              // hole ids currently in the scene

const $ = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/[&<>"]/g, c =>
  ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));

function blend(a, b, t){
  const p = h => [1,3,5].map(i => parseInt(h.slice(i,i+2),16));
  const x = p(a), y = p(b);
  return '#' + x.map((v,i) => Math.round(v + (y[i]-v)*t).toString(16).padStart(2,'0')).join('');
}
function honestColour(mineral, conf){
  const base = M.colours[mineral] || '#7f7f7f';
  if (mineral === 'NO DATA' || conf === 'low') return GREY;
  if (conf === 'medium') return blend(base, GREY_MID, 0.45);
  return base;
}
function reasonText(bits){
  return M.reasons.filter((_, i) => bits & (1 << i)).join(' &middot; ');
}

/* ---------- geometry: one extruded block per run of like intervals ---------- */
function holeFeatures(hole){
  const mPerDegLon = 111320 * Math.cos(hole.lat * Math.PI / 180);
  const dip = (hole.dip ?? -90) * Math.PI / 180;
  const az = (hole.azimuth ?? 0) * Math.PI / 180;
  const run = Math.cos(dip);
  const dy = M.width_m / 2 / M_PER_DEG_LAT;
  const dx = M.width_m / 2 / mPerDegLon;
  return hole.runs.map(([d0, d1, n, mineral, conf, q, bits]) => {
    const mid = (d0 + d1) / 2;
    const lon = hole.lon + (mid * run * Math.sin(az)) / mPerDegLon;
    const lat = hole.lat + (mid * run * Math.cos(az)) / M_PER_DEG_LAT;
    return {
      type: 'Feature',
      geometry: {type: 'Polygon', coordinates: [[
        [lon-dx, lat-dy], [lon+dx, lat-dy], [lon+dx, lat+dy], [lon-dx, lat+dy], [lon-dx, lat-dy]
      ]]},
      properties: {
        id: hole.id, name: hole.name, mineral, conf, q, bits,
        d0, d1, n,
        colour: honestColour(mineral, conf),
        colour_raw: M.colours[mineral] || '#7f7f7f',
        base: Math.max(0, hole.total - d1),
        height: Math.max(0.1, hole.total - d0)
      }
    };
  });
}

async function fetchHole(feature){
  const id = feature.properties.id;
  if (cache.has(id)) return cache.get(id);
  const response = await fetch('holes/' + feature.properties.file);
  if (!response.ok) throw new Error(feature.properties.file + ': ' + response.status);
  const hole = await response.json();
  cache.set(id, hole);
  return hole;
}

/* ---------- decide which holes belong in the scene right now ---------- */
let loadToken = 0;
async function refresh(){
  const token = ++loadToken;
  const all = M.collars.features;
  let wanted = all.filter(f => pinned.has(f.properties.id));
  if (map.getZoom() >= MIN_LOAD_ZOOM){
    const bounds = map.getBounds();
    const centre = map.getCenter();
    const inView = all
      .filter(f => bounds.contains(f.geometry.coordinates) && !pinned.has(f.properties.id))
      .sort((a, b) =>
        (Math.hypot(a.geometry.coordinates[0]-centre.lng, a.geometry.coordinates[1]-centre.lat) -
         Math.hypot(b.geometry.coordinates[0]-centre.lng, b.geometry.coordinates[1]-centre.lat)));
    wanted = wanted.concat(inView).slice(0, MAX_ACTIVE);
  }

  const ids = wanted.map(f => f.properties.id);
  if (ids.length === active.length && ids.every((v, i) => v === active[i])) { setStatus(all.length); return; }
  active = ids;

  const loaded = await Promise.all(wanted.map(f => fetchHole(f).catch(err => {
    console.warn(err); return null;
  })));
  if (token !== loadToken) return;   // a newer refresh already won
  const features = [];
  loaded.filter(Boolean).forEach(hole => features.push(...holeFeatures(hole)));
  map.getSource('seg').setData({type: 'FeatureCollection', features});
  setStatus(all.length, features.length);
}

function setStatus(total, blocks){
  const zoomedOut = map.getZoom() < MIN_LOAD_ZOOM;
  $('status').textContent = zoomedOut
    ? `${total} holes \u00b7 zoom in to draw columns`
    : `${active.length} of ${total} holes drawn` + (blocks ? ` \u00b7 ${blocks} blocks` : '');
}

/* ---------- boot ---------- */
async function boot(){
  if (location.protocol === 'file:'){ $('gate').style.display = 'grid'; return; }
  let manifest;
  try {
    const response = await fetch('manifest.json');
    if (!response.ok) throw new Error('manifest.json: ' + response.status);
    manifest = await response.json();
  } catch (err) {
    $('headline').innerHTML = 'Could not load <span class="num">manifest.json</span>. ' +
      'Rebuild the site with <span class="num">python site.py</span>, then reload.';
    console.error(err); return;
  }
  M = manifest;
  const s = M.stats;
  $('headline').innerHTML =
    `<b class="num" style="display:inline">${s.holes}</b> holes &middot; ` +
    `<b class="num" style="display:inline">${(s.low_share*100).toFixed(0)}%</b> of intervals are low-confidence. ` +
    `Grey means the machine was not sure &mdash; not that the rock is grey. Click a hole to read it down its depth.`;
  if ((M.checks_unavailable || []).length){
    $('checkwarn').innerHTML = '<div class="warn">This provider does not publish ' +
      esc(M.checks_unavailable.join(', ')) + ', so those checks were not run. ' +
      'Confidence here is coarser than it looks.</div>';
  }
  $('legend').innerHTML = Object.entries(M.colours)
    .map(([k, v]) => `<label><span class="sw" style="background:${v}"></span>${esc(k)}</label>`).join('');
  $('holelist').innerHTML = M.collars.features
    .map(f => `<option value="${esc(f.properties.name)}">`).join('');
  if (!M.collars.features.length){
    $('headline').textContent = 'No holes have a collar position yet. Check holes.csv and collars.xlsx.';
    return;
  }
  startMap();
}

function startMap(){
  const lons = M.collars.features.map(f => f.geometry.coordinates[0]).sort((a,b)=>a-b);
  const lats = M.collars.features.map(f => f.geometry.coordinates[1]).sort((a,b)=>a-b);
  const mid = a => a[Math.floor(a.length/2)];

  map = new maplibregl.Map({
    container: 'map', center: [mid(lons), mid(lats)], zoom: 13, pitch: 66,
    bearing: -20, maxPitch: 85,
    style: {version: 8, sources: {
      osm: {type:'raster', tileSize:256, attribution:'&copy; OpenStreetMap',
            tiles:['https://tile.openstreetmap.org/{z}/{x}/{y}.png']},
      sat: {type:'raster', tileSize:256, attribution:'Esri World Imagery',
            tiles:['https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}']},
      dem: {type:'raster-dem', tileSize:256, encoding:'terrarium', maxzoom:12,
            tiles:['https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png']}
    }, layers: [{id:'osm', type:'raster', source:'osm'},
                {id:'sat', type:'raster', source:'sat'}]}
  });
  map.addControl(new maplibregl.NavigationControl({visualizePitch:true}));
  map.addControl(new maplibregl.ScaleControl());

  map.on('load', () => {
    map.setTerrain({source:'dem', exaggeration:1});
    map.addSource('seg', {type:'geojson', data:{type:'FeatureCollection', features:[]}});
    map.addSource('pts', {type:'geojson', data:M.collars});

    map.addLayer({id:'holes', type:'fill-extrusion', source:'seg', paint:{
      'fill-extrusion-color':['get','colour'],
      'fill-extrusion-base':['get','base'],
      'fill-extrusion-height':['get','height'],
      'fill-extrusion-opacity':0.96}});
    map.addLayer({id:'marks', type:'circle', source:'pts', paint:{
      'circle-radius':['interpolate',['linear'],['zoom'],6,3,14,6],
      'circle-color':'#ffcc00','circle-stroke-width':1.5,'circle-stroke-color':'#0d1117'}});
    map.addLayer({id:'lbls', type:'symbol', source:'pts',
      layout:{'text-field':['get','name'],'text-size':12,'text-offset':[0,-1.4],
              'text-allow-overlap':false},
      paint:{'text-color':'#fff','text-halo-color':'#0d1117','text-halo-width':1.4}});

    const popup = new maplibregl.Popup({closeButton:false, maxWidth:'300px'});
    map.on('click', 'holes', e => {
      const p = e.features[0].properties;
      const span = p.n > 1 ? `${p.d0}&ndash;${p.d1} m` : `${p.d0} m`;
      popup.setLngLat(e.lngLat).setHTML(
        `<b>${esc(p.name)}</b> &middot; <span class="num">${span}</span><br>${esc(p.mineral)}<br>` +
        `<span class="tag ${p.conf}">${p.conf} confidence</span>` +
        (p.bits ? `<br><small style="color:#9fb0c4">${reasonText(p.bits)}</small>` : '')
      ).addTo(map);
    });
    map.on('click', 'marks', async e => {
      const p = e.features[0].properties;
      popup.setLngLat(e.lngLat).setHTML(
        `<b>${esc(p.name)}</b><br><span class="num">${p.total} m &middot; ${p.n} intervals</span><br>` +
        `<small style="color:#9fb0c4">${p.low} low, ${p.med} medium confidence</small>`).addTo(map);
      await openLog(p.id, e.features[0]);
    });
    ['holes','marks'].forEach(layer => {
      map.on('mouseenter', layer, () => map.getCanvas().style.cursor = 'pointer');
      map.on('mouseleave', layer, () => map.getCanvas().style.cursor = '');
    });

    const vis = (id, on) => map.setLayoutProperty(id, 'visibility', on ? 'visible' : 'none');
    $('sat').onchange = e => vis('sat', e.target.checked);
    $('holes').onchange = e => vis('holes', e.target.checked);
    $('marks').onchange = e => vis('marks', e.target.checked);
    $('lbls').onchange = e => vis('lbls', e.target.checked);
    $('terr').onchange = e => map.setTerrain(
      e.target.checked ? {source:'dem', exaggeration:+$('vx').value} : null);
    $('honest').onchange = e => {
      map.setPaintProperty('holes','fill-extrusion-color',
        ['get', e.target.checked ? 'colour' : 'colour_raw']);
      drawLogs();
    };
    $('vx').oninput = e => {
      const k = +e.target.value;
      $('vxlabel').innerHTML = k + '&times;';
      map.setPaintProperty('holes','fill-extrusion-base', ['*',['get','base'], k]);
      map.setPaintProperty('holes','fill-extrusion-height',['*',['get','height'], k]);
      // keep the ground under the columns on the same scale
      if ($('terr').checked) map.setTerrain({source:'dem', exaggeration:k});
    };
    $('find').onchange = e => {
      const term = e.target.value.trim().toLowerCase();
      if (!term) return;
      const hit = M.collars.features.find(f =>
        f.properties.name.toLowerCase() === term || f.properties.id.toLowerCase() === term);
      if (!hit){ $('status').textContent = 'No hole called ' + term; return; }
      map.flyTo({center: hit.geometry.coordinates, zoom: 16, pitch: 70, duration: 1400});
      openLog(hit.properties.id, hit);
    };
    $('clear').onclick = e => {
      e.preventDefault();
      shown.forEach(id => pinned.delete(id));
      shown = []; drawLogs(); refresh();
    };

    map.on('moveend', refresh);
    map.on('zoomend', refresh);
    refresh();
  });
}

async function openLog(id, feature){
  pinned.add(id);
  shown = shown.includes(id) ? shown.filter(x => x !== id) : [...shown, id].slice(-2);
  if (!shown.includes(id)) pinned.delete(id);
  try { await fetchHole(feature); } catch (err) { console.warn(err); }
  drawLogs();
  refresh();
}

/* ---------- stripe logs: the plain, readable barcode of a hole ---------- */
const PX_PER_M = 2.6, LOG_W = 46;

function drawLogs(){
  const wrap = $('logwrap');
  $('strip').style.display = shown.length ? 'block' : 'none';
  wrap.innerHTML = '';
  const ready = shown.map(id => cache.get(id)).filter(Boolean);
  if (!ready.length) return;
  const tallest = Math.max(...ready.map(h => h.total), 1);
  const height = Math.min(tallest * PX_PER_M, window.innerHeight * 0.5);
  const scale = height / tallest;
  const useHonest = $('honest').checked;

  ready.forEach(hole => {
    const box = document.createElement('div');
    box.className = 'log';
    box.innerHTML = `<h5>${esc(hole.name)}</h5><p class="meta">0&ndash;${hole.total} m &middot; ` +
      `${hole.counts.low} of ${hole.counts.high + hole.counts.medium + hole.counts.low} ` +
      `intervals low-confidence</p>`;

    const canvas = document.createElement('canvas');
    const dpr = window.devicePixelRatio || 1;
    const w = LOG_W + 46, h = height + 14;
    canvas.width = w * dpr; canvas.height = h * dpr;
    canvas.style.width = w + 'px'; canvas.style.height = h + 'px';
    const g = canvas.getContext('2d');
    g.scale(dpr, dpr);

    hole.runs.forEach(([d0, d1, n, mineral, conf]) => {
      g.fillStyle = useHonest ? honestColour(mineral, conf) : (M.colours[mineral] || '#7f7f7f');
      g.fillRect(40, 6 + d0 * scale, LOG_W, Math.max(1, (d1 - d0) * scale));
    });
    g.strokeStyle = '#3b4757'; g.fillStyle = '#8fa0b4';
    g.font = '10px ' + getComputedStyle(document.body).getPropertyValue('--mono');
    g.textAlign = 'right';
    const step = tallest > 400 ? 100 : 50;
    for (let d = 0; d <= hole.total; d += step){
      const y = 6 + d * scale;
      g.beginPath(); g.moveTo(34, y); g.lineTo(40, y); g.stroke();
      g.fillText(d + ' m', 32, y + 3);
    }
    g.strokeStyle = '#55637a'; g.strokeRect(40, 6, LOG_W, hole.total * scale);

    const out = document.createElement('div');
    out.className = 'readout';
    const idle = 'Hover the stripe to read a depth.';
    out.textContent = idle;
    canvas.onmousemove = ev => {
      const d = (ev.offsetY - 6) / scale;
      const run = hole.runs.find(r => d >= r[0] && d < r[1]);
      out.innerHTML = run
        ? `<b class="num">${d.toFixed(1)} m</b> &mdash; ${esc(run[3])}<br>` +
          `<span class="tag ${run[4]}">${run[4]}</span>` +
          (run[6] ? `<br><small>${reasonText(run[6])}</small>` : '')
        : `<b class="num">${d.toFixed(1)} m</b> &mdash; outside the logged interval`;
    };
    canvas.onmouseleave = () => out.textContent = idle;

    box.appendChild(canvas); box.appendChild(out); wrap.appendChild(box);
  });
}

boot();
</script></body></html>
"""


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build the local 3D drill-core viewer from extract.py output."
    )
    parser.add_argument("--out", default=str(HERE / "data"), help="data root (default: ./data)")
    parser.add_argument("--csv-root", help="override <out>/csv")
    parser.add_argument("--site-root", help="override <out>/site")
    parser.add_argument("--collars", default=str(HERE / "collars.xlsx"),
                        help="GSWA collar spreadsheet (default: ./collars.xlsx)")
    args = parser.parse_args()

    data_root = Path(args.out).resolve()
    csv_root = Path(args.csv_root).resolve() if args.csv_root else data_root / "csv"
    site_root = Path(args.site_root).resolve() if args.site_root else data_root / "site"
    collars_path = Path(args.collars).resolve() if args.collars else None
    if collars_path and not collars_path.is_file():
        print(f"  collar sheet not found at {collars_path}; "
              "holes will use holes.csv only and be treated as vertical")
        collars_path = None

    site_root.mkdir(parents=True, exist_ok=True)
    try:
        manifest = build(csv_root, collars_path, site_root)
    except FileNotFoundError as exc:
        parser.error(str(exc))

    stats = manifest["stats"]
    print(f"\nwrote {site_root}")
    print(f"  {stats['holes']} holes, {stats['intervals']} intervals, "
          f"{stats['low_share']:.0%} low-confidence")
    if manifest["skipped"]:
        print(f"  {len(manifest['skipped'])} holes not mapped "
              "(see 'skipped' in manifest.json)")
    print(f"\nnow run:  python serve.py --out {data_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

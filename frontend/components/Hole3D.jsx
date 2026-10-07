"use client";

// The core viewer: each chosen hole as a real drill core - its NVCL tray
// photos wrapped round a cylinder at the depths they were logged, true to
// scale (a ~6 cm core is 6 cm wide beside a metre of depth), with the mineral
// log running beside it. Scroll to travel down the hole like a lift going
// underground, drag to turn the cores, and the strip on the right jumps
// anywhere in the hole.
//
// Cores stand side by side at the same depth so two holes compare directly.
// That spacing is not their real distance - the map shows where they are.

import { useEffect, useMemo, useRef, useState } from "react";
import DeckGL, {
  COORDINATE_SYSTEM, LineLayer, OrbitView, ScatterplotLayer, SimpleMeshLayer, TextLayer,
} from "deck.gl";
import { getCoreStrip, getMeasurements } from "@/lib/api";
import {
  ANOMALY_COLOUR, CONFIDENCE_THRESHOLD, LOW_CONFIDENCE_COLOUR, MEDIA_BASE, mineralColour, toRgb,
} from "@/config";
import { CORE_MESH, MAX_TEXTURE_DIM, PHOTO_CORE_MESH, loadImage, segmentTransform } from "@/lib/coreMesh";

const HOLE_COLOURS = ["#4fd1c5", "#b98cff"]; // --accent, --accent-b: the map's A and B
const FALLBACK_DIAMETER_M = 0.06; // a hole with no photos to measure: typical NQ-HQ core
const START_VISIBLE_M = 1.2; // opening view: about one tray row of core
const PHOTO_WINDOW_MAX_M = 40; // zoomed out past this, cores draw in mineral colours (no photo fetches)
const POLL_MS = 5000; // while the backend builds a hole's strip (~1 min, first view only)
const MAX_SHEETS = 12; // decoded sheet images kept (each up to 120 x 20,000 px)
const MAX_ROW_TEXTURES = 200; // cropped tray-row textures kept
const PHOTO_MATERIAL = { ambient: 0.65, diffuse: 0.45, shininess: 6, specularColor: [25, 25, 25] };
const VIEW = new OrbitView({ orbitAxis: "Z" });
const ORIGIN = () => [0, 0, 0]; // every segment's real position is baked into its transform
const TRANSFORM = (d) => d?.transform; // deck.gl probes this with data[0] - undefined when a layer is empty
const GREY = [96, 108, 120];

export default function Hole3D({ holeIds, distanceKm, onClose }) {
  const byHole = useHolesData(holeIds);
  const containerRef = useRef(null);
  const headerRef = useRef(null);
  const height = useHeight(containerRef);
  const headerHeight = useHeight(headerRef, 40);
  const [depth, setDepth] = useState(null);
  const [view, setView] = useState({
    target: [0, 0, 0], zoom: 9, rotationOrbit: 0, rotationX: 0,
    minZoom: 0, maxZoom: 14, minRotationX: -60, maxRotationX: 60,
  });
  const [hover, setHover] = useState(null);

  const scene = useMemo(() => describe(holeIds, byHole), [holeIds, byHole]);
  const visible = height / 2 ** view.zoom; // metres of hole on screen
  const clamp = (d) => Math.min(Math.max(d, scene.top), scene.bottom);

  // open on the first hole's photographed (else logged) top, about a tray row in view
  const firstHole = holeIds[0];
  useEffect(() => setDepth(null), [firstHole]);
  useEffect(() => {
    if (depth !== null || !scene.ready) return;
    const first = scene.cores[0];
    const start = (first.photos || first.logged || [scene.top])[0];
    setView((v) => ({ ...v, zoom: Math.log2(height / START_VISIBLE_M), rotationOrbit: 0, rotationX: 0 }));
    setDepth(start + START_VISIBLE_M / 2);
  }, [depth, scene, height]);

  const range = depth === null ? null : [depth - visible * 0.75 - 0.5, depth + visible * 0.75 + 0.5]; // depths to draw
  const photosOn = visible <= PHOTO_WINDOW_MAX_M;
  const wanted = useMemo(
    () => (range && photosOn ? rowsIn(scene.cores, range) : []),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [scene, range?.[0].toFixed(1), range?.[1].toFixed(1), photosOn],
  );
  const textures = useRowTextures(wanted);

  const layers = useMemo(
    () => (range ? buildLayers(scene, range, depth, visible, ((headerHeight + 30) / height) * visible, photosOn, textures, setHover) : []),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [scene, range?.[0], range?.[1], depth, visible, headerHeight, height, photosOn, textures.version],
  );

  function move(deltaM) {
    if (depth !== null) setDepth(clamp(depth + deltaM));
  }

  function onKeyDown(event) {
    const step = { ArrowDown: 0.25, ArrowUp: -0.25, PageDown: 0.9, PageUp: -0.9 }[event.key];
    if (step) {
      event.preventDefault();
      move(step * visible);
    } else if (event.key === "Home" || event.key === "End") {
      event.preventDefault();
      setDepth(event.key === "Home" ? scene.top : scene.bottom);
    }
  }

  return (
    <div
      ref={containerRef}
      className="core3d"
      tabIndex={0}
      onKeyDown={onKeyDown}
      onWheel={(event) => move((event.deltaY * visible) / 800)}
      aria-label="3D drill core viewer - scroll or use the arrow keys to move down the hole"
    >
      <DeckGL
        views={VIEW}
        viewState={{ ...view, target: [view.target[0], view.target[1], -(depth ?? 0)] }}
        onViewStateChange={({ viewState }) => {
          setView(viewState);
          if (depth !== null) setDepth(clamp(-viewState.target[2]));
        }}
        controller={{ scrollZoom: false, keyboard: false, inertia: false }}
        layers={layers}
        getCursor={({ isDragging }) => (isDragging ? "grabbing" : "grab")}
        style={{ position: "absolute", inset: 0 }}
      />

      <header ref={headerRef} className="core3d-head">
        {scene.cores.map((core, i) => (
          <span key={core.holeId} className="core3d-chip" style={{ borderColor: HOLE_COLOURS[i] }} title={core.strip?.unavailable}>
            <span className="dot" style={{ background: HOLE_COLOURS[i] }} />
            <b className="mono">{core.holeId}</b>
            <span className="hint">{photoStatus(core)}</span>
            <button type="button" aria-label={`Close ${core.holeId}`} onClick={() => onClose(core.holeId)}>×</button>
          </span>
        ))}
        {scene.cores.length === 2 && distanceKm != null && (
          <span className="hint">
            {distanceKm.toFixed(2)} km apart on the map - drawn side by side here, at the same depth
          </span>
        )}
      </header>

      {scene.ready && scene.cores.every((core) => !core.rows.length && !core.measurements.length && !core.strip?.building) && (
        <p className="core3d-empty">
          NVCL has no tray photos or mineral log for {holeIds.join(" or ")}, so there is nothing to draw here.
          Pick another hole on the map.
        </p>
      )}

      {scene.ready && (
        <DepthNavigator scene={scene} depth={depth} visible={visible} onJump={(d) => setDepth(clamp(d))} />
      )}

      <footer className="core3d-foot">
        <span className="mono core3d-depth">{depth === null ? "…" : `${depth.toFixed(1)} m`}</span>
        <span className="seg" style={{ marginTop: 0 }}>
          <button type="button" onClick={() => setView((v) => ({ ...v, zoom: Math.min(v.zoom + 0.5, 14) }))}>+</button>
          <button type="button" onClick={() => setView((v) => ({ ...v, zoom: Math.max(v.zoom - 0.5, 0) }))}>−</button>
          <button type="button" onClick={() => setView((v) => ({ ...v, rotationOrbit: 0, rotationX: 0, target: [0, 0, v.target[2]] }))}>
            Face on
          </button>
        </span>
        <span className="hint">
          {photosOn ? "Scroll or ↑↓ to go deeper · drag to turn · strip on the right: jump" : "Zoom in (+) to see the core photos"}
          <br />
          NVCL tray photos (depth within a tray row interpolated) · beside each core its mineral log:
          grey = uncertain, amber dot = unusual
        </span>
      </footer>

      {hover && (
        <div
          className="tooltip"
          style={{
            position: "absolute", top: hover.y + 14, maxWidth: 300, whiteSpace: "normal",
            // flip left near the panel's right edge instead of being cut off
            left: hover.x + 330 > (containerRef.current?.clientWidth ?? 0) ? hover.x - 314 : hover.x + 14,
          }}
        >
          <b>{hover.object.holeId}</b> · {hover.object.from.toFixed(2)}–{hover.object.to.toFixed(2)} m
          <br />
          {hover.object.label}
        </div>
      )}
    </div>
  );
}

/** Measurements and core-strip for each hole, the strip polled while the
 * backend is still building it from NVCL. */
function useHolesData(holeIds) {
  const [byHole, setByHole] = useState({});
  const key = holeIds.join("|");

  useEffect(() => {
    let cancelled = false;
    const timers = [];
    const put = (holeId, patch) => {
      if (!cancelled) setByHole((all) => ({ ...all, [holeId]: { ...all[holeId], ...patch } }));
    };
    for (const holeId of holeIds) {
      getMeasurements(holeId)
        .then((measurements) => put(holeId, { measurements }))
        .catch(() => put(holeId, { measurements: [] }));
      const loadStrip = () => getCoreStrip(holeId)
        .then((strip) => {
          put(holeId, { strip });
          if (strip?.building) timers.push(setTimeout(loadStrip, POLL_MS));
        })
        .catch(() => put(holeId, { strip: { unavailable: "The API didn't answer." } }));
      loadStrip();
    }
    return () => {
      cancelled = true;
      timers.forEach(clearTimeout);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  return byHole;
}

function useHeight(ref, fallback = 600) {
  const [height, setHeight] = useState(fallback);
  useEffect(() => {
    const observer = new ResizeObserver(([entry]) => setHeight(entry.contentRect.height || fallback));
    observer.observe(ref.current);
    return () => observer.disconnect();
  }, [ref, fallback]);
  return height;
}

/** Where each core stands, how wide it is, and the depth range they span. */
function describe(holeIds, byHole) {
  const cores = holeIds.map((holeId) => {
    const { measurements, strip } = byHole[holeId] || {};
    const rows = strip?.rows?.length ? strip.rows : [];
    const log = measurements || [];
    return {
      holeId,
      strip,
      rows,
      measurements: log,
      loaded: measurements !== undefined && strip !== undefined,
      diameter: rows.length ? stripDiameter(strip) : FALLBACK_DIAMETER_M,
      photos: rows.length ? [strip.depth_min_m, strip.depth_max_m] : null,
      logged: log.length ? [log[0].depth_from_m, log[log.length - 1].depth_to_m] : null,
    };
  });
  const widest = Math.max(FALLBACK_DIAMETER_M, ...cores.map((core) => core.diameter));
  const spacing = widest * 4.5;
  cores.forEach((core, i) => {
    core.x = (i - (cores.length - 1) / 2) * spacing;
  });
  const ranges = cores.flatMap((core) => [core.photos, core.logged]).filter(Boolean);
  return {
    cores,
    widest,
    ready: cores.length > 0 && cores.every((core) => core.loaded),
    top: ranges.length ? Math.min(...ranges.map((r) => r[0])) : 0,
    bottom: ranges.length ? Math.max(...ranges.map((r) => r[1])) : 1,
  };
}

/** The core's real diameter, read off its own photo strip: the strip is
 * core_width_px across the core, and each tray row says how many metres its
 * pixels cover - so the cylinder is as wide as the rock in the photo. */
function stripDiameter(strip) {
  const metresPerPx = strip.rows
    .map((row) => (row.depth_to_m - row.depth_from_m) / Math.max(1, row.y_to_px - row.y_from_px))
    .sort((a, b) => a - b);
  return strip.core_width_px * metresPerPx[Math.floor(metresPerPx.length / 2)];
}

function photoStatus(core) {
  const { strip } = core;
  if (strip === undefined) return "loading…";
  if (strip.building) return "building core photo from NVCL (~1 min)…";
  if (strip.unavailable) return "no core photo - mineral colours only";
  return `core photo ${strip.depth_min_m.toFixed(0)}–${strip.depth_max_m.toFixed(0)} m`;
}

/** Tray rows of every core that overlap [from, to], with the sheet each sits on. */
function rowsIn(cores, [from, to]) {
  const wanted = [];
  for (const core of cores) {
    for (const row of core.rows) {
      if (row.depth_to_m < from || row.depth_from_m > to) continue;
      const sheet = core.strip.sheets.find((s) => s.sheet_index === row.sheet_index);
      if (!sheet) continue;
      const url = MEDIA_BASE + sheet.photo_url;
      wanted.push({ key: `${url}#${row.y_from_px}-${row.y_to_px}`, url, row, holeId: core.holeId });
    }
  }
  return wanted;
}

// Decoded sheet images, newest last (Map keeps insertion order) - shared by
// every viewer instance so re-opening a hole doesn't refetch.
const sheetCache = new Map();

function loadSheet(url) {
  let entry = sheetCache.get(url);
  if (entry) sheetCache.delete(url);
  else entry = loadImage(url);
  sheetCache.set(url, entry);
  entry.catch(() => sheetCache.delete(url));
  while (sheetCache.size > MAX_SHEETS) sheetCache.delete(sheetCache.keys().next().value);
  return entry;
}

/** One texture per tray row in view, cropped out of its (tall) sheet image. */
function useRowTextures(wanted) {
  const cache = useRef(new Map()); // key -> canvas
  const pending = useRef(new Set());
  const [version, setVersion] = useState(0);

  useEffect(() => {
    for (const { key, url, row } of wanted) {
      if (cache.current.has(key) || pending.current.has(key)) continue;
      pending.current.add(key);
      loadSheet(url)
        .then((image) => {
          cache.current.set(key, cropRow(image, row));
          while (cache.current.size > MAX_ROW_TEXTURES) cache.current.delete(cache.current.keys().next().value);
          setVersion((v) => v + 1);
        })
        .catch(() => {})
        .finally(() => pending.current.delete(key));
    }
  }, [wanted]);

  return { get: (key) => cache.current.get(key), version };
}

function cropRow(image, row) {
  const height = Math.max(1, Math.round(row.y_to_px - row.y_from_px));
  const scale = Math.min(1, MAX_TEXTURE_DIM / height);
  const canvas = document.createElement("canvas");
  canvas.width = image.naturalWidth;
  canvas.height = Math.max(1, Math.round(height * scale));
  canvas.getContext("2d").drawImage(image, 0, row.y_from_px, image.naturalWidth, height, 0, 0, canvas.width, canvas.height);
  return canvas;
}

function intervalColour(m) {
  if (!m.mineral_1 || m.confidence < CONFIDENCE_THRESHOLD) return toRgb(LOW_CONFIDENCE_COLOUR);
  return toRgb(mineralColour(m.mineral_1));
}

function intervalLabel(m) {
  const call = m.mineral_1 || "no mineral called";
  const sure = m.confidence < CONFIDENCE_THRESHOLD ? `uncertain (confidence ${m.confidence.toFixed(2)})` : `confidence ${m.confidence.toFixed(2)}`;
  return [call, sure, m.why, m.is_anomaly ? "flagged unusual by the anomaly model" : ""].filter(Boolean).join(" · ");
}

function buildLayers(scene, [from, to], depth, visible, labelDropM, photosOn, textures, setHover) {
  const onHover = (info) => setHover(info.object ? info : null);
  const along = (x, a, b, radius) => segmentTransform([x, 0, -a], [x, 0, -b], radius);
  const photoLayers = [];
  const plain = []; // cores without a photo (or still loading) in mineral colours / grey
  const band = []; // the mineral log beside each core
  const flags = [];
  const labels = [];

  for (const [i, core] of scene.cores.entries()) {
    const r = core.diameter / 2;
    const inView = core.measurements.filter((m) => m.depth_to_m >= from && m.depth_from_m <= to);

    if (photosOn && core.rows.length) {
      for (const { key, row } of rowsIn([core], [from, to])) {
        const segment = {
          transform: along(core.x, row.depth_from_m, row.depth_to_m, r),
          holeId: core.holeId, from: row.depth_from_m, to: row.depth_to_m,
          label: "NVCL tray photo (this tray row)",
        };
        const texture = textures.get(key);
        if (!texture) {
          plain.push({ ...segment, colour: GREY, label: "loading photo…" });
          continue;
        }
        photoLayers.push(new SimpleMeshLayer({
          id: `photo-${key}`,
          data: [segment],
          mesh: PHOTO_CORE_MESH,
          texture,
          material: PHOTO_MATERIAL,
          coordinateSystem: COORDINATE_SYSTEM.CARTESIAN,
          getPosition: ORIGIN,
          getTransformMatrix: TRANSFORM,
          getColor: [255, 255, 255],
          pickable: true,
          onHover,
        }));
      }
    } else {
      for (const m of inView) {
        plain.push({
          transform: along(core.x, m.depth_from_m, m.depth_to_m, r),
          colour: intervalColour(m), holeId: core.holeId, from: m.depth_from_m, to: m.depth_to_m,
          label: `${intervalLabel(m)}${core.rows.length ? "" : " · no core photo for this hole"}`,
        });
      }
    }

    for (const m of inView) {
      band.push({
        transform: along(core.x + r * 2.2, m.depth_from_m, m.depth_to_m, r * 0.35),
        colour: intervalColour(m), holeId: core.holeId, from: m.depth_from_m, to: m.depth_to_m,
        label: intervalLabel(m),
      });
      if (m.is_anomaly) {
        flags.push({
          position: [core.x + r * 3.2, 0, -(m.depth_from_m + m.depth_to_m) / 2],
          holeId: core.holeId, from: m.depth_from_m, to: m.depth_to_m, label: intervalLabel(m),
        });
      }
    }
    // the hole's name, riding just under the header (however tall it wraps)
    labels.push({ position: [core.x, 0, -(depth - visible / 2 + labelDropM)], text: core.holeId, colour: toRgb(HOLE_COLOURS[i]), anchor: "middle" });
  }

  // depth ruler left of the cores, ~8 ticks in view
  const step = niceStep(visible / 8);
  const rulerX = scene.cores[0].x - scene.widest * 1.6;
  const ticks = [];
  for (let d = Math.ceil(from / step) * step; d <= to; d += step) {
    ticks.push({ from: [rulerX, 0, -d], to: [rulerX + scene.widest * 0.5, 0, -d], text: `${formatDepth(d, step)} m` });
  }
  const lastX = scene.cores[scene.cores.length - 1].x + scene.widest * 2;

  return [
    new SimpleMeshLayer({
      id: "plain-cores",
      data: plain,
      mesh: CORE_MESH,
      coordinateSystem: COORDINATE_SYSTEM.CARTESIAN,
      getPosition: ORIGIN,
      getTransformMatrix: TRANSFORM,
      getColor: (d) => d.colour,
      pickable: true,
      onHover,
    }),
    ...photoLayers,
    new SimpleMeshLayer({
      id: "mineral-band",
      data: band,
      mesh: CORE_MESH,
      coordinateSystem: COORDINATE_SYSTEM.CARTESIAN,
      getPosition: ORIGIN,
      getTransformMatrix: TRANSFORM,
      getColor: (d) => d.colour,
      pickable: true,
      onHover,
    }),
    new ScatterplotLayer({
      id: "unusual",
      data: flags,
      coordinateSystem: COORDINATE_SYSTEM.CARTESIAN,
      getPosition: (d) => d.position,
      getFillColor: toRgb(ANOMALY_COLOUR),
      radiusUnits: "pixels",
      getRadius: 4,
      billboard: true,
      pickable: true,
      onHover,
    }),
    new LineLayer({
      id: "ruler",
      data: [...ticks, { from: [rulerX + scene.widest * 0.5, 0, -depth], to: [lastX, 0, -depth] }],
      coordinateSystem: COORDINATE_SYSTEM.CARTESIAN,
      getSourcePosition: (d) => d.from,
      getTargetPosition: (d) => d.to,
      getColor: (d) => (d.text ? [163, 179, 191, 200] : [79, 209, 197, 110]),
      getWidth: 1,
      widthUnits: "pixels",
    }),
    new TextLayer({
      id: "ruler-labels",
      data: [...ticks.map((t) => ({ position: t.from, text: t.text, colour: [200, 214, 224], anchor: "end" })), ...labels],
      coordinateSystem: COORDINATE_SYSTEM.CARTESIAN,
      getPosition: (d) => d.position,
      getText: (d) => d.text,
      getColor: (d) => d.colour,
      getSize: 12,
      sizeUnits: "pixels",
      getTextAnchor: (d) => d.anchor,
      getAlignmentBaseline: "center",
      fontFamily: "ui-monospace, Menlo, monospace",
      billboard: true,
    }),
  ];
}

/** Every chosen hole's whole mineral log, top to bottom, with the part in
 * view outlined - click or drag anywhere on it to jump there. */
function DepthNavigator({ scene, depth, visible, onJump }) {
  const span = scene.bottom - scene.top || 1;
  const y = (d) => ((d - scene.top) / span) * 1000;
  const jumpTo = (event) => {
    const box = event.currentTarget.getBoundingClientRect();
    onJump(scene.top + ((event.clientY - box.top) / box.height) * span);
  };
  const column = 14;
  const width = scene.cores.length * (column + 6);

  return (
    <div className="core3d-nav">
      <span className="mono hint">{scene.top.toFixed(0)} m</span>
      <svg
        viewBox={`0 0 ${width} 1000`}
        preserveAspectRatio="none"
        onPointerDown={(event) => {
          event.currentTarget.setPointerCapture(event.pointerId);
          jumpTo(event);
        }}
        onPointerMove={(event) => event.buttons && jumpTo(event)}
        role="slider"
        aria-label="Depth"
        aria-valuemin={Math.round(scene.top)}
        aria-valuemax={Math.round(scene.bottom)}
        aria-valuenow={depth === null ? undefined : Math.round(depth)}
      >
        {scene.cores.map((core, i) => (
          <g key={core.holeId} transform={`translate(${i * (column + 6)} 0)`}>
            <rect width={column} height="1000" fill="rgba(255,255,255,0.06)" />
            {core.measurements.map((m) => (
              <rect key={m.id ?? m.depth_from_m} y={y(m.depth_from_m)} width={column}
                    height={Math.max(y(m.depth_to_m) - y(m.depth_from_m), 0.5)}
                    fill={`rgb(${intervalColour(m).join(",")})`} />
            ))}
            {core.photos && (
              <rect x={column - 3} y={y(core.photos[0])} width="3" height={y(core.photos[1]) - y(core.photos[0])} fill={HOLE_COLOURS[i]} />
            )}
          </g>
        ))}
        {depth !== null && (
          <rect x="0" y={y(depth - visible / 2)} width={width} height={Math.max((visible / span) * 1000, 3)}
                fill="rgba(255,255,255,0.18)" stroke="#fff" strokeWidth="2" vectorEffect="non-scaling-stroke" />
        )}
      </svg>
      <span className="mono hint">{scene.bottom.toFixed(0)} m</span>
    </div>
  );
}

// 1-2-5 steps only: every tick is exact at the decimals formatDepth shows
// (a 0.25 m step printed to 0.1 m would label 0.25 m as "0.3 m")
function niceStep(raw) {
  for (const step of [0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10, 20, 50, 100]) if (step >= raw) return step;
  return 200;
}

const formatDepth = (d, step) => d.toFixed(step < 0.1 ? 2 : step < 1 ? 1 : 0);

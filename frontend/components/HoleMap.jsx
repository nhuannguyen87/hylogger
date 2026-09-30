"use client";

// MapLibre, used directly rather than through a React wrapper. It is about 60
// lines and you can see exactly what it does, which matters more than brevity
// while you're learning.
//
// The pattern: create the map once, then push new data into a GeoJSON source
// whenever `holes` changes. Never recreate the map on every render.
//
// The "3D core" toggle adds a deck.gl overlay on top of that same map (ported
// from database5553/wa-drillhole-map/public/index.html): tilting the camera and
// drawing each hole's collar-to-toe trajectory as a coloured, exaggerated line
// is what makes real dip/azimuth read as a leaning drill core instead of a dot.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { MapLibreOverlay, PathLayer } from "deck.gl";
import {
  LOW_CONFIDENCE_COLOUR, MAP_START, MAP_STYLE, MAP_3D_DEFAULT_EXAGGERATION, MAP_3D_DEFAULT_CORE_WIDTH_M,
  mineralColour, toRgb,
} from "@/config";
import { desurveyPoint, desurveyStraightLine } from "@/lib/desurvey";
import CoreStripPanel from "./CoreStripPanel";

const SOURCE = "holes";
const CORE_COLOUR = [79, 209, 197]; // --accent
const CONFIDENTIAL_COLOUR = [255, 138, 122]; // matches .badge.confidential
// Once mineral logs are on, the stretches of a core with no log (above the
// first logged metre, between runs, below the last, or a hole with no log at
// all) - a see-through grey, so it reads as absent and can't pass for a
// pale mineral like QUARTZ, and a colour always means a real call there.
const NOT_LOGGED_COLOUR = [150, 158, 166, 120];

export default function HoleMap({
  holes = [], selectedId, onSelect, onHover, selectedIdB, flyToSelection = true, flyToId,
  // Every hole's mineral log (api.getMineralLogs) - colours each 3D core by
  // its mineral runs. Without it, every core is one plain colour.
  mineralLogs = null,
  // Open with the 3D cores already standing (camera tilted), not the flat dots.
  initial3d = false,
  // The flat core-photo panel under the map (Explore). The 3D page shows the
  // photos on its own cores instead.
  coreStripPanel = true,
  // Holes kept bright while the rest dim (the 3D page's open cores); without
  // it, the clicked hole (flyToId) is the one in focus.
  focusIds = null,
}) {
  const containerRef = useRef(null);
  const mapRef = useRef(null);
  const overlayRef = useRef(null);
  const readyRef = useRef(false);
  // latest props for the map's one-off "load" handler, which would otherwise
  // only ever see the first render's (empty) holes
  const dataRef = useRef({ holes, mineralLogs });
  dataRef.current = { holes, mineralLogs };
  const highlightedRef = useRef([]);

  // One click can hit a collar circle (MapLibre) and a core (deck.gl) - the
  // same hole's, or a neighbour's a few metres away - and each reports it. So
  // a click selects exactly one hole, whichever reports first; otherwise a
  // caller that toggles (the 3D page) would undo its own click, or pick two.
  // Through a ref, too: the map's click handler is registered once, at load.
  const onSelectRef = useRef(onSelect);
  onSelectRef.current = onSelect;
  const lastSelectAt = useRef(0);
  const select = useCallback((holeId) => {
    const now = performance.now();
    if (now - lastSelectAt.current < 300) return;
    lastSelectAt.current = now;
    onSelectRef.current?.(holeId);
  }, []);

  const [show3d, setShow3d] = useState(initial3d);
  const [exaggeration, setExaggeration] = useState(MAP_3D_DEFAULT_EXAGGERATION);
  const [coreWidth, setCoreWidth] = useState(MAP_3D_DEFAULT_CORE_WIDTH_M);
  const [tooltip, setTooltip] = useState(null);

  // 1. create the map, once
  useEffect(() => {
    const map = new maplibregl.Map({
      container: containerRef.current,
      style: MAP_STYLE,
      center: [MAP_START.longitude, MAP_START.latitude],
      zoom: MAP_START.zoom,
      // effect 5 below only tilts on a *change* of show3d, after load
      pitch: initial3d ? 55 : 0,
    });
    map.addControl(new maplibregl.NavigationControl(), "top-right");
    map.addControl(new maplibregl.ScaleControl({ unit: "metric" }));

    // interleaved:false draws deck.gl on its own canvas above the map, so a
    // core leaning below the surface (negative altitude) stays visible once
    // the map is pitched, instead of being clipped by the basemap's own tiles.
    const overlay = new MapLibreOverlay({ interleaved: false, layers: [] });
    map.addControl(overlay);
    overlayRef.current = overlay;

    map.on("load", () => {
      map.addSource(SOURCE, { type: "geojson", data: emptyCollection() });

      map.addLayer({
        id: "holes-circles",
        type: "circle",
        source: SOURCE,
        paint: {
          // grow the dots as you zoom in; holes with no mineral log stay
          // small and faint so the logged (coloured) ones stand out
          "circle-radius": [
            "interpolate", ["linear"], ["zoom"],
            3, ["case", ["get", "logged"], 4, 2],
            8, ["case", ["get", "logged"], 8, 4],
            12, ["case", ["get", "logged"], 12, 7],
          ],
          "circle-opacity": ["case", ["get", "logged"], 1, 0.55],
          // each dot in its hole's dominant mineral colour, so the map reads
          // before anything is clicked. selectedB (--accent-b) only gets set
          // when a caller passes selectedIdB (Compare's two-hole picker)
          "circle-color": [
            "case",
            ["boolean", ["feature-state", "selectedA"], false], "#4fd1c5",
            ["boolean", ["feature-state", "selectedB"], false], "#b98cff",
            ["get", "colour"],
          ],
          "circle-stroke-width": [
            "case",
            ["boolean", ["feature-state", "selectedA"], false], 2,
            ["boolean", ["feature-state", "selectedB"], false], 2,
            ["get", "has_full_spectrum"], 2,
            0.5,
          ],
          // full VSWIR/TIR spectrum available (5 holes, see HoleDetail's badge) - a
          // ring in the "second hole" accent, distinct from selection/confidential
          "circle-stroke-color": ["case", ["get", "has_full_spectrum"], "#b98cff", "#0e1418"],
        },
      });

      // hover previews on the map (cheap: just a highlight, no data fetch);
      // click is what actually opens the detail panel, which does fetch data
      map.on("mousemove", "holes-circles", (event) => {
        const feature = event.features?.[0];
        if (feature) onHover?.(feature.properties.hole_id);
      });
      map.on("click", "holes-circles", (event) => {
        const feature = event.features?.[0];
        if (feature) select(feature.properties.hole_id);
      });
      map.on("mouseenter", "holes-circles", () => {
        map.getCanvas().style.cursor = "pointer";
      });
      map.on("mouseleave", "holes-circles", () => {
        map.getCanvas().style.cursor = "";
      });

      readyRef.current = true;
      mapRef.current = map;
      pushData(map, dataRef.current.holes, dataRef.current.mineralLogs);
    });

    mapRef.current = map;
    // the 3D page halves the map's width when a core opens beside it -
    // MapLibre only tracks window resizes on its own
    const resize = new ResizeObserver(() => map.resize());
    resize.observe(containerRef.current);
    return () => {
      resize.disconnect();
      map.remove();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 2. push new holes into the map whenever the list or the logs change
  useEffect(() => {
    if (mapRef.current && readyRef.current) pushData(mapRef.current, holes, mineralLogs);
  }, [holes, mineralLogs]);

  // 3. highlight the hole(s) - tracks hover, so touch only the holes whose
  // state actually changes, not all ~2,000 on every mouse move
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !readyRef.current) return;
    for (const id of highlightedRef.current) {
      map.setFeatureState({ source: SOURCE, id }, { selectedA: false, selectedB: false });
    }
    if (selectedId) map.setFeatureState({ source: SOURCE, id: selectedId }, { selectedA: true });
    if (selectedIdB) map.setFeatureState({ source: SOURCE, id: selectedIdB }, { selectedB: true });
    highlightedRef.current = [selectedId, selectedIdB].filter(Boolean);
  }, [selectedId, selectedIdB, holes]);

  // 3b. fly to a hole - deliberately separate from highlighting above, and
  // keyed on flyToId (defaults to selectedId) rather than always hover:
  // Explore passes the clicked detailId here, so sweeping the mouse down the
  // list previews the highlight without also yanking the camera around.
  const effectiveFlyToId = flyToId ?? selectedId;
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !readyRef.current || !flyToSelection) return;
    const target = holes.find((hole) => hole.hole_id === effectiveFlyToId);
    if (target) {
      map.easeTo({ center: [target.longitude, target.latitude], duration: 600 });
    }
  }, [effectiveFlyToId, holes, flyToSelection]);

  // 5. tilt the camera when 3D core turns on - at pitch 0 the exaggerated
  // altitude is invisible (you're looking straight down), which is exactly
  // the flat plan view the map opens with.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !readyRef.current) return;
    map.easeTo({ pitch: show3d ? 55 : 0, duration: 600 });
  }, [show3d]);

  // 6. cut every core into its mineral runs - thousands of segments once
  // mineral logs are on, so only when the holes, logs or exaggeration
  // change, not on every hover like the layers below.
  const cores = useMemo(() => buildCores(holes, mineralLogs, exaggeration), [holes, mineralLogs, exaggeration]);
  const legend = useMemo(() => mineralLegend(mineralLogs), [mineralLogs]);

  // 7. rebuild the deck.gl core layers whenever the cores, controls or the
  // committed hole change. Dimming follows the clicked hole (effectiveFlyToId),
  // not hover: the map is dense enough that the cursor is nearly always over
  // some core, and re-dimming everything on each mouse move made the whole
  // scene flicker.
  useEffect(() => {
    const overlay = overlayRef.current;
    if (!overlay) return;
    overlay.setProps({
      layers: show3d
        ? buildCoreLayers(cores, { coreWidth, focus: focusIds ?? (effectiveFlyToId ? [effectiveFlyToId] : []), onSelect: select, onHover, onTooltip: setTooltip })
        : [],
    });
  }, [cores, show3d, coreWidth, effectiveFlyToId, focusIds, select, onHover]);

  return (
    <>
      <div ref={containerRef} style={{ position: "absolute", inset: 0 }} />

      <div className="controls-3d">
        <label className="checkbox">
          <input
            type="checkbox"
            checked={show3d}
            onChange={(event) => setShow3d(event.target.checked)}
          />
          3D core (real dip/azimuth)
        </label>

        {show3d && (
          <>
            <p className="section-title" style={{ marginTop: 14 }}>
              Vertical exaggeration ×{exaggeration}
            </p>
            <input
              type="range"
              min="1"
              max="50"
              value={exaggeration}
              onChange={(event) => setExaggeration(Number(event.target.value))}
            />

            <p className="section-title" style={{ marginTop: 10 }}>
              Core width {coreWidth} m
            </p>
            <input
              type="range"
              min="2"
              max="80"
              value={coreWidth}
              onChange={(event) => setCoreWidth(Number(event.target.value))}
            />

            <p className="hint" style={{ marginTop: 8 }}>
              Right-drag (or Ctrl+drag) to tilt &amp; rotate. Holes are spread
              over 100s of km, so the lean mostly reads once you zoom into one
              hole or a tight cluster.
            </p>

            {legend.length > 0 && (
              <>
                <p className="section-title" style={{ marginTop: 14 }}>Mineral group</p>
                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", columnGap: 8, fontSize: 11 }}>
                  {legend.map((group) => (
                    <div className="legend-item" key={group}>
                      <span className="legend-swatch" style={{ background: mineralColour(group) }} />
                      {group || "no call"}
                    </div>
                  ))}
                  <div className="legend-item">
                    <span className="legend-swatch" style={{ background: LOW_CONFIDENCE_COLOUR }} />
                    uncertain
                  </div>
                  <div className="legend-item">
                    <span className="legend-swatch" style={{ background: `rgba(${NOT_LOGGED_COLOUR.slice(0, 3)}, 0.5)` }} />
                    not logged
                  </div>
                </div>
              </>
            )}
          </>
        )}
      </div>

      {show3d && tooltip && (
        // .tooltip is position: fixed for page coordinates; deck.gl hands
        // back canvas coordinates, and the canvas fills .map-area
        <div className="tooltip" style={{ position: "absolute", left: tooltip.x + 14, top: tooltip.y + 14 }}>
          {tooltip.segment.hole.hole_id} · {tooltip.segment.from.toFixed(0)}–{tooltip.segment.to.toFixed(0)} m
          {tooltip.segment.label && <><br />{tooltip.segment.label}</>}
        </div>
      )}

      {/* the committed hole (Explore's click), not every hover: the panel fetches */}
      {show3d && coreStripPanel && <CoreStripPanel holeId={effectiveFlyToId} />}
    </>
  );
}

/** Each hole with a valid straight-line trajectory as a collar->toe `line`,
 * and that line cut into `segments`: one per mineral run
 * (views.mineral_logs), plus neutral ones wherever the hole isn't logged, so
 * the whole core still draws. Without mineralLogs (Compare, or before they
 * load) each hole is a single segment in the old plain colour. */
function buildCores(holes, mineralLogs, exaggeration) {
  const lines = [];
  const segments = [];
  for (const hole of holes) {
    const line = desurveyStraightLine(hole, exaggeration);
    if (!line) continue;
    lines.push({ hole, ...line });

    // With mineral colours on, a salmon "confidential" core would read as a
    // pink mineral - so it's faint like any unlogged hole and says so in words.
    const plain = mineralLogs ? NOT_LOGGED_COLOUR : hole.confidential ? CONFIDENTIAL_COLOUR : CORE_COLOUR;
    const unlogged = mineralLogs ? (hole.confidential ? "not logged · confidential" : "not logged") : null;
    const add = (from, to, colour, label) => segments.push({
      hole, from, to, colour, label, logged: colour !== NOT_LOGGED_COLOUR,
      path: [desurveyPoint(hole, from, exaggeration), desurveyPoint(hole, to, exaggeration)],
    });

    const length = hole.drawn_length_m;
    let reached = 0;
    for (const [from, to, code] of mineralLogs?.holes?.[hole.hole_id] ?? []) {
      const top = Math.max(from, reached);
      const bottom = Math.min(to, length);
      if (bottom <= top) continue;
      if (top > reached) add(reached, top, plain, unlogged);
      if (code === null) {
        add(top, bottom, toRgb(LOW_CONFIDENCE_COLOUR), "uncertain reading");
      } else {
        const group = mineralLogs.groups[code];
        add(top, bottom, toRgb(mineralColour(group)), group || "no mineral called");
      }
      reached = bottom;
    }
    if (reached < length) add(reached, length, plain, unlogged);
  }
  return { lines, segments };
}

/** The mineral groups on the map, most metres first, for the legend. */
function mineralLegend(mineralLogs) {
  if (!mineralLogs) return [];
  const metres = {};
  for (const runs of Object.values(mineralLogs.holes)) {
    for (const [from, to, code] of runs) {
      if (code === null) continue;
      const group = mineralLogs.groups[code];
      metres[group] = (metres[group] || 0) + (to - from);
    }
  }
  return Object.keys(metres).sort((a, b) => metres[b] - metres[a]);
}

function buildCoreLayers({ lines, segments }, { coreWidth, focus, onSelect, onHover, onTooltip }) {
  const pathLayer = (id, data, widthScale, widthMinPixels) => new PathLayer({
    id,
    data,
    getPath: (d) => d.path,
    getColor: (d) => {
      const [r, g, b, alpha = 255] = d.colour;
      const dimmed = focus.length > 0 && !focus.includes(d.hole.hole_id);
      return [r, g, b, dimmed ? Math.min(alpha, 70) : alpha];
    },
    getWidth: coreWidth * widthScale,
    widthUnits: "meters",
    widthMinPixels,
    billboard: true,
    capRounded: true,
    jointRounded: true,
    pickable: true,
    onHover: (info) => {
      if (info.object) onHover?.(info.object.hole.hole_id);
      onTooltip(info.object ? { x: info.x, y: info.y, segment: info.object } : null);
    },
    onClick: (info) => info.object && onSelect(info.object.hole.hole_id),
    updateTriggers: { getColor: [focus.join()] },
  });
  return [
    // a faint flat shadow at collar elevation, so dip direction reads even
    // before you tilt the camera
    new PathLayer({
      id: "map-core-shadow",
      data: lines,
      getPath: (d) => [d.collar, [d.toe[0], d.toe[1], d.collar[2]]],
      getColor: [255, 255, 255, 70],
      getWidth: 1,
      widthUnits: "pixels",
    }),
    // unlogged stretches thin and underneath; real mineral calls wide on top,
    // at least 5 px so the colours read even at the whole-of-WA zoom
    pathLayer("map-core-unlogged", segments.filter((s) => !s.logged), 0.4, 1.5),
    pathLayer("map-core", segments.filter((s) => s.logged), 1, 5),
  ];
}

/** The mineral group with the most logged metres in one hole, or null. */
function dominantGroup(runs, groups) {
  const metres = {};
  for (const [from, to, code] of runs ?? []) {
    if (code !== null) metres[code] = (metres[code] || 0) + (to - from);
  }
  const best = Object.keys(metres).sort((a, b) => metres[b] - metres[a])[0];
  return best === undefined ? null : groups[best];
}

function pushData(map, holes, mineralLogs) {
  const source = map.getSource(SOURCE);
  if (!source) return;
  source.setData({
    type: "FeatureCollection",
    features: holes.map((hole) => {
      const group = mineralLogs && dominantGroup(mineralLogs.holes[hole.hole_id], mineralLogs.groups);
      return {
        type: "Feature",
        id: hole.hole_id, // needed for setFeatureState
        properties: {
          hole_id: hole.hole_id,
          hole_name: hole.hole_name,
          has_full_spectrum: !!hole.has_full_spectrum,
          // no logs passed at all (Compare): every dot full size, plain grey
          logged: !mineralLogs || !!group,
          colour: group ? mineralColour(group) : "#8c9aa5",
        },
        geometry: { type: "Point", coordinates: [hole.longitude, hole.latitude] },
      };
    }),
  });
}

const emptyCollection = () => ({ type: "FeatureCollection", features: [] });

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

import { useEffect, useRef, useState } from "react";
import maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { MapLibreOverlay, PathLayer } from "deck.gl";
import {
  MAP_START, MAP_STYLE, MAP_3D_DEFAULT_EXAGGERATION, MAP_3D_DEFAULT_CORE_WIDTH_M,
} from "@/config";
import { desurveyStraightLine } from "@/lib/desurvey";
import CoreStripPanel from "./CoreStripPanel";

const SOURCE = "holes";
const CORE_COLOUR = [79, 209, 197]; // --accent
const CONFIDENTIAL_COLOUR = [255, 138, 122]; // matches .badge.confidential

export default function HoleMap({
  holes = [], selectedId, onSelect, onHover, selectedIdB, flyToSelection = true, flyToId,
}) {
  const containerRef = useRef(null);
  const mapRef = useRef(null);
  const overlayRef = useRef(null);
  const readyRef = useRef(false);

  const [basemap, setBasemap] = useState("satellite");
  const [show3d, setShow3d] = useState(false);
  const [exaggeration, setExaggeration] = useState(MAP_3D_DEFAULT_EXAGGERATION);
  const [coreWidth, setCoreWidth] = useState(MAP_3D_DEFAULT_CORE_WIDTH_M);

  // 1. create the map, once
  useEffect(() => {
    const map = new maplibregl.Map({
      container: containerRef.current,
      style: MAP_STYLE,
      center: [MAP_START.longitude, MAP_START.latitude],
      zoom: MAP_START.zoom,
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
          // grow the dots as you zoom in
          "circle-radius": ["interpolate", ["linear"], ["zoom"], 3, 3, 8, 7, 12, 11],
          // selectedB (--accent-b) only gets set when a caller passes
          // selectedIdB, e.g. Compare's "pick two holes on the map" picker
          "circle-color": [
            "case",
            ["boolean", ["feature-state", "selectedA"], false], "#4fd1c5",
            ["boolean", ["feature-state", "selectedB"], false], "#b98cff",
            "#8c9aa5",
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
        if (feature) onSelect?.(feature.properties.hole_id);
      });
      map.on("mouseenter", "holes-circles", () => {
        map.getCanvas().style.cursor = "pointer";
      });
      map.on("mouseleave", "holes-circles", () => {
        map.getCanvas().style.cursor = "";
      });

      readyRef.current = true;
      mapRef.current = map;
      pushData(map, holes);
    });

    mapRef.current = map;
    return () => map.remove();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 2. push new holes into the map whenever the list changes
  useEffect(() => {
    if (mapRef.current && readyRef.current) pushData(mapRef.current, holes);
  }, [holes]);

  // 3. highlight the hole(s) - cheap, so this tracks hover (selectedId)
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !readyRef.current) return;

    holes.forEach((hole) => {
      map.setFeatureState(
        { source: SOURCE, id: hole.hole_id },
        { selectedA: hole.hole_id === selectedId, selectedB: hole.hole_id === selectedIdB }
      );
    });
  }, [selectedId, selectedIdB, holes]);

  // 3b. fly to a hole - deliberately separate from highlighting above, and
  // keyed on flyToId (defaults to selectedId) rather than always hover:
  // Explore passes the clicked detailId here, so sweeping the mouse down the
  // list previews the highlight without also yanking the camera around.
  // Compare's two-hole picker moves selectedId on every hover of either slot,
  // so it opts out of this entirely via flyToSelection.
  const effectiveFlyToId = flyToId ?? selectedId;
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !readyRef.current || !flyToSelection) return;
    const target = holes.find((hole) => hole.hole_id === effectiveFlyToId);
    if (target) {
      map.easeTo({ center: [target.longitude, target.latitude], duration: 600 });
    }
  }, [effectiveFlyToId, holes, flyToSelection]);

  // 4. switch basemap by toggling raster-layer visibility - no style reload,
  // so the "holes" source/layer above never has to be re-added.
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    const apply = () => {
      map.setLayoutProperty("satellite", "visibility", basemap === "satellite" ? "visible" : "none");
      map.setLayoutProperty("light", "visibility", basemap === "light" ? "visible" : "none");
    };
    if (map.isStyleLoaded()) apply();
    else map.once("load", apply);
  }, [basemap]);

  // 5. tilt the camera when 3D core turns on - at pitch 0 the exaggerated
  // altitude is invisible (you're looking straight down), which is exactly
  // the flat plan view the map opens with.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !readyRef.current) return;
    map.easeTo({ pitch: show3d ? 55 : 0, duration: 600 });
  }, [show3d]);

  // 6. rebuild the deck.gl core layers whenever the holes, controls or
  // selection change. Cheap: no re-desurvey beyond a handful of trig calls
  // per hole, so there's no need to cache this like the multi-station version
  // in wa-drillhole-map's lib/desurvey.js does for its heavier calculation.
  useEffect(() => {
    const overlay = overlayRef.current;
    if (!overlay) return;
    overlay.setProps({
      layers: show3d ? buildCoreLayers(holes, { exaggeration, coreWidth, selectedId, onSelect, onHover }) : [],
    });
  }, [holes, show3d, exaggeration, coreWidth, selectedId, onSelect, onHover]);

  return (
    <>
      <div ref={containerRef} style={{ position: "absolute", inset: 0 }} />

      <div className="controls-3d">
        <p className="section-title">Basemap</p>
        <div className="seg">
          <button
            type="button"
            className={basemap === "satellite" ? "active" : ""}
            onClick={() => setBasemap("satellite")}
          >
            Satellite
          </button>
          <button
            type="button"
            className={basemap === "light" ? "active" : ""}
            onClick={() => setBasemap("light")}
          >
            Streets
          </button>
        </div>

        <label className="checkbox" style={{ marginTop: 14 }}>
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
          </>
        )}
      </div>

      {show3d && <CoreStripPanel holeId={selectedId} />}
    </>
  );
}

/** Every hole with a valid straight-line trajectory, as a collar->toe core. */
function buildCoreLayers(holes, { exaggeration, coreWidth, selectedId, onSelect, onHover }) {
  const cores = holes
    .map((hole) => {
      const line = desurveyStraightLine(hole, exaggeration);
      return line && { hole, ...line };
    })
    .filter(Boolean);

  return [
    // a faint flat shadow at collar elevation, so dip direction reads even
    // before you tilt the camera
    new PathLayer({
      id: "map-core-shadow",
      data: cores,
      getPath: (d) => [d.collar, [d.toe[0], d.toe[1], d.collar[2]]],
      getColor: [255, 255, 255, 70],
      getWidth: 1,
      widthUnits: "pixels",
    }),
    new PathLayer({
      id: "map-core",
      data: cores,
      getPath: (d) => [d.collar, d.toe],
      getColor: (d) => {
        const [r, g, b] = d.hole.confidential ? CONFIDENTIAL_COLOUR : CORE_COLOUR;
        const dimmed = selectedId && d.hole.hole_id !== selectedId;
        return [r, g, b, dimmed ? 70 : 255];
      },
      getWidth: coreWidth,
      widthUnits: "meters",
      widthMinPixels: 2,
      billboard: true,
      capRounded: true,
      jointRounded: true,
      pickable: true,
      onHover: (info) => info.object && onHover?.(info.object.hole.hole_id),
      onClick: (info) => info.object && onSelect?.(info.object.hole.hole_id),
      updateTriggers: { getColor: [selectedId] },
    }),
  ];
}

function pushData(map, holes) {
  const source = map.getSource(SOURCE);
  if (!source) return;
  source.setData({
    type: "FeatureCollection",
    features: holes.map((hole) => ({
      type: "Feature",
      id: hole.hole_id, // needed for setFeatureState
      properties: {
        hole_id: hole.hole_id,
        hole_name: hole.hole_name,
        has_full_spectrum: !!hole.has_full_spectrum,
      },
      geometry: { type: "Point", coordinates: [hole.longitude, hole.latitude] },
    })),
  });
}

const emptyCollection = () => ({ type: "FeatureCollection", features: [] });

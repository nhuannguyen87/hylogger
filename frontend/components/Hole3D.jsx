"use client";

// The 3D view, drawn with deck.gl in an orbit camera (drag to rotate, scroll to zoom).
//
// Coordinates are plain metres, not longitude/latitude: we pick the middle of the
// selected holes as the origin, then place everything east/north/down from there.
// That keeps the maths obvious and avoids fighting a map projection underground.

import { useMemo, useState } from "react";
import DeckGL, { COORDINATE_SYSTEM, OrbitView, PathLayer, ScatterplotLayer } from "deck.gl";
import { ANOMALY_COLOUR, mineralColour, toRgb } from "@/config";

const METRES_PER_DEGREE_LAT = 110540;

export default function Hole3D({ traces = [], verticalExaggeration = 25, colourBy = "mineral" }) {
  const [hovered, setHovered] = useState(null);

  const { segments, collars, anomalies, view } = useMemo(
    () => buildScene(traces, verticalExaggeration, colourBy),
    [traces, verticalExaggeration, colourBy]
  );

  if (!traces.length) {
    return <p className="empty">Choose one or more holes to see them in 3D.</p>;
  }

  const layers = [
    new PathLayer({
      id: "hole-traces",
      data: segments,
      coordinateSystem: COORDINATE_SYSTEM.CARTESIAN,
      getPath: (segment) => segment.path,
      getColor: (segment) => segment.colour,
      getWidth: 3,
      widthUnits: "pixels",
      widthMinPixels: 3,
      pickable: true,
      onHover: (info) => setHovered(info.object ? info : null),
    }),
    new ScatterplotLayer({
      id: "collars",
      data: collars,
      coordinateSystem: COORDINATE_SYSTEM.CARTESIAN,
      getPosition: (collar) => collar.position,
      getFillColor: [79, 209, 197],
      radiusUnits: "pixels",
      getRadius: 5,
      pickable: true,
      onHover: (info) => setHovered(info.object ? info : null),
    }),
    new ScatterplotLayer({
      id: "anomaly-points",
      data: anomalies,
      coordinateSystem: COORDINATE_SYSTEM.CARTESIAN,
      getPosition: (point) => point.position,
      getFillColor: toRgb(ANOMALY_COLOUR),
      radiusUnits: "pixels",
      getRadius: 4,
      pickable: true,
      onHover: (info) => setHovered(info.object ? info : null),
    }),
  ];

  return (
    <>
      <DeckGL
        views={new OrbitView({ orbitAxis: "Z" })}
        initialViewState={view}
        controller={true}
        layers={layers}
        style={{ position: "absolute", inset: 0 }}
      />
      {hovered?.object && (
        <div className="tooltip" style={{ left: hovered.x + 14, top: hovered.y + 14 }}>
          {hovered.object.holeId}
          {hovered.object.depth != null && <> · {hovered.object.depth.toFixed(0)} m</>}
          {hovered.object.mineral && <><br />{hovered.object.mineral}</>}
        </div>
      )}
    </>
  );
}

function buildScene(traces, exaggeration, colourBy) {
  const empty = { segments: [], collars: [], anomalies: [], view: defaultView() };
  if (!traces.length) return empty;

  // origin = middle of the selected holes
  const originLat = average(traces.map((t) => t.latitude));
  const originLon = average(traces.map((t) => t.longitude));
  const metresPerDegreeLon =
    METRES_PER_DEGREE_LAT * Math.cos((originLat * Math.PI) / 180);

  const segments = [];
  const collars = [];
  const anomalies = [];
  let maxExtent = 200; // metres, horizontally
  let maxDown = 0; // metres, after exaggeration

  traces.forEach((trace) => {
    const collarX = (trace.longitude - originLon) * metresPerDegreeLon;
    const collarY = (trace.latitude - originLat) * METRES_PER_DEGREE_LAT;
    maxExtent = Math.max(maxExtent, Math.abs(collarX), Math.abs(collarY));

    collars.push({
      position: [collarX, collarY, 0],
      holeId: trace.hole_id,
      depth: 0,
    });

    const points = trace.points.map((point) => [
      collarX + point.east_m,
      collarY + point.north_m,
      -point.tvd_m * exaggeration, // negative = down
    ]);
    maxDown = Math.max(maxDown, ...trace.points.map((p) => p.tvd_m * exaggeration));

    for (let i = 0; i < points.length - 1; i += 1) {
      const point = trace.points[i];
      segments.push({
        path: [points[i], points[i + 1]],
        colour:
          colourBy === "anomaly"
            ? anomalyColour(point.anomaly_score)
            : toRgb(mineralColour(point.mineral)),
        holeId: trace.hole_id,
        depth: point.depth_m,
        mineral: point.mineral,
      });

      if (point.is_anomaly) {
        anomalies.push({
          position: points[i],
          holeId: trace.hole_id,
          depth: point.depth_m,
          mineral: point.mineral,
        });
      }
    }
  });

  // Frame the camera around everything we just built. Note that deck.gl treats a
  // new initialViewState as "reset the camera", so changing the selection or the
  // depth stretch re-centres the view. That's usually what you want.
  const halfSize = Math.max(maxExtent, maxDown / 2, 50);

  return {
    segments,
    collars,
    anomalies,
    view: {
      ...defaultView(),
      target: [0, 0, -maxDown / 2],
      zoom: Math.log2(420 / halfSize),
    },
  };
}

/** Cool grey when ordinary, amber when odd. */
function anomalyColour(score = 0) {
  const amber = toRgb(ANOMALY_COLOUR);
  const grey = [90, 102, 114];
  return grey.map((value, index) => Math.round(value + (amber[index] - value) * score));
}

const defaultView = () => ({
  target: [0, 0, 0],
  zoom: -2,
  rotationX: 35,
  rotationOrbit: 20,
  minZoom: -12,
  maxZoom: 12,
});

const average = (values) => values.reduce((total, value) => total + value, 0) / values.length;

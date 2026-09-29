"use client";

// The 3D view, drawn with deck.gl in an orbit camera (drag to rotate, scroll to zoom).
//
// Coordinates are plain metres, not longitude/latitude: we pick the middle of the
// selected holes as the origin, then place everything east/north/down from there.
// That keeps the maths obvious and avoids fighting a map projection underground.

import { useEffect, useMemo, useState } from "react";
import DeckGL, { COORDINATE_SYSTEM, OrbitView, ScatterplotLayer, SimpleMeshLayer } from "deck.gl";
import { CylinderGeometry } from "@luma.gl/engine";
import { getCoreStrip } from "@/lib/api";
import { ANOMALY_COLOUR, MEDIA_BASE, mineralColour, toRgb } from "@/config";

const METRES_PER_DEGREE_LAT = 110540;

// How far along each core_strip.py sheet to cut cylinder segments for
// texturing - matches the mineral-cylinder trace step (see Hole3D's own
// getTrace(holeId, stepM=5) call). PROTOTYPE SCOPE: only the first sheet
// per hole for now (see usePhotoCylinders) - confirm this looks right
// before the full loop across every sheet, which is many more textures.
const PHOTO_SEGMENT_STEP_M = 5;

function loadImage(url) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.crossOrigin = "anonymous";
    img.onload = () => resolve(img);
    img.onerror = reject;
    img.src = url;
  });
}

/** Same piecewise depth->pixel mapping as depth_lookup.py's DepthLookupEngine,
 * scoped to one sheet's own rows (already sorted by depth, contiguous). */
function depthToSheetPixel(rows, depthM) {
  let row = rows.find((r) => depthM >= r.depth_from_m && depthM < r.depth_to_m);
  if (!row) row = depthM < rows[0].depth_from_m ? rows[0] : rows[rows.length - 1];
  const span = row.depth_to_m - row.depth_from_m;
  const fraction = span > 0 ? (depthM - row.depth_from_m) / span : 0;
  return row.y_from_px + Math.min(Math.max(fraction, 0), 1) * (row.y_to_px - row.y_from_px);
}

function cropSheetTexture(image, widthPx, yFromPx, yToPx) {
  const height = Math.max(1, Math.round(yToPx - yFromPx));
  const canvas = document.createElement("canvas");
  canvas.width = widthPx;
  canvas.height = height;
  canvas.getContext("2d").drawImage(image, 0, yFromPx, widthPx, height, 0, 0, widthPx, height);
  return canvas;
}

/** For each hole with real core_strip.py output, crop its first photo sheet
 * into PHOTO_SEGMENT_STEP_M-deep textures ready to paint onto a cylinder.
 * Real image work (fetch + canvas crop), so this runs in an effect, not
 * useMemo - buildScene picks up the result once it's ready. */
function usePhotoCylinders(traces) {
  const [byHole, setByHole] = useState({});

  useEffect(() => {
    let cancelled = false;
    const holeIds = traces.map((t) => t.hole_id);

    Promise.all(
      holeIds.map(async (holeId) => {
        const coreStrip = await getCoreStrip(holeId);
        const sheet = coreStrip?.sheets?.[0]; // prototype scope: first sheet only
        if (!sheet) return [holeId, null];

        const image = await loadImage(MEDIA_BASE + sheet.photo_url).catch(() => null);
        if (!image) return [holeId, null];

        const rows = coreStrip.rows.filter((r) => r.sheet_index === sheet.sheet_index);
        const segments = [];
        for (let depth = sheet.depth_from_m; depth < sheet.depth_to_m; depth += PHOTO_SEGMENT_STEP_M) {
          const depthTo = Math.min(depth + PHOTO_SEGMENT_STEP_M, sheet.depth_to_m);
          const yFrom = depthToSheetPixel(rows, depth);
          const yTo = depthToSheetPixel(rows, depthTo);
          if (yTo <= yFrom) continue;
          segments.push({
            depthFrom: depth,
            depthTo,
            texture: cropSheetTexture(image, coreStrip.core_width_px, yFrom, yTo),
          });
        }
        return [holeId, segments];
      })
    ).then((entries) => {
      if (cancelled) return;
      const next = {};
      for (const [holeId, segments] of entries) if (segments) next[holeId] = segments;
      setByHole(next);
    });

    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [traces.map((t) => t.hole_id).join(",")]);

  return byHole;
}

// One unit cylinder (radius 1, height 1, centred on its local origin, axis
// along local Y), instanced per hole segment via SimpleMeshLayer - actual
// 3D geometry with normals, so it catches light and reads as a round drilled
// core the way a flat PathLayer ribbon never can.
const CORE_MESH = new CylinderGeometry({ radius: 1, height: 1, nradial: 16, topCap: true, bottomCap: true });

/** Column-major 4x4 mapping the unit cylinder onto the segment from a to b:
 * local Y (its height axis) becomes the segment direction/length, local X/Z
 * (its radius) are scaled to coreRadius, and it's translated to the midpoint. */
function segmentTransform(a, b, coreRadius) {
  const dx = b[0] - a[0], dy = b[1] - a[1], dz = b[2] - a[2];
  const len = Math.hypot(dx, dy, dz) || 1e-6;
  const dirX = dx / len, dirY = dy / len, dirZ = dz / len;

  // Any reference not parallel to the direction; swap when direction is
  // near-vertical (the common case for a drill hole) to avoid a degenerate cross product.
  const nearVertical = Math.abs(dirY) > 0.99;
  const refX = nearVertical ? 1 : 0, refY = nearVertical ? 0 : 1, refZ = 0;

  let rx = refY * dirZ - refZ * dirY;
  let ry = refZ * dirX - refX * dirZ;
  let rz = refX * dirY - refY * dirX;
  const rlen = Math.hypot(rx, ry, rz) || 1e-6;
  rx /= rlen; ry /= rlen; rz /= rlen;

  const fx = dirY * rz - dirZ * ry;
  const fy = dirZ * rx - dirX * rz;
  const fz = dirX * ry - dirY * rx;

  return [
    rx * coreRadius, ry * coreRadius, rz * coreRadius, 0,
    dirX * len, dirY * len, dirZ * len, 0,
    fx * coreRadius, fy * coreRadius, fz * coreRadius, 0,
    (a[0] + b[0]) / 2, (a[1] + b[1]) / 2, (a[2] + b[2]) / 2, 1,
  ];
}

export default function Hole3D({ traces = [], holes = [], verticalExaggeration = 25, colourBy = "mineral" }) {
  const [hovered, setHovered] = useState(null);
  const photoCylindersByHole = usePhotoCylinders(traces);

  const { segments, collars, anomalies, photoCylinders, view } = useMemo(
    () => buildScene(traces, verticalExaggeration, colourBy, holes, photoCylindersByHole),
    [traces, verticalExaggeration, colourBy, holes, photoCylindersByHole]
  );

  if (!traces.length) {
    return <p className="empty">Choose one or more holes to see them in 3D.</p>;
  }

  const layers = [
    new SimpleMeshLayer({
      id: "hole-core-cylinders",
      data: segments,
      mesh: CORE_MESH,
      coordinateSystem: COORDINATE_SYSTEM.CARTESIAN,
      getPosition: () => [0, 0, 0], // the segment's real position is already baked into getTransformMatrix
      getTransformMatrix: (segment) => segment.transform,
      getColor: (segment) => segment.colour,
      pickable: true,
      onHover: (info) => setHovered(info.object ? info : null),
    }),
    // One layer per photo-textured segment, not one shared layer like the
    // mineral cylinders above: SimpleMeshLayer takes a single texture per
    // layer, and every segment here has its own distinct crop of the core
    // photo. Fine at prototype scale (one sheet's worth per hole); revisit
    // if this grows to every sheet of every selected hole.
    ...photoCylinders.map((cyl) => new SimpleMeshLayer({
      id: `photo-cylinder-${cyl.holeId}-${cyl.depthFrom}`,
      data: [cyl],
      mesh: CORE_MESH,
      texture: cyl.texture,
      coordinateSystem: COORDINATE_SYSTEM.CARTESIAN,
      getPosition: () => [0, 0, 0],
      getTransformMatrix: (segment) => segment.transform,
      getColor: [255, 255, 255],
      pickable: true,
      onHover: (info) => setHovered(info.object ? info : null),
    })),
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
        key={`${traces.map((t) => t.hole_id).join(",")}:${photoCylinders.length > 0}`}
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

/** Straight-line "tangent method" desurvey - same maths as geo.py's
 * trace_points and lib/desurvey.js, but for one arbitrary depth rather than
 * a length-from-collar series: BH01's own borehole_length_m is wrong (3.6m
 * in the DB vs its real 398.3m per NVCL's own borehole.json) and fixing
 * that site-wide is a separate, case-by-case data-quality job (checked:
 * 6/177 downloaded holes disagree with NVCL by >10%, not all the same
 * direction, at least one NVCL record itself is garbage) - so the photo
 * cylinders use the core_strip.py depth range directly instead of trusting
 * that field. */
function desurveyPoint(depthM, inclinationDeg, azimuthDeg) {
  const dip = (Math.abs(inclinationDeg) * Math.PI) / 180;
  const azimuth = ((azimuthDeg || 0) * Math.PI) / 180;
  const horizontal = depthM * Math.cos(dip);
  return {
    east_m: horizontal * Math.sin(azimuth),
    north_m: horizontal * Math.cos(azimuth),
    tvd_m: depthM * Math.sin(dip),
  };
}

function buildScene(traces, exaggeration, colourBy, holes, photoCylindersByHole) {
  const empty = {
    segments: [], collars: [], anomalies: [], photoCylinders: [], view: defaultView(),
  };
  if (!traces.length) return empty;

  const holesById = Object.fromEntries(holes.map((hole) => [hole.hole_id, hole]));

  // origin = middle of the selected holes
  const originLat = average(traces.map((t) => t.latitude));
  const originLon = average(traces.map((t) => t.longitude));
  const metresPerDegreeLon =
    METRES_PER_DEGREE_LAT * Math.cos((originLat * Math.PI) / 180);

  const segments = [];
  const collars = [];
  const anomalies = [];
  const photoCylinders = [];
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

    // Photo-textured segments, positioned off the hole's own real
    // inclination/azimuth rather than its (possibly wrong) trace - see
    // desurveyPoint's comment. Independent depth range from the mineral
    // trace above, so this can extend well past it (BH01's photos run to
    // 561m; its trace above stops at 3.6m).
    const hole = holesById[trace.hole_id];
    const photoSegments = photoCylindersByHole[trace.hole_id];
    if (hole && photoSegments) {
      for (const seg of photoSegments) {
        const from = desurveyPoint(seg.depthFrom, hole.inclination_deg, hole.azimuth_deg);
        const to = desurveyPoint(seg.depthTo, hole.inclination_deg, hole.azimuth_deg);
        const a = [collarX + from.east_m, collarY + from.north_m, -from.tvd_m * exaggeration];
        const b = [collarX + to.east_m, collarY + to.north_m, -to.tvd_m * exaggeration];
        maxDown = Math.max(maxDown, to.tvd_m * exaggeration);
        photoCylinders.push({ path: [a, b], texture: seg.texture, holeId: trace.hole_id, depthFrom: seg.depthFrom, depthTo: seg.depthTo });
      }
    }

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
  //
  // KNOWN LIMITATION: photo cylinders are a short, real-scale span (metres)
  // sitting deep inside a scene sized for whole-hole traces (hundreds of
  // exaggerated metres) - e.g. BH01's photos start at 163m, far from its
  // own (separately wrong - see desurveyPoint's comment) 3.6m trace. Tried
  // framing the camera tightly on the photo cylinders specifically instead
  // of the whole scene; that made everything disappear rather than zoom in,
  // for a reason I haven't root-caused yet (not a clipping-plane value I
  // could find, not stale view state - ruled that out with a forced
  // remount too). Reverted to the plain whole-scene framing below, which IS
  // confirmed working (verified by temporarily rendering the photo
  // cylinders at 20x their real radius - clearly visible then), and sized
  // the radius up instead so they're findable without a special camera
  // path. Revisit the tight-framing approach separately if this isn't
  // visible enough in practice.
  const target = [0, 0, -maxDown / 2];
  const halfSize = Math.max(maxExtent, maxDown / 2, 50);

  // Each segment is a real 3D cylinder (see segmentTransform), radius scaled
  // off halfSize so it stays a sensible thickness whatever's currently framed.
  const coreRadius = Math.max(halfSize * 0.015, 2);
  segments.forEach((segment) => {
    segment.transform = segmentTransform(segment.path[0], segment.path[1], coreRadius);
  });
  photoCylinders.forEach((cylinder) => {
    // Thicker than the mineral cylinders: at real-core scale they'd be
    // legible only if you zoom in tight on this one small stretch of a
    // scene framed for the whole (much longer) hole. A flat multiplier
    // isn't physically accurate, and it's called out here rather than
    // silently baked in, so it can come out again once/if the camera
    // framing above is worth revisiting.
    cylinder.transform = segmentTransform(cylinder.path[0], cylinder.path[1], coreRadius * 6);
  });

  return {
    segments,
    collars,
    anomalies,
    photoCylinders,
    view: {
      ...defaultView(),
      target,
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

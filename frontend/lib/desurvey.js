// Turns a hole's collar (lat/lon/elevation) + single dip/azimuth reading into a
// 3D line from collar to toe, in [lng, lat, altitude-metres] - the same shape
// deck.gl's PathLayer wants when overlaid on a MapLibre map.
//
// HyLogger holes record one collar dip/azimuth, not a multi-station downhole
// survey, so there's no curvature to solve for: the industry-standard
// minimum-curvature desurvey collapses to this exact straight-line projection
// when there's only one station (see wa-drillhole-map's lib/desurvey.js for the
// general multi-station version, kept for if survey stations ever get added).
// This matches backend/holes/geo.py's trace_points() maths - cross-checked
// against its /trace/ output for the same hole.

const METRES_PER_DEGREE_LAT = 110540;

/**
 * @param {{latitude:number, longitude:number, elevation_m:?number, borehole_length_m:?number, inclination_deg:?number, azimuth_deg:?number}} hole
 * @param {number} exaggeration - depth stretch factor, purely visual
 * @returns {{collar:number[], toe:number[]}|null} null if the hole has nothing to draw (no depth or trajectory)
 */
export function desurveyStraightLine(hole, exaggeration = 1) {
  const depth = hole.borehole_length_m;
  if (!depth || hole.inclination_deg == null || hole.azimuth_deg == null) return null;

  const incRad = (hole.inclination_deg * Math.PI) / 180;
  const azRad = (hole.azimuth_deg * Math.PI) / 180;

  const horizontal = depth * Math.cos(incRad); // metres, flat distance from collar
  const tvd = -depth * Math.sin(incRad); // metres, positive = downward
  const east = horizontal * Math.sin(azRad);
  const north = horizontal * Math.cos(azRad);

  const metresPerDegreeLon = METRES_PER_DEGREE_LAT * Math.cos((hole.latitude * Math.PI) / 180);
  const collarZ = hole.elevation_m ?? 0;

  return {
    collar: [hole.longitude, hole.latitude, collarZ],
    toe: [
      hole.longitude + east / metresPerDegreeLon,
      hole.latitude + north / METRES_PER_DEGREE_LAT,
      collarZ - tvd * exaggeration,
    ],
  };
}

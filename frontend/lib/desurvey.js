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
 * @param {{latitude:number, longitude:number, elevation_m:?number, drawn_length_m:?number, inclination_deg:?number, azimuth_deg:?number}} hole
 * @param {number} exaggeration - depth stretch factor, purely visual
 * @returns {{collar:number[], toe:number[]}|null} null if the hole has nothing to draw (no depth or trajectory)
 */
export function desurveyStraightLine(hole, exaggeration = 1) {
  // Not borehole_length_m: that's metres of core scanned, and logging often
  // starts below the collar - drawn_length_m reaches the deepest logged metre.
  const depth = hole.drawn_length_m;
  if (!depth || hole.inclination_deg == null || hole.azimuth_deg == null) return null;

  return {
    collar: desurveyPoint(hole, 0, exaggeration),
    toe: desurveyPoint(hole, depth, exaggeration),
  };
}

/**
 * Any depth down the hole on that same collar-to-toe line, as
 * [lng, lat, altitude-metres] - e.g. where one mineral run starts and ends, so
 * the run lands exactly on the line desurveyStraightLine draws.
 */
export function desurveyPoint(hole, depthM, exaggeration = 1) {
  const incRad = (hole.inclination_deg * Math.PI) / 180;
  const azRad = (hole.azimuth_deg * Math.PI) / 180;

  const horizontal = depthM * Math.cos(incRad); // metres, flat distance from collar
  const tvd = -depthM * Math.sin(incRad); // metres, positive = downward
  const east = horizontal * Math.sin(azRad);
  const north = horizontal * Math.cos(azRad);

  const metresPerDegreeLon = METRES_PER_DEGREE_LAT * Math.cos((hole.latitude * Math.PI) / 180);
  const collarZ = hole.elevation_m ?? 0;

  return [
    hole.longitude + east / metresPerDegreeLon,
    hole.latitude + north / METRES_PER_DEGREE_LAT,
    collarZ - tvd * exaggeration,
  ];
}

/**
 * Same straight-line desurvey as above, but for a single arbitrary depth
 * rather than only the toe - e.g. a core-photo cylinder segment positioned
 * at its own depth range, independent of the hole's logged trace (a
 * core_strip.py photo run can extend past what's been logged).
 * @returns {{east_m:number, north_m:number, tvd_m:number}} unexaggerated - positive tvd_m is downward
 */
export function desurveyOffset(depthM, inclinationDeg, azimuthDeg) {
  const incRad = (Math.abs(inclinationDeg) * Math.PI) / 180;
  const azRad = ((azimuthDeg || 0) * Math.PI) / 180;
  const horizontal = depthM * Math.cos(incRad);
  return {
    east_m: horizontal * Math.sin(azRad),
    north_m: horizontal * Math.cos(azRad),
    tvd_m: depthM * Math.sin(incRad),
  };
}

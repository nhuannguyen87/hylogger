// Every call to the Django API goes through here. One place to add auth headers
// or change error handling later.

import { API_BASE } from "@/config";

async function get(path) {
  const response = await fetch(`${API_BASE}${path}`, { cache: "no-store" });
  if (!response.ok) {
    throw new Error(
      `${response.status} from ${path}. Is the Django server running on ${API_BASE}?`
    );
  }
  return response.json();
}

export const getStats = () => get("/stats/");

export const getHoles = ({ search = "", anomaliesOnly = false, limit = 3000 } = {}) => {
  const query = new URLSearchParams({ limit: String(limit) });
  if (search) query.set("search", search);
  if (anomaliesOnly) query.set("anomalies_only", "1");
  return get(`/holes/?${query}`);
};

export const getHole = (holeId) => get(`/holes/${holeId}/`);
export const getMeasurements = (holeId) => get(`/holes/${holeId}/measurements/`);
export const getTrays = (holeId) => get(`/holes/${holeId}/trays/`);
export const getAnomalies = (holeId) => get(`/holes/${holeId}/anomalies/`);
export const getTrace = (holeId, stepM = 5) => get(`/holes/${holeId}/trace/?step_m=${stepM}`);
export const getNearby = (holeId, km = 25) => get(`/holes/${holeId}/nearby/?km=${km}`);
export const getDistance = (a, b) => get(`/distance/?a=${a}&b=${b}`);

/**
 * Every hole's mineral log in one call, merged into [from_m, to_m, group]
 * runs - what lets the map colour every 3D core on first load. Pass
 * CONFIDENCE_THRESHOLD so it greys out the same intervals StripLog does.
 */
export const getMineralLogs = (minConfidence) => get(`/mineral-logs/?min_confidence=${minConfidence}`);

/**
 * Real VSWIR/TIR spectrum + mineral call, only for the handful of holes with
 * has_full_spectrum=true. null (not an error) for every other hole - that's
 * the normal case, not a failure, so it doesn't go through get()'s throw.
 */
export const getSpectralSample = async (holeId, depthM) => {
  const query = depthM != null ? `?depth_m=${depthM}` : "";
  const response = await fetch(`${API_BASE}/holes/${holeId}/spectral-sample/${query}`, { cache: "no-store" });
  if (response.status === 404) return null;
  if (!response.ok) throw new Error(`${response.status} from /holes/${holeId}/spectral-sample/`);
  return response.json();
};

/**
 * Continuous core-photo + TSG strip (core_strip.py output), only for holes
 * that have actually been run through that pipeline. null (not an error)
 * for every other hole - same normal-not-a-failure shape as getSpectralSample.
 */
export const getCoreStrip = async (holeId) => {
  const response = await fetch(`${API_BASE}/holes/${holeId}/core-strip/`, { cache: "no-store" });
  // 202: the backend is building this hole's strip from NVCL right now - ask again shortly
  if (response.status === 202) return { building: true };
  // 404: no photos for this hole (NVCL has none, or they're another hole's) - say why
  if (response.status === 404) {
    const body = await response.json().catch(() => ({}));
    return { unavailable: body.detail || "No core photos for this hole." };
  }
  if (!response.ok) throw new Error(`${response.status} from /holes/${holeId}/core-strip/`);
  return response.json();
};

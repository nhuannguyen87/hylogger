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

export const getHoles = ({ search = "", anomaliesOnly = false, limit = 1000 } = {}) => {
  const query = new URLSearchParams({ limit: String(limit) });
  if (search) query.set("search", search);
  if (anomaliesOnly) query.set("anomalies_only", "1");
  return get(`/holes/?${query}`);
};

export const getHole = (holeId) => get(`/holes/${holeId}/`);
export const getMeasurements = (holeId) => get(`/holes/${holeId}/measurements/`);
export const getAnomalies = (holeId) => get(`/holes/${holeId}/anomalies/`);
export const getTrace = (holeId, stepM = 5) => get(`/holes/${holeId}/trace/?step_m=${stepM}`);
export const getNearby = (holeId, km = 25) => get(`/holes/${holeId}/nearby/?km=${km}`);
export const getDistance = (a, b) => get(`/distance/?a=${a}&b=${b}`);

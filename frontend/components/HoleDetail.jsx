"use client";

// The right-hand panel: what this hole is, its mineral log, and what's near it.

import { useEffect, useState } from "react";
import Link from "next/link";
import { getHole, getMeasurements, getNearby } from "@/lib/api";
import { ANOMALY_COLOUR, LOW_CONFIDENCE_COLOUR, MINERAL_COLOURS } from "@/config";
import StripLog from "./StripLog";

export default function HoleDetail({ holeId, onSelect }) {
  const [hole, setHole] = useState(null);
  const [measurements, setMeasurements] = useState([]);
  const [nearby, setNearby] = useState([]);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!holeId) return;
    setError(null);
    setHole(null);

    Promise.all([
      getHole(holeId),
      getMeasurements(holeId),
      getNearby(holeId, 50),
    ])
      .then(([holeData, measurementData, nearbyData]) => {
        setHole(holeData);
        setMeasurements(measurementData);
        setNearby(nearbyData);
      })
      .catch((err) => setError(err.message));
  }, [holeId]);

  if (!holeId) {
    return (
      <aside className="detail">
        <p className="empty">
          Pick a hole on the map or in the list.
          <br />
          Its mineral log appears here.
        </p>
      </aside>
    );
  }

  if (error) {
    return <aside className="detail"><div className="error">{error}</div></aside>;
  }

  if (!hole) {
    return <aside className="detail"><p className="hint">Loading {holeId}…</p></aside>;
  }

  const lowConfidence = measurements.filter((m) => m.confidence < 0.5).length;

  return (
    <aside className="detail">
      <h2 style={{ margin: "0 0 2px", fontSize: 20 }} className="mono">{hole.hole_id}</h2>
      <p style={{ margin: "0 0 16px", color: "var(--text-dim)" }}>{hole.hole_name}</p>

      <table className="facts">
        <tbody>
          <tr><td>Length</td><td>{fmt(hole.borehole_length_m)} m</td></tr>
          <tr><td>Collar elevation</td><td>{fmt(hole.elevation_m)} m</td></tr>
          <tr><td>Latitude</td><td>{hole.latitude?.toFixed(5)}</td></tr>
          <tr><td>Longitude</td><td>{hole.longitude?.toFixed(5)}</td></tr>
          <tr><td>Inclination</td><td>{fmt(hole.inclination_deg)}°</td></tr>
          <tr><td>Azimuth</td><td>{fmt(hole.azimuth_deg)}°</td></tr>
          <tr><td>Intervals</td><td>{hole.measurement_count}</td></tr>
          <tr>
            <td>Flagged</td>
            <td>
              {hole.anomaly_count > 0
                ? <span className="badge warn">{hole.anomaly_count} unusual</span>
                : <span className="badge">none</span>}
            </td>
          </tr>
        </tbody>
      </table>

      <p className="section-title">Mineral log</p>
      <div style={{ display: "flex", gap: 16, alignItems: "flex-start" }}>
        <StripLog measurements={measurements} height={420} />
        <Legend measurements={measurements} lowConfidence={lowConfidence} />
      </div>

      <p className="section-title" style={{ marginTop: 22 }}>Nearest holes</p>
      {nearby.length === 0 ? (
        <p className="hint">Nothing else within 50 km.</p>
      ) : (
        <div>
          {nearby.slice(0, 6).map((other) => (
            <button
              key={other.hole_id}
              className="hole-row"
              onClick={() => onSelect?.(other.hole_id)}
            >
              <span className="id">{other.hole_id}</span>
              <span className="name">{other.hole_name}</span>
              <span className="len">{other.distance_km.toFixed(1)} km</span>
            </button>
          ))}
          <p style={{ marginTop: 12 }}>
            <Link href={`/compare?a=${hole.hole_id}&b=${nearby[0].hole_id}`}>
              Compare with {nearby[0].hole_id} →
            </Link>
          </p>
        </div>
      )}
    </aside>
  );
}

function Legend({ measurements, lowConfidence }) {
  // only show minerals that actually appear in this hole
  const present = [...new Set(measurements.map((m) => m.mineral_1).filter(Boolean))];

  return (
    <div style={{ fontSize: 11, color: "var(--text-dim)" }}>
      {present.map((mineral) => (
        <div className="legend-item" key={mineral}>
          <span
            className="legend-swatch"
            style={{ background: MINERAL_COLOURS[mineral] || "var(--text-dim)" }}
          />
          {mineral}
        </div>
      ))}
      <div className="legend-item" style={{ marginTop: 10 }}>
        <span className="legend-swatch" style={{ background: LOW_CONFIDENCE_COLOUR }} />
        low confidence ({lowConfidence})
      </div>
      <div className="legend-item">
        <span className="legend-swatch" style={{ background: ANOMALY_COLOUR }} />
        unusual reading
      </div>
    </div>
  );
}

const fmt = (value) => (value == null ? "–" : Number(value).toFixed(1));

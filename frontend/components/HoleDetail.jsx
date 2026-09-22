"use client";

// The right-hand panel: what this hole is, its mineral log, and what's near it.

import { useEffect, useState } from "react";
import Link from "next/link";
import { getHole, getMeasurements, getNearby, getSpectralSample, getTrays } from "@/lib/api";
import { ANOMALY_COLOUR, LOW_CONFIDENCE_COLOUR, MINERAL_COLOURS } from "@/config";
import { distinctName } from "@/lib/format";
import StripLog from "./StripLog";
import CoreTrays from "./CoreTrays";

export default function HoleDetail({ holeId, onSelect }) {
  const [hole, setHole] = useState(null);
  const [measurements, setMeasurements] = useState([]);
  const [trays, setTrays] = useState([]);
  const [nearby, setNearby] = useState([]);
  // undefined = still checking, null = checked and nothing available, object = real data
  const [spectral, setSpectral] = useState(undefined);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!holeId) return;
    let cancelled = false;
    setError(null);
    setHole(null);
    setSpectral(undefined);

    Promise.all([
      getHole(holeId),
      getMeasurements(holeId),
      getTrays(holeId),
      getNearby(holeId, 50),
    ])
      .then(([holeData, measurementData, trayData, nearbyData]) => {
        if (cancelled) return;
        setHole(holeData);
        setMeasurements(measurementData);
        setTrays(trayData);
        setNearby(nearbyData);
        // separate request: every hole tries this, but the 5 database5553
        // holes answer instantly while everyone else is a live NVCL lookup
        // (a few seconds) - no need to hold up the rest of the panel on it
        getSpectralSample(holeId).then((data) => !cancelled && setSpectral(data));
      })
      .catch((err) => !cancelled && setError(err.message));

    return () => {
      cancelled = true;
    };
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
      <p style={{ margin: "0 0 16px", color: "var(--text-dim)" }}>
        {distinctName(hole)}
        {hole.confidential && (
          <span className="badge confidential" style={{ marginLeft: 8 }}>confidential</span>
        )}
        {hole.has_full_spectrum && (
          <span
            className="badge"
            style={{ marginLeft: 8, background: "rgba(185, 140, 255, 0.15)", color: "var(--accent-b)" }}
            title="Full spectrum restored locally from database5553/ - instant, pre-verified. Every other hole still shows its own spectrum below, fetched live from NVCL."
          >
            verified spectrum
          </span>
        )}
      </p>

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

      <CoreTrays trays={trays} />

      <p className="section-title" style={{ marginTop: 22 }}>Full spectral data</p>
      {spectral === undefined && (
        <p className="hint">
          {hole.has_full_spectrum ? "Loading real VSWIR/TIR spectrum…" : "Checking NVCL for a real spectrum here (a few seconds)…"}
        </p>
      )}
      {spectral === null && (
        <p className="hint">No spectral log available for this hole.</p>
      )}
      {spectral && <FullSpectrum sample={spectral} />}

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

/** Real per-depth mineral calls + a VSWIR/TIR sparkline, from etl4_bridge.py
 * (database5553/, instant) or nvcl_bridge.py (live NVCL, a few seconds). */
function FullSpectrum({ sample }) {
  const uniqueMinerals = [...new Set(sample.minerals.map((m) => m.mineral))];

  return (
    <div>
      <p className="hint" style={{ marginBottom: 8 }}>
        Sample {sample.sample_no} of {sample.sample_count} · {sample.md_m?.toFixed(2)} m
        {" · "}
        {sample.source === "database5553" ? "restored locally" : "live from NVCL"}
      </p>

      {uniqueMinerals.length > 0 && (
        <div style={{ marginBottom: 12 }}>
          {uniqueMinerals.map((mineral) => (
            <span key={mineral} className="badge" style={{ marginRight: 6, marginBottom: 6, display: "inline-block" }}>
              {mineral}
            </span>
          ))}
        </div>
      )}

      {sample.spectra.map((spectrum) => (
        <Sparkline key={spectrum.region} spectrum={spectrum} />
      ))}
    </div>
  );
}

/** Plain SVG polyline - wavelength on x, reflectance on y, no library. */
function Sparkline({ spectrum, width = 320, height = 70 }) {
  const values = spectrum.values;
  const finite = values.filter((v) => v != null && Number.isFinite(v));
  if (!finite.length) return null;

  const min = Math.min(...finite);
  const max = Math.max(...finite) || 1;
  const toX = (i) => (i / (values.length - 1)) * width;
  const toY = (v) => height - ((v - min) / (max - min || 1)) * height;

  const points = values
    .map((v, i) => (v == null || !Number.isFinite(v) ? null : `${toX(i).toFixed(1)},${toY(v).toFixed(1)}`))
    .filter(Boolean)
    .join(" ");

  return (
    <div style={{ marginBottom: 10 }}>
      <p className="hint" style={{ marginBottom: 2 }}>
        {spectrum.region} · {values.length} channels · {spectrum.wavelength[0]}–{spectrum.wavelength.at(-1)} {spectrum.wavelength_unit}
      </p>
      <svg width={width} height={height} role="img" aria-label={`${spectrum.region} spectrum`}>
        <polyline points={points} fill="none" stroke="var(--accent)" strokeWidth="1.5" />
      </svg>
    </div>
  );
}

const fmt = (value) => (value == null ? "–" : Number(value).toFixed(1));

"use client";

// Pick two holes, see how far apart they are and how their logs line up.
// The distance comes from PostGIS via the API - we don't do geodesy in the browser.

import { Suspense, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import { getDistance, getHoles, getMeasurements } from "@/lib/api";
import StripLog from "@/components/StripLog";

export default function ComparePageWrapper() {
  // useSearchParams needs a Suspense boundary in the app router
  return (
    <Suspense fallback={<div className="page"><p className="hint">Loading…</p></div>}>
      <ComparePage />
    </Suspense>
  );
}

function ComparePage() {
  const params = useSearchParams();
  const [holes, setHoles] = useState([]);
  const [aId, setAId] = useState(params.get("a") || "");
  const [bId, setBId] = useState(params.get("b") || "");
  const [logA, setLogA] = useState([]);
  const [logB, setLogB] = useState([]);
  const [distance, setDistance] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    getHoles({ limit: 2000 }).then(setHoles).catch((err) => setError(err.message));
  }, []);

  useEffect(() => {
    if (aId) getMeasurements(aId).then(setLogA).catch(() => setLogA([]));
  }, [aId]);

  useEffect(() => {
    if (bId) getMeasurements(bId).then(setLogB).catch(() => setLogB([]));
  }, [bId]);

  useEffect(() => {
    if (aId && bId && aId !== bId) {
      getDistance(aId, bId).then(setDistance).catch((err) => setError(err.message));
    } else {
      setDistance(null);
    }
  }, [aId, bId]);

  // draw both logs against the same depth scale, or the comparison lies
  const deepest = Math.max(
    ...logA.map((m) => m.depth_to_m),
    ...logB.map((m) => m.depth_to_m),
    1
  );

  return (
    <div className="page">
      <h1 style={{ fontSize: 18, margin: "0 0 4px" }}>Compare two holes</h1>
      <p className="hint" style={{ marginBottom: 20 }}>
        Both logs use the same depth scale, so a band at 40 m on the left sits level
        with 40 m on the right.
      </p>

      {error && <div className="error" style={{ marginBottom: 16 }}>{error}</div>}

      <div className="pick">
        <div className="field">
          <p className="section-title">Hole A</p>
          <HolePicker holes={holes} value={aId} onChange={setAId} />
        </div>
        <div className="field">
          <p className="section-title">Hole B</p>
          <HolePicker holes={holes} value={bId} onChange={setBId} />
        </div>
      </div>

      {distance && (
        <div className="distance-readout">
          <span className="value">{distance.distance_km.toFixed(2)}</span>
          <span className="hint">
            km apart · {distance.hole_a.hole_id} → {distance.hole_b.hole_id}
            <br />
            measured on the earth&apos;s surface by PostGIS
          </span>
        </div>
      )}

      <div className="compare-grid">
        {aId && (
          <StripLog
            measurements={logA}
            maxDepth={deepest}
            height={520}
            width={54}
            label={aId}
          />
        )}
        {bId && (
          <StripLog
            measurements={logB}
            maxDepth={deepest}
            height={520}
            width={54}
            label={bId}
            accent="var(--accent-b)"
          />
        )}
        {!aId && !bId && (
          <p className="empty">Choose two holes above to line their logs up.</p>
        )}
      </div>
    </div>
  );
}

function HolePicker({ holes, value, onChange }) {
  return (
    <select value={value} onChange={(event) => onChange(event.target.value)}>
      <option value="">Choose a hole…</option>
      {holes.map((hole) => (
        <option key={hole.hole_id} value={hole.hole_id}>
          {hole.hole_id} — {hole.hole_name}
        </option>
      ))}
    </select>
  );
}

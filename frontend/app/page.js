"use client";

// The main view: search + list on the left, map in the middle, hole detail right.

import { useEffect, useState } from "react";
import dynamic from "next/dynamic";
import { getHoles } from "@/lib/api";
import HoleDetail from "@/components/HoleDetail";

// MapLibre touches `window`, so it can't run during server rendering.
const HoleMap = dynamic(() => import("@/components/HoleMap"), {
  ssr: false,
  loading: () => <p className="hint" style={{ padding: 16 }}>Loading map…</p>,
});

export default function ExplorePage() {
  const [holes, setHoles] = useState([]);
  const [search, setSearch] = useState("");
  const [anomaliesOnly, setAnomaliesOnly] = useState(false);
  const [selectedId, setSelectedId] = useState(null);
  const [error, setError] = useState(null);

  // refetch when the filters change, with a short pause so we aren't
  // hitting the API on every keystroke
  useEffect(() => {
    const timer = setTimeout(() => {
      getHoles({ search, anomaliesOnly })
        .then((data) => {
          setHoles(data);
          setError(null);
        })
        .catch((err) => setError(err.message));
    }, 250);
    return () => clearTimeout(timer);
  }, [search, anomaliesOnly]);

  return (
    <div className="columns">
      <div className="sidebar">
        <div className="search-row">
          <input
            type="search"
            placeholder="Search hole id or name"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
          <label className="checkbox">
            <input
              type="checkbox"
              checked={anomaliesOnly}
              onChange={(event) => setAnomaliesOnly(event.target.checked)}
            />
            Only holes with flagged intervals
          </label>
        </div>

        <div className="hole-list">
          {error && <div className="error" style={{ margin: 12 }}>{error}</div>}

          {!error && holes.length === 0 && (
            <p className="empty">
              No holes match.
              <br />
              If the list is empty on first run, load the data:
              <br />
              <code className="mono">python manage.py load_data</code>
            </p>
          )}

          {holes.map((hole) => (
            <button
              key={hole.hole_id}
              className={`hole-row ${hole.hole_id === selectedId ? "selected" : ""}`}
              onClick={() => setSelectedId(hole.hole_id)}
            >
              <span className="id">{hole.hole_id}</span>
              <span className="name">{hole.hole_name}</span>
              <span className="len">{Math.round(hole.borehole_length_m || 0)} m</span>
            </button>
          ))}
        </div>
      </div>

      <div className="map-area">
        <HoleMap holes={holes} selectedId={selectedId} onSelect={setSelectedId} />
        <div className="legend">
          <div style={{ color: "var(--text-dim)" }}>{holes.length} holes shown</div>
          <div className="legend-item">
            <span className="legend-swatch" style={{ background: "var(--accent)" }} />
            selected
          </div>
        </div>
      </div>

      <HoleDetail holeId={selectedId} onSelect={setSelectedId} />
    </div>
  );
}

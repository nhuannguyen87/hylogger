"use client";

// Pick a few holes and look at them underground.

import { useEffect, useState } from "react";
import dynamic from "next/dynamic";
import { getHoles, getNearby, getTrace } from "@/lib/api";
import { DEFAULT_VERTICAL_EXAGGERATION } from "@/config";
import { distinctName } from "@/lib/format";
import CoreStripPanel from "@/components/CoreStripPanel";

const Hole3D = dynamic(() => import("@/components/Hole3D"), { ssr: false });

const MAX_HOLES = 12; // keeps it readable and the browser happy

export default function Viewer3DPage() {
  const [holes, setHoles] = useState([]);
  const [chosen, setChosen] = useState([]);
  const [traces, setTraces] = useState([]);
  const [exaggeration, setExaggeration] = useState(DEFAULT_VERTICAL_EXAGGERATION);
  const [colourBy, setColourBy] = useState("mineral");
  const [error, setError] = useState(null);

  useEffect(() => {
    getHoles()
      .then((data) => {
        setHoles(data);
        if (data.length) setChosen([data[0].hole_id]);
      })
      .catch((err) => setError(err.message));
  }, []);

  useEffect(() => {
    if (!chosen.length) {
      setTraces([]);
      return;
    }
    Promise.all(chosen.map((holeId) => getTrace(holeId)))
      .then(setTraces)
      .catch((err) => setError(err.message));
  }, [chosen]);

  function toggle(holeId) {
    setChosen((current) =>
      current.includes(holeId)
        ? current.filter((id) => id !== holeId)
        : [...current, holeId].slice(-MAX_HOLES)
    );
  }

  /** Add the closest handful of holes to whatever is already selected. */
  async function addNeighbours() {
    if (!chosen.length) return;
    const nearby = await getNearby(chosen[0], 100);
    setChosen((current) =>
      [...new Set([...current, ...nearby.slice(0, 5).map((h) => h.hole_id)])].slice(0, MAX_HOLES)
    );
  }

  return (
    <div className="columns">
      <div className="sidebar">
        <div className="search-row">
          <p className="section-title">Holes in view</p>
          <p className="hint">
            Up to {MAX_HOLES}. Drag to rotate, scroll to zoom.
          </p>
          <button
            className="action"
            style={{ marginTop: 10, width: "100%" }}
            onClick={addNeighbours}
            disabled={!chosen.length}
          >
            Add 5 nearest holes
          </button>
        </div>

        <div className="hole-list">
          {error && <div className="error" style={{ margin: 12 }}>{error}</div>}
          {holes.map((hole) => (
            <button
              key={hole.hole_id}
              className={`hole-row ${chosen.includes(hole.hole_id) ? "selected" : ""}`}
              onClick={() => toggle(hole.hole_id)}
            >
              <span className="id">{hole.hole_id}</span>
              {distinctName(hole) && <span className="name">{distinctName(hole)}</span>}
              <span className="len">{Math.round(hole.borehole_length_m || 0)} m</span>
            </button>
          ))}
        </div>
      </div>

      <div className="map-area">
        <Hole3D
          traces={traces}
          holes={holes}
          verticalExaggeration={exaggeration}
          colourBy={colourBy}
        />

        <div className="controls-3d">
          <p className="section-title">Colour by</p>
          <select value={colourBy} onChange={(event) => setColourBy(event.target.value)}>
            <option value="mineral">Mineral</option>
            <option value="anomaly">Anomaly score</option>
          </select>

          <p className="section-title" style={{ marginTop: 14 }}>
            Depth stretch ×{exaggeration}
          </p>
          <input
            type="range"
            min="1"
            max="80"
            value={exaggeration}
            onChange={(event) => setExaggeration(Number(event.target.value))}
          />
          <p className="hint" style={{ marginTop: 6 }}>
            Real holes are kilometres apart and only metres wide, so depth is
            stretched to make them visible. Set this to 1 for true scale.
          </p>
        </div>

        <CoreStripPanel holeId={chosen[0]} />
      </div>
    </div>
  );
}

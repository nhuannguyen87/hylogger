"use client";

// Pick two holes on the map, see how far apart they are and how their logs line up.
// The distance comes from PostGIS via the API - we don't do geodesy in the browser.
//
// Picking is hover-driven, no click required (see handleHover): whichever
// hole you're over becomes A until you move over a different one, which
// becomes B - after that, further hovering just fine-tunes B so refining
// your second pick can't accidentally disturb the first.

import { Suspense, useEffect, useState } from "react";
import dynamic from "next/dynamic";
import { useSearchParams } from "next/navigation";
import { getDistance, getHoles, getMeasurements } from "@/lib/api";
import { distinctName } from "@/lib/format";
import StripLog from "@/components/StripLog";

const HoleMap = dynamic(() => import("@/components/HoleMap"), {
  ssr: false,
  loading: () => <p className="hint" style={{ padding: 16 }}>Loading map…</p>,
});

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
    getHoles().then(setHoles).catch((err) => setError(err.message));
  }, []);

  /** The hole under the cursor fills whichever slot makes sense: A first,
   * then B, then B keeps updating so A stays put once it's chosen. */
  function handleHover(holeId) {
    if (!aId) setAId(holeId);
    else if (holeId !== aId) setBId(holeId);
  }

  function reset() {
    setAId("");
    setBId("");
  }

  function swap() {
    setAId(bId);
    setBId(aId);
  }

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

  const holeA = holes.find((hole) => hole.hole_id === aId);
  const holeB = holes.find((hole) => hole.hole_id === bId);

  return (
    <div className="page">
      <h1 style={{ fontSize: 18, margin: "0 0 4px" }}>Compare two holes</h1>
      <p className="hint" style={{ marginBottom: 20 }}>
        Move over a hole on the map to set it as A, then move over a different one for B
        - no clicking needed. Both logs below use the same depth scale, so a band at 40 m
        on the left sits level with 40 m on the right.
      </p>

      {error && <div className="error" style={{ marginBottom: 16 }}>{error}</div>}

      <div className="compare-map">
        <HoleMap
          holes={holes} selectedId={aId} selectedIdB={bId}
          onHover={handleHover} onSelect={handleHover} flyToSelection={false}
        />

        <div className="controls-3d" style={{ width: 210, top: "auto", left: "auto", bottom: 12, right: 12 }}>
          <p className="section-title">Hole A</p>
          <p className="mono" style={{ color: "var(--accent)", margin: "0 0 10px" }}>
            {aId || "hover a hole…"}
          </p>
          <p className="section-title">Hole B</p>
          <p className="mono" style={{ color: "var(--accent-b)", margin: "0 0 10px" }}>
            {bId || "hover a different hole…"}
          </p>
          <div className="seg">
            <button type="button" onClick={swap} disabled={!aId || !bId}>Swap</button>
            <button type="button" onClick={reset} disabled={!aId && !bId}>Reset</button>
          </div>
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
            label={holeA ? [aId, distinctName(holeA)].filter(Boolean).join(" · ") : aId}
          />
        )}
        {bId && (
          <StripLog
            measurements={logB}
            maxDepth={deepest}
            height={520}
            width={54}
            label={holeB ? [bId, distinctName(holeB)].filter(Boolean).join(" · ") : bId}
            accent="var(--accent-b)"
          />
        )}
        {!aId && !bId && (
          <p className="empty">Hover two holes on the map above to line their logs up.</p>
        )}
      </div>
    </div>
  );
}

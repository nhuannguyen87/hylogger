"use client";

// 3D: the satellite map on its own until you click a hole - then that hole
// opens on the right as a real drill core (its NVCL tray photos wrapped round
// a cylinder at their true depths, see Hole3D). Click a second hole to stand
// the two cores side by side at the same depth; click a chosen hole again,
// or its ×, to put it back.

import { Suspense, useEffect, useState } from "react";
import dynamic from "next/dynamic";
import { useSearchParams } from "next/navigation";
import { getDistance, getHoles, getMineralLogs } from "@/lib/api";
import { CONFIDENCE_THRESHOLD } from "@/config";

const HoleMap = dynamic(() => import("@/components/HoleMap"), {
  ssr: false,
  loading: () => <p className="hint" style={{ padding: 16 }}>Loading map…</p>,
});
const Hole3D = dynamic(() => import("@/components/Hole3D"), { ssr: false });

const MAX_HOLES = 2; // the map marks two (A and B); a third click replaces the older

export default function Viewer3DPageWrapper() {
  // useSearchParams needs a Suspense boundary in the app router
  return (
    <Suspense fallback={<div className="page"><p className="hint">Loading…</p></div>}>
      <Viewer3DPage />
    </Suspense>
  );
}

function Viewer3DPage() {
  const params = useSearchParams();
  const [holes, setHoles] = useState([]);
  const [mineralLogs, setMineralLogs] = useState(null);
  const [chosen, setChosen] = useState(() => [params.get("a"), params.get("b")].filter(Boolean));
  const [distance, setDistance] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    getHoles().then(setHoles).catch((err) => setError(err.message));
    getMineralLogs(CONFIDENCE_THRESHOLD).then(setMineralLogs).catch(() => setMineralLogs(null));
  }, []);

  const [a, b] = chosen;
  useEffect(() => {
    setDistance(null);
    if (a && b) getDistance(a, b).then(setDistance).catch(() => setDistance(null));
  }, [a, b]);

  function toggle(holeId) {
    setChosen((current) => (current.includes(holeId)
      ? current.filter((id) => id !== holeId)
      : [...current, holeId].slice(-MAX_HOLES)));
  }

  return (
    <div className="split">
      <div className="map-area">
        {error && <div className="error" style={{ position: "absolute", top: 12, left: 260, zIndex: 3 }}>{error}</div>}
        <HoleMap
          holes={holes}
          mineralLogs={mineralLogs}
          initial3d
          selectedId={a}
          selectedIdB={b}
          onSelect={toggle}
          flyToId={chosen[chosen.length - 1]}
          coreStripPanel={false}
          focusIds={chosen}
        />
        {!chosen.length && (
          <div className="legend">
            Click a hole to open its core in 3D · click a second one to compare them side by side
          </div>
        )}
      </div>

      {chosen.length > 0 && (
        <div className="core3d-area">
          <Hole3D holeIds={chosen} distanceKm={distance?.distance_km} onClose={toggle} />
        </div>
      )}
    </div>
  );
}

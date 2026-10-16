"use client";

// The hole's core photo, shown on the map when 3D is on: ETL4's core-row
// crops (one ~400x25 px image per tray row, cut from the NVCL tray photos),
// stacked like the rows of a tray, each labelled with its depth range. See
// lib/api.js getCoreStrip. A hole with none says so in a line, not an error.

import { useEffect, useState } from "react";
import { getCoreStrip } from "@/lib/api";
import { MEDIA_BASE } from "@/config";
import MapPanel from "./MapPanel";

export default function CoreStripPanel({ holeId, className = "" }) {
  const [strip, setStrip] = useState(undefined);

  useEffect(() => {
    setStrip(undefined);
    if (!holeId) return;
    let cancelled = false;
    getCoreStrip(holeId)
      .then((data) => !cancelled && setStrip(data))
      .catch(() => !cancelled && setStrip(null));
    return () => {
      cancelled = true;
    };
  }, [holeId]);

  if (!holeId || strip === null) return null; // nothing selected, or the API failed - stay out of the way

  const ready = strip && !strip.unavailable;
  // one MapPanel through every state, so "−" stays minimised from hole to hole
  return (
    <MapPanel
      className={`core-strip-panel ${className}`}
      label="core photo"
      head={
        strip === undefined ? (
          <p className="hint">Loading core photo…</p>
        ) : strip.unavailable ? (
          <p className="hint">No core photo for {holeId}: {strip.unavailable}</p>
        ) : (
          <span className="section-title" style={{ margin: 0 }}>
            Core photo · {holeId} · {strip.depth_min_m.toFixed(0)}-{strip.depth_max_m.toFixed(0)} m
          </span>
        )
      }
    >
      {ready && (
        <div className="core-strip-sheets">
          {strip.sheets.map((sheet) => (
            <div className="core-strip-sheet" key={sheet.sheet_index}>
              <div className="core-strip-sheet-label mono">
                {sheet.depth_from_m.toFixed(1)}-{sheet.depth_to_m.toFixed(1)} m
              </div>
              <div className={`core-strip-pair ${sheet.tsg_url ? "" : "rows"}`}>
                {/* lazy: a deep hole has hundreds of rows - fetch only those scrolled into view */}
                {sheet.tsg_url && (
                  <img loading="lazy" src={MEDIA_BASE + sheet.tsg_url} alt={`TSG mineral, ${sheet.depth_from_m}-${sheet.depth_to_m} m`} />
                )}
                <img loading="lazy" src={MEDIA_BASE + sheet.photo_url} alt={`Core photo, ${sheet.depth_from_m}-${sheet.depth_to_m} m`} />
              </div>
            </div>
          ))}
        </div>
      )}
    </MapPanel>
  );
}

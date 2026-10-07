"use client";

// The real, continuous core photo (+ matching TSG mineral-colour strip),
// built offline by core_strip.py/mineral_strip.py from real GSWA/NVCL tray
// photos - see those files at the repo root for how a photo becomes this.
// Only holes actually run through that pipeline have anything to show
// (just BH01 so far); every other hole renders the same "not available"
// hint the spectral panel already uses, not an error.
//
// A full hole is split into depth-bounded "sheets" (core_strip.py's own
// doc explains why: an unbroken column would be over a million pixels
// tall). Each sheet keeps its own internal scroll so a 400m hole doesn't
// need one impossibly tall scrollbar.

import { useEffect, useState } from "react";
import { getCoreStrip } from "@/lib/api";
import { MEDIA_BASE } from "@/config";

export default function CoreStripPanel({ holeId, className = "" }) {
  const [strip, setStrip] = useState(undefined);

  useEffect(() => {
    if (!holeId) {
      setStrip(undefined);
      return;
    }
    setStrip(undefined);
    getCoreStrip(holeId).then(setStrip).catch(() => setStrip(null));
  }, [holeId]);

  if (!holeId || strip === null) return null; // nothing selected, or genuinely unavailable - stay out of the way

  return (
    <div className={`core-strip-panel ${className}`}>
      {strip === undefined ? (
        <p className="hint">Loading core photo…</p>
      ) : (
        <>
          <p className="section-title">
            Core photo · {holeId} · {strip.depth_min_m.toFixed(0)}-{strip.depth_max_m.toFixed(0)} m
          </p>
          <div className="core-strip-sheets">
            {strip.sheets.map((sheet) => (
              <div className="core-strip-sheet" key={sheet.sheet_index}>
                <div className="core-strip-sheet-label mono">
                  {sheet.depth_from_m.toFixed(1)}-{sheet.depth_to_m.toFixed(1)} m
                </div>
                <div className="core-strip-pair">
                  <img src={MEDIA_BASE + sheet.tsg_url} alt={`TSG mineral, ${sheet.depth_from_m}-${sheet.depth_to_m} m`} />
                  <img src={MEDIA_BASE + sheet.photo_url} alt={`Core photo, ${sheet.depth_from_m}-${sheet.depth_to_m} m`} />
                </div>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  );
}

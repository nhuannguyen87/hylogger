"use client";

// The real, continuous core photo (+ matching TSG mineral-colour strip),
// built offline by core_strip.py/mineral_strip.py from real GSWA/NVCL tray
// photos - see those files at the repo root for how a photo becomes this.
// Any hole with NVCL tray photos gets one: the first view makes the backend
// build it from those photos (~1 min, polled here), later views are instant.
// A hole with none - or whose photos are another hole's (views.py's
// photos_from_elsewhere) - says so in a line, not an error.
//
// A full hole is split into depth-bounded "sheets" (core_strip.py's own
// doc explains why: an unbroken column would be over a million pixels
// tall). Each sheet keeps its own internal scroll so a 400m hole doesn't
// need one impossibly tall scrollbar.

import { useEffect, useState } from "react";
import { getCoreStrip } from "@/lib/api";
import { MEDIA_BASE } from "@/config";

const POLL_MS = 5000; // while the backend builds a strip (~1 min, first view only)

export default function CoreStripPanel({ holeId, className = "" }) {
  const [strip, setStrip] = useState(undefined);

  useEffect(() => {
    setStrip(undefined);
    if (!holeId) return;
    let cancelled = false;
    let timer;
    const load = () => getCoreStrip(holeId)
      .then((data) => {
        if (cancelled) return;
        setStrip(data);
        if (data?.building) timer = setTimeout(load, POLL_MS);
      })
      .catch(() => !cancelled && setStrip(null));
    load();
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [holeId]);

  if (!holeId || strip === null) return null; // nothing selected, or the API failed - stay out of the way

  return (
    <div className={`core-strip-panel ${className}`}>
      {strip === undefined ? (
        <p className="hint">Loading core photo…</p>
      ) : strip.building ? (
        <p className="hint">
          Building {holeId}&apos;s core photo from its NVCL tray photos - first time only, about a minute…
        </p>
      ) : strip.unavailable ? (
        <p className="hint">No core photo for {holeId}: {strip.unavailable}</p>
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
                  {/* lazy: a deep hole has 100+ sheets, each up to 20,000 px tall - fetch only those scrolled into view */}
                  {sheet.tsg_url && (
                    <img loading="lazy" src={MEDIA_BASE + sheet.tsg_url} alt={`TSG mineral, ${sheet.depth_from_m}-${sheet.depth_to_m} m`} />
                  )}
                  <img loading="lazy" src={MEDIA_BASE + sheet.photo_url} alt={`Core photo, ${sheet.depth_from_m}-${sheet.depth_to_m} m`} />
                </div>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  );
}

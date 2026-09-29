"use client";

// The "barcode" down a hole: one coloured band per depth interval.
//
// Three states, and the difference between them is the whole point:
//   confident   -> solid mineral colour
//   uncertain   -> grey with diagonal hatching (we do NOT pretend to know)
//   flagged     -> an amber tick in the margin next to the interval
//
// It is plain SVG. If you want to change the look, everything is right here.

import { useState } from "react";
import {
  ANOMALY_COLOUR,
  CONFIDENCE_THRESHOLD,
  LOW_CONFIDENCE_COLOUR,
  mineralColour,
} from "@/config";

export default function StripLog({
  measurements = [],
  maxDepth,
  height = 460,
  width = 46,
  accent = "var(--accent)",
  label = "",
}) {
  const [hover, setHover] = useState(null);

  if (!measurements.length) {
    return <p className="hint">No depth log for this hole.</p>;
  }

  const bottom = maxDepth || Math.max(...measurements.map((m) => m.depth_to_m));
  const toY = (depth) => (depth / bottom) * height;

  // depth ruler every 20 m, or whatever divides the hole into ~8 marks
  const step = niceStep(bottom);
  const ticks = [];
  for (let depth = 0; depth <= bottom; depth += step) ticks.push(depth);

  const gutter = 44; // room for depth labels on the left
  const margin = 14; // room for anomaly ticks on the right

  return (
    <div style={{ position: "relative" }}>
      {label && <p className="section-title" style={{ color: accent }}>{label}</p>}

      <svg
        width={gutter + width + margin}
        height={height + 16}
        role="img"
        aria-label={`Mineral log to ${bottom.toFixed(0)} metres`}
      >
        <defs>
          {/* diagonal hatching = "we don't trust this reading" */}
          <pattern id="uncertain" width="6" height="6" patternTransform="rotate(45)"
                   patternUnits="userSpaceOnUse">
            <rect width="6" height="6" fill={LOW_CONFIDENCE_COLOUR} />
            <line x1="0" y1="0" x2="0" y2="6" stroke="#5a6672" strokeWidth="2" />
          </pattern>
        </defs>

        <g transform="translate(0, 8)">
          {/* depth ruler */}
          {ticks.map((depth) => (
            <g key={depth}>
              <text x={gutter - 8} y={toY(depth) + 4} textAnchor="end"
                    fill="var(--text-dim)" fontSize="10" fontFamily="var(--mono)">
                {depth}
              </text>
              <line x1={gutter - 5} y1={toY(depth)} x2={gutter} y2={toY(depth)}
                    stroke="var(--line)" />
            </g>
          ))}

          {/* the intervals */}
          {measurements.map((m, index) => {
            const y = toY(m.depth_from_m);
            const bandHeight = Math.max(1, toY(m.depth_to_m) - y);
            const uncertain =
              m.quality_flag === "missing" || m.confidence < CONFIDENCE_THRESHOLD;

            return (
              <g key={index}>
                <rect
                  x={gutter}
                  y={y}
                  width={width}
                  height={bandHeight}
                  fill={uncertain ? "url(#uncertain)" : mineralColour(m.mineral_1)}
                  onMouseEnter={(event) =>
                    setHover({ m, x: event.clientX, y: event.clientY })
                  }
                  onMouseMove={(event) =>
                    setHover({ m, x: event.clientX, y: event.clientY })
                  }
                  onMouseLeave={() => setHover(null)}
                />
                {m.is_anomaly && (
                  <rect
                    x={gutter + width + 3}
                    y={y}
                    width={7}
                    height={Math.max(2, bandHeight)}
                    fill={ANOMALY_COLOUR}
                  />
                )}
              </g>
            );
          })}

          <rect x={gutter} y={0} width={width} height={height}
                fill="none" stroke="var(--line)" />
        </g>
      </svg>

      {hover && (
        <div className="tooltip" style={{ left: hover.x + 14, top: hover.y - 10 }}>
          {hover.m.depth_from_m}–{hover.m.depth_to_m} m
          <br />
          {hover.m.mineral_1 || "no mineral logged"}
          {hover.m.mineral_1_pct ? ` ${(hover.m.mineral_1_pct * 100).toFixed(0)}%` : ""}
          <br />
          confidence {hover.m.confidence.toFixed(2)} · {hover.m.quality_flag}
          {hover.m.why && (
            <>
              <br />
              <span style={{ color: "var(--text-dim)" }}>{hover.m.why}</span>
            </>
          )}
          {hover.m.is_anomaly && (
            <>
              <br />
              <span style={{ color: ANOMALY_COLOUR }}>
                unusual · score {hover.m.anomaly_score?.toFixed(2)}
              </span>
            </>
          )}
        </div>
      )}
    </div>
  );
}

function niceStep(bottom) {
  const rough = bottom / 8;
  return [5, 10, 20, 25, 50, 100, 200].find((step) => step >= rough) || 500;
}

"use client";

// Real core-tray photos, where we have them - each tray got its own
// independent mineral call from two different scans: SWIR/VNIR (reflected
// light, good for clays/micas/carbonates) and TIR (thermal infrared, good
// for quartz/feldspar and other anhydrous silicates SWIR/VNIR can't see).
// Showing both, side by side, instead of picking one is itself an honesty
// choice - the two scans sometimes disagree, and collapsing that away would
// hide real information.

export default function CoreTrays({ trays = [] }) {
  if (!trays.length) return null;

  return (
    <>
      <p className="section-title" style={{ marginTop: 22 }}>
        Core trays ({trays.length})
      </p>
      <div className="tray-strip">
        {trays.map((tray) => (
          <div className="tray-card" key={tray.tray_no}>
            {tray.image_url ? (
              <img
                src={tray.image_url}
                alt={`Tray ${tray.tray_no}, ${tray.depth_from_m}-${tray.depth_to_m} m`}
                loading="lazy"
              />
            ) : (
              <div className="tray-card-noimg">no photo</div>
            )}
            <div className="tray-card-depth mono">
              {tray.depth_from_m.toFixed(1)}-{tray.depth_to_m.toFixed(1)} m
            </div>
            <div className="tray-card-scan">
              <span className="tray-card-label">SWIR/VNIR</span>
              <span>{tray.swir_vnir_mineral || "-"}</span>
            </div>
            <div className="tray-card-scan">
              <span className="tray-card-label">TIR</span>
              <span>{tray.tir_mineral || "-"}</span>
            </div>
          </div>
        ))}
      </div>
    </>
  );
}

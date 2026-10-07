import { useState } from "react";

// A box floating on the map that minimises to its header row with "−", giving
// the map its room back. `head` stays visible (and usable) either way; with
// nothing to hide there's no button.
export default function MapPanel({ head, label, className = "", children }) {
  const [open, setOpen] = useState(true);
  return (
    <div className={`map-panel ${className}`}>
      <div className="panel-head">
        {head}
        {children && (
          <button
            type="button"
            className="panel-toggle"
            aria-expanded={open}
            aria-label={`${open ? "Minimise" : "Expand"} ${label}`}
            title={open ? "Minimise" : "Expand"}
            onClick={() => setOpen(!open)}
          >
            {open ? "−" : "+"}
          </button>
        )}
      </div>
      {open && children}
    </div>
  );
}

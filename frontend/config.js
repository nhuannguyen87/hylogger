// ---------------------------------------------------------------------------
// START HERE if you want to change how the site looks or behaves.
// Almost everything you'd want to tweak lives in this one file.
// ---------------------------------------------------------------------------

// Where the Django API is. Override with NEXT_PUBLIC_API_BASE in .env.local.
export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8000/api";

// Basemap. This one is free and needs no key, but it is very plain.
// Nicer options (free tier, needs a key):
//   https://api.maptiler.com/maps/topo-v2/style.json?key=YOUR_KEY
//   https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json  (no key)
export const MAP_STYLE = "https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json";

// Where the map opens. Roughly the middle of the WA goldfields.
export const MAP_START = { longitude: 119.5, latitude: -29.2, zoom: 4.4 };

// Below this confidence, an interval is drawn greyed and hatched instead of
// coloured. The brief is explicit about this: never hide a gap, show it.
export const CONFIDENCE_THRESHOLD = 0.5;

// One colour per mineral. Add your own as your ETL produces more names -
// anything not listed falls back to UNKNOWN_COLOUR.
export const MINERAL_COLOURS = {
  Quartz: "#e8e3d8",
  Kaolinite: "#c9a227",
  Muscovite: "#7fb069",
  Chlorite: "#2f9e8f",
  Hematite: "#c0453b",
  Carbonate: "#6a8fd8",
};

export const UNKNOWN_COLOUR = "#4a5560"; // no mineral logged
export const LOW_CONFIDENCE_COLOUR = "#39424c"; // greyed-out fill
export const ANOMALY_COLOUR = "#ffb347";

export function mineralColour(name) {
  if (!name) return UNKNOWN_COLOUR;
  return MINERAL_COLOURS[name] || UNKNOWN_COLOUR;
}

/** deck.gl wants colours as [r, g, b] arrays, not hex strings. */
export function toRgb(hex) {
  const clean = hex.replace("#", "");
  return [
    parseInt(clean.slice(0, 2), 16),
    parseInt(clean.slice(2, 4), 16),
    parseInt(clean.slice(4, 6), 16),
  ];
}

// How much to stretch depth in the 3D view. Real holes are hair-thin compared
// to the distances between them, so 1:1 looks like nothing at all.
export const DEFAULT_VERTICAL_EXAGGERATION = 25;

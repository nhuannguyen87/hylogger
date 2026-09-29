// ---------------------------------------------------------------------------
// START HERE if you want to change how the site looks or behaves.
// Almost everything you'd want to tweak lives in this one file.
// ---------------------------------------------------------------------------

// Where the Django API is. Override with NEXT_PUBLIC_API_BASE in .env.local.
export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8000/api";

// Django's MEDIA_URL is host-relative (e.g. "/media/tray_images/..."), so it
// needs the API's own origin prepended - there's no Next.js rewrite proxying
// /media, and a bare "/media/..." would otherwise resolve against the
// Next.js dev server on :3000, not Django on :8000.
export const MEDIA_BASE = API_BASE.replace(/\/api\/?$/, "");

// Basemap: satellite by default, with a plain streets layer as the alternative -
// same two free, no-API-key raster sources as the wa-drillhole-map/drillcore-viewer
// reference projects (Esri World Imagery + CARTO light), so switching between them
// is just toggling layer visibility (see HoleMap.jsx) rather than reloading the
// whole map style. MapTiler's topo-v2 style is another option if you have a key:
//   https://api.maptiler.com/maps/topo-v2/style.json?key=YOUR_KEY
export const MAP_STYLE = {
  version: 8,
  sources: {
    satellite: {
      type: "raster",
      tiles: ["https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"],
      tileSize: 256,
      maxzoom: 19,
      attribution: "Imagery &copy; Esri, Maxar, Earthstar Geographics",
    },
    light: {
      type: "raster",
      tiles: ["https://a.basemaps.cartocdn.com/light_all/{z}/{x}/{y}.png"],
      tileSize: 256,
      attribution: "&copy; OpenStreetMap contributors &copy; CARTO",
    },
  },
  layers: [
    { id: "satellite", type: "raster", source: "satellite", paint: { "raster-saturation": -0.35 } },
    { id: "light", type: "raster", source: "light", layout: { visibility: "none" } },
  ],
};

// Tuned for the WA-wide map view (holes spread over 100s of km): higher than
// Hole3D.jsx's DEFAULT_VERTICAL_EXAGGERATION because you're usually much further
// zoomed out here than when comparing one or two holes underground.
export const MAP_3D_DEFAULT_EXAGGERATION = 15;
export const MAP_3D_DEFAULT_CORE_WIDTH_M = 25;

// Where the map opens. Roughly the middle of the WA goldfields.
export const MAP_START = { longitude: 119.5, latitude: -29.2, zoom: 4.4 };

// Below this confidence, an interval is drawn greyed and hatched instead of
// coloured. The brief is explicit about this: never hide a gap, show it.
export const CONFIDENCE_THRESHOLD = 0.5;

// One colour per mineral GROUP - these are the exact group names
// files/extract.py's MIN2GRP produces (real TSA/HyLogger mineral-group
// vocabulary, not raw mineral species). Anything not listed - including a
// group name TSG returns directly, like plain QUARTZ - falls back to
// UNKNOWN_COLOUR, so a new group never silently renders invisible.
export const MINERAL_COLOURS = {
  KAOLIN: "#c9a227",
  "WHITE-MICA": "#7fb069",
  "DARK-MICA": "#5c7a4a",
  CHLORITE: "#2f9e8f",
  CARBONATE: "#6a8fd8",
  SULPHATE: "#d8a6e0",
  EPIDOTE: "#8fae4a",
  AMPHIBOLE: "#4a7a8f",
  SERPENTINE: "#3f8f6a",
  SMECTITE: "#b08050",
  TOURMALINE: "#2f2f3f",
  "OTHER-MGOH": "#9a9a5a",
  QUARTZ: "#e8e3d8",
  HEMATITE: "#c0453b",
  OTHER: "#8c9aa5",
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

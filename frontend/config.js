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

// Basemap: Esri World Imagery (free, no API key). Near full brightness, only
// lightly desaturated - enough that the saturated mineral colours on top
// still stand out from the ground, without the whole map going murky.
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
  },
  layers: [
    {
      id: "satellite", type: "raster", source: "satellite",
      paint: { "raster-saturation": -0.2, "raster-brightness-max": 0.95, "raster-brightness-min": 0.06 },
    },
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
// group name TSG returns directly, like PAL-SEP - falls back to
// UNKNOWN_COLOUR, so a new group never silently renders invisible.
// mineral_strip.py keeps a copy for the core-photo strip - change both.
// Saturated and far apart in hue so each strip reads at a glance on the dark
// satellite basemap; the most common groups (top of the list, by logged
// metres) get the most distinct hues. None is grey, so a colour can never be
// mistaken for "uncertain" or "not logged".
export const MINERAL_COLOURS = {
  CHLORITE: "#22c55e",
  "WHITE-MICA": "#fde047",
  "DARK-MICA": "#ef4444",
  AMPHIBOLE: "#38bdf8",
  CARBONATE: "#6366f1",
  KAOLIN: "#f472b6",
  SERPENTINE: "#14b8a6",
  "OTHER-MGOH": "#a3e635",
  SMECTITE: "#c2410c",
  SULPHATE: "#c084fc",
  EPIDOTE: "#4d7c0f",
  TOURMALINE: "#1e40af",
  QUARTZ: "#f5f5f4",
  HEMATITE: "#9f1239",
  OTHER: "#8c9aa5",
  // Groups the thermal-infrared (TIR) classification calls - the silicates
  // SWIR can't see. OTHER-ALOH turns up in TSA SWIR logs too. SILICA is
  // TIR's quartz group and OXIDE hematite's, so they share those colours.
  SILICA: "#f5f5f4",
  OXIDE: "#9f1239",
  PLAGIOCLASE: "#7c3aed",
  "K-FELDSPAR": "#fda4af",
  PYROXENE: "#0891b2",
  OLIVINE: "#bef264",
  "OTHER-ALOH": "#d946ef",
  PHOSPHATE: "#e11d48",
  GARNET: "#965970",
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

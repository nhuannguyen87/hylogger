// Every call to the FastAPI backend (hylogger/backend, docs/FRONTEND_API.md)
// goes through here. FastAPI serves raw ETL4: one sample every few mm, keyed
// by dataset revision + axis. The components were written against one-metre
// intervals, so this file turns the one into the other and the components
// stay as they are.

import { API_BASE } from "@/config";

async function get(path) {
  const response = await fetch(`${API_BASE}${path}`, { cache: "no-store" });
  if (!response.ok) {
    throw new Error(
      `${response.status} from ${path}. Is the FastAPI server running on ${API_BASE}?`
    );
  }
  return response.json();
}

const qs = (params) => {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value == null) continue;
    for (const v of [].concat(value)) query.append(key, String(v));
  }
  return query.toString();
};

/** Run fn over items, at most `limit` at once (the browser and FastAPI both choke on 300 at once). */
async function pool(items, limit, fn) {
  const out = new Array(items.length);
  let next = 0;
  const worker = async () => {
    while (next < items.length) {
      const i = next++;
      out[i] = await fn(items[i]);
    }
  };
  await Promise.all(Array.from({ length: Math.min(limit, items.length) }, worker));
  return out;
}

// One promise per key, kept for the session - opening a hole twice, or the
// map and the detail panel both asking, costs one round of requests.
const cache = new Map();
function once(key, fn) {
  if (!cache.has(key)) {
    const promise = fn();
    promise.catch(() => cache.delete(key)); // let a failed load retry
    cache.set(key, promise);
  }
  return cache.get(key);
}

// ---------------------------------------------------------------- holes

/** /v1/boreholes rows -> the hole shape the components use. */
function toHole(row) {
  const [longitude, latitude] = row.geometry?.coordinates ?? [null, null];
  return {
    hole_id: row.hole_id,
    hole_name: row.project || row.hole_id,
    latitude,
    longitude,
    elevation_m: null, // not in ETL4
    borehole_length_m: row.reported_length_m,
    drawn_length_m: row.scan_to_m ?? row.reported_length_m,
    logged_from_m: row.scan_from_m,
    logged_to_m: row.scan_to_m,
    // ETL4 withholds orientation for confidentiality; null keeps the map from
    // drawing a made-up trajectory (desurvey.js skips holes without one).
    inclination_deg: null,
    azimuth_deg: null,
    confidential: !!row.orientation_missing_reason,
    has_full_spectrum: true, // every ETL4 hole carries VSWIR/TIR spectra
    instrument: row.instrument,
  };
}

const allHoles = () =>
  once("holes", async () => {
    const { items } = await get("/v1/boreholes");
    const byId = new Map();
    for (const row of items) if (!byId.has(row.hole_id)) byId.set(row.hole_id, toHole(row));
    return [...byId.values()];
  });

export const getHoles = async ({ search = "", anomaliesOnly = false } = {}) => {
  const needle = search.trim().toLowerCase();
  let holes = (await allHoles()).filter(
    (h) => !needle || h.hole_id.toLowerCase().includes(needle) || h.hole_name.toLowerCase().includes(needle)
  );
  if (anomaliesOnly) {
    const flagged = await Promise.all(holes.map((h) => holeAnomalies(h.hole_id).then((a) => a.length > 0, () => false)));
    holes = holes.filter((_, i) => flagged[i]);
  }
  return holes;
};

export const getHole = async (holeId) => {
  const hole = (await allHoles()).find((h) => h.hole_id === holeId);
  if (!hole) throw new Error(`${holeId} is not in the active ETL4 release.`);
  const measurements = await getMeasurements(holeId);
  return {
    ...hole,
    measurement_count: measurements.length,
    anomaly_count: measurements.filter((m) => m.is_anomaly).length,
    logged_from_m: measurements[0]?.depth_from_m ?? hole.logged_from_m,
    logged_to_m: measurements.at(-1)?.depth_to_m ?? hole.logged_to_m,
  };
};

// ------------------------------------------------------- dataset context

/**
 * The hole's datasets spliced by depth - e.g. 15EIS001, then _wedge, _wedge2:
 * FastAPI returns them in splice order, and each one is used from its own
 * start depth until the next one starts. Each part carries its revision,
 * sample axis, log catalogue and [from, to) depth window.
 */
const context = (holeId) =>
  once(`ctx:${holeId}`, async () => {
    const { items } = await get(`/v1/boreholes/${encodeURIComponent(holeId)}/datasets`);
    const datasets = items.filter((d) => d.axis_id);
    if (!datasets.length) throw new Error(`${holeId} has no sample axis in ETL4.`);
    return Promise.all(datasets.map(async (d, i) => {
      const { items: logs } = await get(`/v1/datasets/${d.dataset_revision_id}/logs`);
      return {
        part: i,
        revision: d.dataset_revision_id,
        axis: d.axis_id,
        sampleCount: d.sample_count,
        logs,
        from: i === 0 ? -Infinity : d.depth_min_m,
        to: datasets[i + 1]?.depth_min_m ?? Infinity,
      };
    }));
  });

const inPart = (part, depth) => depth >= part.from && depth < part.to;

const usable = (log) => log.availability_status === "payload_present" && log.axis_binding_status === "verified";

/** The mineral-GROUP log to colour by: Grp1, SWIR/VNIR unless asked for TIR. */
function groupLog(logs, region) {
  const groups = logs.filter(
    (l) => l.log_kind === "scalar" && l.metric_key === "mineral_name" && usable(l) && /^grp\s*1/i.test(l.source_log_name)
  );
  const isTir = (l) => /TIR/i.test(l.output_region || "") || /TSAT/i.test(l.source_log_name);
  return region === "TIR" ? groups.find(isTir) : groups.find((l) => !isTir(l));
}

/** Every sample of one scalar log, paging through after_sample. */
async function logValues({ revision, axis }, logId) {
  const rows = [];
  let after = -1;
  for (;;) {
    const page = await get(
      `/v1/datasets/${revision}/logs/${logId}/values?${qs({ axis_id: axis, after_sample: after, limit: 5000 })}`
    );
    rows.push(...page.items);
    if (page.next_after_sample == null) return rows;
    after = page.next_after_sample;
  }
}

/** "White Mica" -> "WHITE-MICA", the config.js MINERAL_COLOURS key. Aspectral = no mineral seen. */
function groupName(row) {
  if (row.status !== "available" || !row.value_text) return null;
  const name = row.value_text.trim().toUpperCase().replace(/[\s_]+/g, "-");
  return name === "ASPECTRAL" || name === "NULL" ? null : name;
}

/** One group log's samples over the spliced hole, each tagged with its dataset `part`. */
const groupSamples = (holeId, region = "SWIR") =>
  once(`grp:${holeId}:${region}`, async () => {
    const parts = await Promise.all((await context(holeId)).map(async (part) => {
      const log = groupLog(part.logs, region);
      const rows = log ? await logValues(part, log.log_id) : [];
      return rows.filter((r) => r.depth_m != null && inPart(part, r.depth_m)).map((r) => ({ ...r, part: part.part }));
    }));
    return parts.flat();
  });

// ------------------------------------------------------- measurements

/** Every page of a paged (offset/next_offset) list endpoint. */
async function allPages(path, params) {
  const items = [];
  let offset = 0;
  while (offset != null) {
    const page = await get(`${path}?${qs({ ...params, offset, limit: 1000 })}`);
    items.push(...page.items);
    offset = page.next_offset;
  }
  return items;
}

const holeAnomalies = (holeId) =>
  once(`anom:${holeId}`, async () => {
    const parts = await Promise.all((await context(holeId)).map(async (part) => {
      const items = await allPages(`/v1/datasets/${part.revision}/anomalies`, { axis_id: part.axis, flag: "high" });
      return items.filter((a) => a.depth_to_m > part.from && a.depth_from_m < part.to);
    }));
    return parts.flat();
  });

/** Most common value and its count. */
function dominant(values) {
  const counts = new Map();
  for (const v of values) if (v) counts.set(v, (counts.get(v) || 0) + 1);
  let best = null, n = 0;
  for (const [v, c] of counts) if (c > n) [best, n] = [v, c];
  return [best, n];
}

/**
 * Raw samples -> one interval per metre. confidence is the share of that
 * metre's samples agreeing with its dominant group - an honest read of how
 * mixed the metre is, NOT the Process-Level confidence FRONTEND_API.md §15
 * says is still to come. ponytail: swap for confidence_level once FastAPI exposes it.
 */
export const getMeasurements = (holeId) =>
  once(`meas:${holeId}`, async () => {
    const [samples, anomalies] = await Promise.all([groupSamples(holeId), holeAnomalies(holeId).catch(() => [])]);
    const metres = new Map();
    for (const s of samples) {
      if (s.depth_m == null) continue;
      const m = Math.floor(s.depth_m);
      if (!metres.has(m)) metres.set(m, []);
      metres.get(m).push(groupName(s));
    }
    return [...metres.keys()].sort((a, b) => a - b).map((from) => {
      const calls = metres.get(from);
      const [mineral, n] = dominant(calls);
      const flag = anomalies.find((a) => a.depth_from_m < from + 1 && a.depth_to_m > from);
      return {
        depth_from_m: from,
        depth_to_m: from + 1,
        mineral_1: mineral,
        mineral_1_pct: mineral ? n / calls.length : null,
        confidence: n / calls.length,
        quality_flag: mineral ? "ok" : "missing",
        is_anomaly: !!flag,
        anomaly_score: flag?.anomaly_score ?? null, // 0-100 percentile, not a probability
        why: flag?.why ?? null,
      };
    });
  });

export const getAnomalies = async (holeId) => (await getMeasurements(holeId)).filter((m) => m.is_anomaly);

/** Every hole's log as merged [from_m, to_m, code] runs, code indexing `groups` (null = uncertain). */
export const getMineralLogs = async (minConfidence) => {
  const holes = await allHoles();
  const logs = await Promise.all(holes.map((h) => getMeasurements(h.hole_id).catch(() => [])));
  const groups = [];
  const code = (g) => (groups.includes(g) ? groups.indexOf(g) : groups.push(g) - 1);
  const result = {};
  holes.forEach((hole, i) => {
    const runs = [];
    for (const m of logs[i]) {
      const c = m.mineral_1 && m.confidence >= minConfidence ? code(m.mineral_1) : null;
      const last = runs.at(-1);
      if (last && last[2] === c && last[1] === m.depth_from_m) last[1] = m.depth_to_m;
      else runs.push([m.depth_from_m, m.depth_to_m, c]);
    }
    result[hole.hole_id] = runs;
  });
  return { groups, holes: result };
};

export const getStats = async () => {
  const holes = await allHoles();
  const logs = await Promise.all(holes.map((h) => getMeasurements(h.hole_id).catch(() => [])));
  return {
    holes: holes.length,
    measurements: logs.reduce((n, l) => n + l.length, 0),
    anomalies: logs.reduce((n, l) => n + l.filter((m) => m.is_anomaly).length, 0),
  };
};

// ------------------------------------------------------- where holes are

function haversineKm(a, b) {
  const rad = Math.PI / 180;
  const dLat = (b.latitude - a.latitude) * rad;
  const dLon = (b.longitude - a.longitude) * rad;
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(a.latitude * rad) * Math.cos(b.latitude * rad) * Math.sin(dLon / 2) ** 2;
  return 2 * 6371.0088 * Math.asin(Math.sqrt(h));
}

export const getNearby = async (holeId, km = 25) => {
  const hole = (await allHoles()).find((h) => h.hole_id === holeId);
  if (!hole) return [];
  const { items } = await get(
    `/v1/boreholes/nearby?${qs({ latitude: hole.latitude, longitude: hole.longitude, radius_km: km, limit: 20 })}`
  );
  const seen = new Set([holeId]);
  return items
    .filter((row) => !seen.has(row.hole_id) && seen.add(row.hole_id))
    .map((row) => ({ hole_id: row.hole_id, hole_name: row.project, distance_km: row.distance_km }));
};

export const getDistance = async (a, b) => {
  const holes = await allHoles();
  const [ha, hb] = [a, b].map((id) => holes.find((h) => h.hole_id === id));
  if (!ha || !hb) throw new Error(`Unknown hole ${!ha ? a : b}`);
  return { a, b, distance_km: haversineKm(ha, hb) };
};

// ------------------------------------------------------- trays & photos

/** Interval rows (tray or section) of every spliced part, with depths read off the sample axis. */
const intervals = (holeId, kind) =>
  once(`int:${holeId}:${kind}`, async () => {
    const [parts, samples] = await Promise.all([context(holeId), groupSamples(holeId)]);
    // the group log holds every (spliced-in) sample's depth, so no extra
    // /samples paging - and an interval whose start was spliced out drops out
    const depth = new Map(samples.map((s) => [`${s.part}:${s.sample_no}`, s.depth_m]));
    const lists = await Promise.all(parts.map(async (part) => {
      const items = await allPages(`/v1/datasets/${part.revision}/intervals`, { axis_id: part.axis, kind });
      return items.map((it) => ({
        ...it,
        part: part.part,
        depth_from_m: depth.get(`${part.part}:${it.sample_no_from}`),
        depth_to_m: depth.get(`${part.part}:${it.sample_no_to}`) ?? part.to, // ends past the splice cutoff: clip to it
      }));
    }));
    return lists.flat().filter((it) => it.depth_from_m != null && Number.isFinite(it.depth_to_m));
  });

const callsBetween = (rows, { part, sample_no_from, sample_no_to }) =>
  rows.filter((s) => s.part === part && s.sample_no >= sample_no_from && s.sample_no <= sample_no_to).map(groupName);

export const getTrays = async (holeId) => {
  const [trays, swir, tir] = await Promise.all([
    intervals(holeId, "tray"),
    groupSamples(holeId, "SWIR"),
    groupSamples(holeId, "TIR"),
  ]);
  return trays.map((tray, i) => ({
    tray_no: i + 1,
    depth_from_m: tray.depth_from_m,
    depth_to_m: tray.depth_to_m,
    image_url: null, // ETL4 serves per-row crops, not whole-tray photos; those are in the core strip
    swir_vnir_mineral: dominant(callsBetween(swir, tray))[0],
    tir_mineral: dominant(callsBetween(tir, tray))[0],
  }));
};

/**
 * ETL4's core photo: one ~400x25 px crop per core section (tray row), depth
 * running left to right along it. Returned in core_strip.py's sheet/row shape,
 * one sheet per row (photo_url relative to config.MEDIA_BASE), with `rotate` so Hole3D/CoreStripPanel stand it upright.
 * ponytail: one exact-sample call per section (~270 a hole); a FastAPI
 * /core-strip endpoint returning all mappings at once would make this one call.
 */
export const getCoreStrip = (holeId) =>
  once(`strip:${holeId}`, async () => {
    const [parts, sections] = await Promise.all([context(holeId), intervals(holeId, "section")]);
    // any one log keeps the exact-sample call cheap - the images come back regardless
    const anyLog = parts.map((p) => groupLog(p.logs, "SWIR") || p.logs.find(usable));
    if (!sections.length) return { unavailable: "ETL4 has no core-section photos for this hole." };

    const found = await pool(sections, 8, async (section) => {
      const part = parts[section.part];
      const log = anyLog[section.part];
      if (!log) return null;
      const sample = await get(
        `/v1/datasets/${part.revision}/samples/${section.sample_no_from}?${qs({ axis_id: part.axis, log_ids: log.log_id })}`
      ).catch(() => null);
      const image = sample?.images?.find((im) => im.status === "available" && im.image_asset_id);
      return image && { section, image };
    });

    const rows = found.filter(Boolean).map(({ section, image }, i) => ({
      sheet_index: i,
      depth_from_m: section.depth_from_m,
      depth_to_m: section.depth_to_m,
      // after rotating, depth runs down the image's width
      y_from_px: 0,
      y_to_px: image.width_px,
      rotate: true,
      flip: /right.?to.?left|decreas/i.test(image.source_depth_direction || ""),
      photo_url: `/v1/image-assets/${image.image_asset_id}/content`,
      height_px: image.height_px,
    }));
    if (!rows.length) return { unavailable: "ETL4 has no reviewed core-section photos for this hole." };

    return {
      depth_min_m: Math.min(...rows.map((r) => r.depth_from_m)),
      depth_max_m: Math.max(...rows.map((r) => r.depth_to_m)),
      core_width_px: Math.min(...rows.map((r) => r.height_px)),
      rows,
      sheets: rows.map((r) => ({
        sheet_index: r.sheet_index,
        depth_from_m: r.depth_from_m,
        depth_to_m: r.depth_to_m,
        photo_url: r.photo_url,
        tsg_url: null,
      })),
    };
  });

// ------------------------------------------------------- spectra

/** Real VSWIR/TIR spectrum + mineral calls at one sample (nearest to depthM, else mid-hole with a call). */
export const getSpectralSample = async (holeId, depthM) => {
  const parts = await context(holeId).catch(() => null);
  if (!parts) return null;
  const samples = await groupSamples(holeId);
  if (!samples.length) return null;

  let pick;
  if (depthM != null) {
    pick = samples.reduce((best, s) => (Math.abs(s.depth_m - depthM) < Math.abs(best.depth_m - depthM) ? s : best));
  } else {
    // the sample with a real group call closest to mid-hole
    const mid = Math.floor(samples.length / 2);
    let best = mid;
    for (let d = 0; d <= mid; d += 1) {
      if (samples[mid + d] && groupName(samples[mid + d])) { best = mid + d; break; }
      if (samples[mid - d] && groupName(samples[mid - d])) { best = mid - d; break; }
    }
    pick = samples[best];
  }

  const ctx = parts[pick.part];
  const logIds = ctx.logs
    .filter((l) => usable(l) && (l.log_kind === "spectral" || (l.log_kind === "scalar" && l.metric_key === "mineral_name")))
    .map((l) => l.log_id);
  const raw = await get(`/v1/datasets/${ctx.revision}/samples/${pick.sample_no}?${qs({ axis_id: ctx.axis, log_ids: logIds })}`);

  const ok = (r) => r.status === "available";
  return {
    sample_no: raw.sample_no,
    sample_count: raw.sample_count,
    md_m: raw.md_m,
    source: "etl4",
    minerals: raw.results
      .filter((r) => ok(r) && r.log_kind === "scalar" && r.value?.value_text)
      .map((r) => ({ log_name: r.source_log_name, mineral: r.value.value_text })),
    spectra: raw.results
      .filter((r) => ok(r) && r.log_kind === "spectral" && r.spectra?.length)
      .map((r) => ({ region: r.region_code, wavelength: r.wavelength, values: r.spectra, wavelength_unit: r.wavelength_unit || "nm" })),
  };
};

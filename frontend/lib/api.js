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

/** The hole's dataset revision + sample axis + log catalogue - every later call needs them. */
const context = (holeId) =>
  once(`ctx:${holeId}`, async () => {
    const { items } = await get(`/v1/boreholes/${encodeURIComponent(holeId)}/datasets`);
    const dataset = items.filter((d) => d.axis_id).sort((a, b) => b.sample_count - a.sample_count)[0];
    if (!dataset) throw new Error(`${holeId} has no sample axis in ETL4.`);
    const { items: logs } = await get(`/v1/datasets/${dataset.dataset_revision_id}/logs`);
    return { revision: dataset.dataset_revision_id, axis: dataset.axis_id, sampleCount: dataset.sample_count, logs };
  });

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

const groupSamples = (holeId, region = "SWIR") =>
  once(`grp:${holeId}:${region}`, async () => {
    const ctx = await context(holeId);
    const log = groupLog(ctx.logs, region);
    return log ? logValues(ctx, log.log_id) : [];
  });

// ------------------------------------------------------- measurements

const holeAnomalies = (holeId) =>
  once(`anom:${holeId}`, async () => {
    const { revision, axis } = await context(holeId);
    const items = [];
    let offset = 0;
    while (offset != null) {
      const page = await get(`/v1/datasets/${revision}/anomalies?${qs({ axis_id: axis, flag: "high", offset, limit: 1000 })}`);
      items.push(...page.items);
      offset = page.next_offset;
    }
    return items;
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

/** Interval rows (tray or section) with depths read off the sample axis. */
const intervals = (holeId, kind) =>
  once(`int:${holeId}:${kind}`, async () => {
    const { revision, axis } = await context(holeId);
    const items = [];
    let offset = 0;
    while (offset != null) {
      const page = await get(`/v1/datasets/${revision}/intervals?${qs({ axis_id: axis, kind, offset, limit: 1000 })}`);
      items.push(...page.items);
      offset = page.next_offset;
    }
    // the group log holds every sample's depth, so no extra /samples paging
    const samples = await groupSamples(holeId);
    const depth = new Map(samples.map((s) => [s.sample_no, s.depth_m]));
    return items
      .map((it) => ({ ...it, depth_from_m: depth.get(it.sample_no_from), depth_to_m: depth.get(it.sample_no_to) }))
      .filter((it) => it.depth_from_m != null && it.depth_to_m != null);
  });

const callsBetween = (rows, from, to) => rows.filter((s) => s.sample_no >= from && s.sample_no <= to).map(groupName);

export const getTrays = async (holeId) => {
  const [trays, swir, tir] = await Promise.all([
    intervals(holeId, "tray"),
    groupSamples(holeId, "SWIR"),
    groupSamples(holeId, "TIR"),
  ]);
  return trays.map((tray) => ({
    tray_no: tray.ordinal + 1,
    depth_from_m: tray.depth_from_m,
    depth_to_m: tray.depth_to_m,
    image_url: null, // ETL4 serves per-row crops, not whole-tray photos; those are in the core strip
    swir_vnir_mineral: dominant(callsBetween(swir, tray.sample_no_from, tray.sample_no_to))[0],
    tir_mineral: dominant(callsBetween(tir, tray.sample_no_from, tray.sample_no_to))[0],
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
    const ctx = await context(holeId);
    const sections = await intervals(holeId, "section");
    const anyLog = groupLog(ctx.logs, "SWIR") || ctx.logs.find(usable);
    if (!sections.length || !anyLog) return { unavailable: "ETL4 has no core-section photos for this hole." };

    const found = await pool(sections, 8, async (section) => {
      const sample = await get(
        `/v1/datasets/${ctx.revision}/samples/${section.sample_no_from}?${qs({ axis_id: ctx.axis, log_ids: anyLog.log_id })}`
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
  const ctx = await context(holeId).catch(() => null);
  if (!ctx) return null;
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

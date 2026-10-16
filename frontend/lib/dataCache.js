// Bump the database name when the cached result format changes.
const DB_NAME = "hylogger-data-v1";
const MAX_BYTES = 128 * 1024 * 1024;
const MAX_ENTRY_BYTES = 2 * 1024 * 1024;
const MAX_ENTRIES = 2048;
const IO_TIMEOUT = 1500;
export const CATALOG_TTL = 5 * 60 * 1000;
export const DATA_TTL = 60 * 60 * 1000;

const memory = new Map();
let connection;
let generation = 0;
let channel;
try {
  if (typeof window !== "undefined" && typeof BroadcastChannel !== "undefined") {
    channel = new BroadcastChannel(DB_NAME);
    channel.onmessage = () => { generation += 1; memory.clear(); };
  }
} catch { /* Storage is optional, including in private browsing. */ }

function openDatabase() {
  if (typeof indexedDB === "undefined") return Promise.resolve(null);
  if (!connection) {
    connection = new Promise((resolve) => {
      let done = false;
      const finish = (db) => {
        if (done) { db?.close(); return; }
        done = true;
        clearTimeout(timer);
        resolve(db);
      };
      const timer = setTimeout(() => finish(null), IO_TIMEOUT);
      try {
        const request = indexedDB.open(DB_NAME, 1);
        request.onupgradeneeded = () => {
          request.result.createObjectStore("entries", { keyPath: "key" });
          request.result.createObjectStore("metadata", { keyPath: "key" });
        };
        request.onsuccess = () => {
          const db = request.result;
          db.onversionchange = () => { db.close(); connection = undefined; };
          finish(db);
        };
        request.onerror = request.onblocked = () => finish(null);
      } catch { finish(null); }
    });
  }
  return connection;
}

// Resolve only after the transaction commits. Quota/permission failures must
// never turn a successful API response into a failed page load.
async function transact(mode, action) {
  const db = await openDatabase();
  if (!db) return undefined;
  return new Promise((resolve) => {
    let tx, result;
    const timer = setTimeout(() => {
      try { tx?.abort(); } catch { /* Already completed. */ }
      resolve(undefined);
    }, IO_TIMEOUT);
    const finish = (value) => { clearTimeout(timer); resolve(value); };
    try {
      tx = db.transaction(["entries", "metadata"], mode);
      tx.oncomplete = () => finish(result);
      tx.onabort = tx.onerror = () => finish(undefined);
      action(tx.objectStore("entries"), tx.objectStore("metadata"), (value) => { result = value; });
    } catch { try { tx?.abort(); } catch { /* No live transaction. */ } finish(undefined); }
  });
}

async function readRecord(key) {
  const record = await transact("readonly", (entries, metadata, result) => {
    const request = entries.get(key);
    request.onsuccess = () => result(request.result);
  });
  if (!record || !Number.isFinite(record.expires) || record.expires <= Date.now()) return null;
  try { return { value: JSON.parse(record.json), expires: record.expires }; }
  catch { return null; }
}

async function writeRecord(key, value, expires, epoch) {
  let json, bytes;
  try {
    json = JSON.stringify(value);
    if (json === undefined) return;
    bytes = new TextEncoder().encode(json).byteLength;
  } catch { return; }
  if (bytes > MAX_ENTRY_BYTES || epoch !== generation) return;
  await transact("readwrite", (entries, metadata) => {
    const request = metadata.getAll();
    request.onsuccess = () => {
      if (epoch !== generation) return;
      const now = Date.now();
      const keep = [];
      const remove = (id) => { entries.delete(id); metadata.delete(id); };
      for (const row of request.result) {
        if (row.key === key || row.expires <= now) remove(row.key);
        else keep.push(row);
      }
      keep.sort((a, b) => a.written - b.written);
      let total = keep.reduce((sum, row) => sum + row.bytes, 0) + bytes;
      while (keep.length && (total > MAX_BYTES || keep.length >= MAX_ENTRIES)) {
        const row = keep.shift();
        total -= row.bytes;
        remove(row.key);
      }
      entries.put({ key, json, expires });
      metadata.put({ key, bytes, expires, written: now });
    };
  });
}

/** Cache complete successful results, sharing simultaneous requests. */
export function cached(key, load, { ttl = DATA_TTL, persist = true } = {}) {
  const existing = memory.get(key);
  if (existing && (existing.pending || existing.expires > Date.now())) return existing.promise;
  const epoch = generation;
  const entry = { pending: true, expires: 0, promise: null };
  entry.promise = (async () => {
    const saved = persist ? await readRecord(key) : null;
    if (saved && epoch === generation) {
      entry.expires = saved.expires;
      return saved.value;
    }
    const value = await load(); // Rejections never reach either cache.
    entry.expires = Date.now() + ttl;
    if (persist && epoch === generation) await writeRecord(key, value, entry.expires, epoch);
    return value;
  })().then((value) => {
    entry.pending = false;
    return value;
  }, (error) => {
    if (memory.get(key) === entry) memory.delete(key);
    throw error;
  });
  memory.set(key, entry);
  // Expired completed entries need not accumulate during long sessions.
  for (const [id, item] of memory) {
    if (!item.pending && item.expires <= Date.now()) memory.delete(id);
  }
  return entry.promise;
}

/** Clear only this app's data, and prevent old in-flight loads repopulating it. */
export async function clearDataCache() {
  generation += 1;
  memory.clear();
  try { channel?.postMessage("clear"); } catch { /* No other tab listening. */ }
  const db = await openDatabase();
  if (!db) return;
  const cleared = await transact("readwrite", (entries, metadata, result) => {
    entries.clear();
    metadata.clear();
    result(true);
  });
  if (!cleared) throw new Error("Could not clear saved data. Please try again.");
}

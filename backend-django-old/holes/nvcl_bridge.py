"""
Live, on-demand real VSWIR/TIR spectra + mineral calls straight from the NVCL
web service, for every hole that ISN'T one of the 5 restored locally in
etl4_bridge.py. No local database, no pre-downloaded files: every call here
talks to the WA government NVCL service directly (a few seconds, not
milliseconds), so results are cached - see fetch_spectral_sample_cached.

Reuses download.py's own NVCLReader setup and on-disk HTTP cache rather than
re-deriving the NVCL API calls from scratch; see download.py's
download_scalars/download_spectra for the bulk-download version of the same
two calls (get_scalar_data, get_spectrallog_datasets) this makes on demand.
"""

import logging
import struct
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from django.conf import settings
from django.core.cache import cache

sys.path.insert(0, str(settings.PROJECT_ROOT))
from common import as_dict  # noqa: E402  (path bootstrap must run first)
from download import patch_nvcl_cache  # noqa: E402
from nvcl_datasets import SAME_HOLE_KM, distance_km, header_collar  # noqa: E402

from .models import Hole

logger = logging.getLogger(__name__)

PROVIDER = "wa"
MINERAL_LOG_LIMIT = 4  # each one is a full CSV fetch over HTTP - kept small
CACHE_TIMEOUT = 60 * 60 * 24  # a day: this is static geological data

_reader = None


def get_reader():
    """One NVCLReader for the process - it holds its own connection pool."""
    global _reader
    if _reader is None:
        patch_nvcl_cache()
        from nvcl_kit.param_builder import param_builder
        from nvcl_kit.reader import NVCLReader

        cache_dir = Path(settings.DATA_DIR) / ".cache" / PROVIDER
        cache_dir.mkdir(parents=True, exist_ok=True)
        params = param_builder(PROVIDER, cache_path=str(cache_dir) + "/")
        _reader = NVCLReader(params)
    return _reader


def parse_scalar_csv(text):
    """[(start_depth, end_depth, value_or_None), ...] from an NVCL scalar CSV."""
    rows = []
    for line in (text or "").splitlines()[1:]:  # skip header
        parts = line.split(",")
        if len(parts) < 3:
            continue
        try:
            start, end = float(parts[0]), float(parts[1])
        except ValueError:
            continue
        value = parts[2].strip()
        rows.append((start, end, value if value and value.lower() != "null" else None))
    return rows


def own_datasets(reader, hole_id):
    """(NVCL hole id, this hole's datasets), or None if NVCL has none for it.

    NVCL answers a hole id with every dataset filed under it, ignoring case,
    and one id can cover different holes: asking for "BH01" (a Bunbury bore
    here) also returns "BH01_Behemoth", a hole 1,250 km away, whose logs come
    first. NVCL files that second hole under the first one's id with its own
    dataset name, so this reads the dataset named after the hole ("BH01_Behemoth"
    is asked for as "BH01"), plus any other at that one's collar. A hole with
    no dataset named after it uses its only dataset, else those whose TSG
    header puts them within SAME_HOLE_KM of the holes table's collar.
    """
    hole = Hole.objects.filter(pk=hole_id).first()
    collar = (hole.latitude, hole.longitude) if hole else None
    for nvcl_id in [hole_id] + ([hole_id.rsplit("_", 1)[0]] if "_" in hole_id else []):
        datasets = [as_dict(item) for item in (reader.get_dataset_list(nvcl_id) or [])]
        if not datasets:
            continue
        named = [item for item in datasets if item.get("dataset_name") == hole_id]
        if not named and nvcl_id == hole_id and len(datasets) == 1:
            return nvcl_id, datasets
        anchors = [c for c in map(header_collar, named) if c] if named else [collar] if collar else []
        own = [item for item in datasets
               if item in named or any(near(header_collar(item), anchor) for anchor in anchors)]
        if own:
            return nvcl_id, own
    return None


def near(a, b):
    km = distance_km(a, b)
    return km is not None and km <= SAME_HOLE_KM


def dataset_log_ids(reader, nvcl_id, dataset_ids):
    """Every log id in these datasets - from the getDatasetCollection response
    nvcl_kit's get_logs_data/get_spectrallog_data flatten across datasets."""
    try:
        root = ET.fromstring(reader.svc.get_dataset_collection(nvcl_id))
    except ET.ParseError:
        return set()
    return {
        element.text.strip()
        for dataset in root.findall("./Dataset")
        if dataset.findtext("DatasetID") in dataset_ids
        for element in dataset.iter()
        if element.tag in ("LogID", "logID") and element.text
    }


def fetch_live_spectral_sample(hole_id, depth_m=None):
    """None if NVCL has nothing usable for this hole; otherwise the same
    shape etl4_bridge.fetch_spectral_sample returns."""
    reader = get_reader()
    found = own_datasets(reader, hole_id)
    if found is None:
        return None
    nvcl_id, datasets = found
    own_logs = dataset_log_ids(reader, nvcl_id, {item.get("dataset_id") for item in datasets})

    scalar_logs = [as_dict(x) for x in (reader.get_logs_data(nvcl_id) or []) if x.log_id in own_logs]
    mineral_logs = [log for log in scalar_logs if str(log.get("log_name") or "").startswith("Min") and log.get("log_id")]
    if not mineral_logs:
        return None

    # The first mineral log's row count sets the resolution everything else
    # (other mineral logs, spectra) must match to stay depth-aligned - NVCL
    # serves each spectral log at more than one resolution (see module docstring).
    first_rows = parse_scalar_csv(reader.get_scalar_data([mineral_logs[0]["log_id"]]))
    if not first_rows:
        return None
    aligned_count = len(first_rows)

    spectral_logs = [as_dict(x) for x in (reader.get_spectrallog_data(nvcl_id) or []) if x.log_id in own_logs]
    usable_spectral = {}
    for log in spectral_logs:
        band_count = len(log.get("wavelengths") or [])
        if band_count and band_count not in usable_spectral and int(float(log.get("sample_count") or 0)) == aligned_count:
            usable_spectral[band_count] = log
    if not usable_spectral:
        return None

    if depth_m is not None:
        row_index = min(range(aligned_count), key=lambda i: abs((first_rows[i][0] + first_rows[i][1]) / 2 - depth_m))
    else:
        # sample 0 is often a near-surface/warm-up reading with nothing
        # available - same reality etl4_bridge's pick_informative_sample
        # works around - so prefer the first row with an actual mineral call.
        row_index = next((i for i, row in enumerate(first_rows) if row[2]), aligned_count // 2)

    depth_from, depth_to, _ = first_rows[row_index]

    minerals = []
    for log in mineral_logs[:MINERAL_LOG_LIMIT]:
        rows = first_rows if log is mineral_logs[0] else parse_scalar_csv(reader.get_scalar_data([log["log_id"]]))
        if row_index < len(rows) and rows[row_index][2]:
            minerals.append({"log_name": log["log_name"], "region": None, "mineral": rows[row_index][2]})

    spectra = []
    for band_count, log in usable_spectral.items():
        data = reader.get_spectrallog_datasets(log["log_id"], start_sample_no=str(row_index), end_sample_no=str(row_index))
        if not isinstance(data, (bytes, bytearray)) or len(data) != band_count * 4:
            continue
        spectra.append({
            "region": log.get("log_name"),
            "wavelength": log.get("wavelengths"),
            "values": list(struct.unpack(f"<{band_count}f", data)),
            "wavelength_unit": log.get("wavelength_units"),
        })
    if not spectra:
        return None

    return {
        "hole_id": hole_id,
        "sample_no": row_index,
        "md_m": (depth_from + depth_to) / 2,
        "sample_count": aligned_count,
        "depth_min_m": first_rows[0][0],
        "depth_max_m": first_rows[-1][1],
        "minerals": minerals,
        "spectra": spectra,
        "source": "nvcl_live",
    }


def fetch_spectral_sample_cached(hole_id, depth_m=None):
    key = f"nvcl_spectral:{hole_id}:{depth_m if depth_m is not None else 'default'}"
    cached = cache.get(key)
    if cached is not None:
        return cached
    try:
        result = fetch_live_spectral_sample(hole_id, depth_m)
    except Exception as exc:  # noqa: BLE001 - a live external service; one hole's
        # quirk (missing logs, an NVCL hiccup) must never break the page.
        logger.warning("nvcl_bridge live fetch failed for %s: %s", hole_id, exc)
        return None
    if result is not None:
        cache.set(key, result, CACHE_TIMEOUT)
    return result

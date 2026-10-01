#!/usr/bin/env python
"""Joins real GSWA/NVCL tray photos into one continuous per-hole core log,
depth running down a single narrow column - the standard core-photo-log
convention (one continuous strip per sheet, not tiles side by side) - not
the wide multi-row filmstrip this module used to produce.

Ground truth for how this works came from actually looking at a downloaded
tray photo (data/raw/BH01_Behemoth/images/tray_image/*/sample_0.jpg) and the manifest
download.py already writes next to it. This does NOT do the naive "assume 4
rows, crop a fixed %" thing; it detects rows from the pixel content of each
photo, because row count and rail width both vary across trays/holes.

What a real tray photo looks like (HyLogging Systems output, e.g.
BH01_Behemoth Tray 0001):
  - A black title bar on top and a "distance along section (mm)" axis strip
    on the bottom - both must be cropped off, not treated as core.
  - N rows of core (4 for BH01_Behemoth, not guaranteed elsewhere), each bordered by
    a bright, low-texture metal rail, separated by dark gaps.
  - Each row already reads left-to-right in increasing depth, and row order
    top-to-bottom is increasing depth - i.e. NOT snake/boustrophedon-packed.
    (This was checked, not assumed.)
  - Occasional wooden ID blocks physically sit inline in a row. Detecting
    and inpainting those reliably is a much harder CV problem than row
    detection; this leaves them in place rather than faking a removal.

Each detected row is a wide, short strip (core runs left-to-right across a
tray). A real core log - see the Geologix/RGC examples this was built
against - reads as ONE continuous narrow column, depth flowing straight
down, the way a physical core tube would look laid out end to end. To get
that, every row is rotated 90 deg (its shallow/left edge becomes its top)
and rows are stacked top-to-bottom into that single column instead of being
pasted as separate wide bands.

A full hole doesn't fit in one image at real resolution: BH01_Behemoth alone runs
roughly 3000+ px per metre once rotated, so ~400m would be over a million
pixels tall - no browser or image codec handles that, and it wouldn't be
scrollable if it did. So the column is split into SHEETS with a bounded
pixel height (see MAX_SHEET_HEIGHT_PX), the same reason paper logs are
"Sheet 4 of 5" rather than one continuous roll.

Depth ground truth: images_manifest.json (kind == "tray_image") gives a
real depth_from/depth_to per tray photo, straight from NVCL's tray-depth
log - not guessed from row count or filename order. Depth *within* a tray
(which row, which position along it) isn't independently known, so it's
allocated proportionally to each row's pixel length, and that approximation
is written into the sidecar table rather than hidden.

Whose photos: only trays from the hole's own NVCL datasets go in (see
hole_datasets) - one NVCL id can cover two different holes, and the
original data/raw/BH01 download held the Behemoth hole's 112 trays
alongside a Bunbury bore's. The table records which datasets the photos
came from, and backend-django-old/holes/views.py won't serve them on any other hole.

Output per hole, under data/media/core_strips/<hole_id>/:
  sheet_0.jpg, sheet_1.jpg, ...  one continuous column each, depth
                                 increasing top-to-bottom, in order
  depth_pixel_table.json        per-row, per-sheet breakpoints + the source
                                 datasets; depth_lookup.py and
                                 mineral_strip.py read this

Where the photos come from: data/raw/<hole>/images/tray_image/ when
download.py fetched them (full, non --scalars-only runs); otherwise
straight from NVCL's getImage service, streamed through memory - a hole's
full-resolution trays are ~0.5-1 GB, and only the ~120 px wide strip built
from them is kept.

Usage:
    python core_strip.py --hole BH01_Behemoth
    python core_strip.py --hole GDD005 KD1
    python core_strip.py --all                # every hole under data/raw without a strip yet
"""

from __future__ import annotations

import argparse
import io
import shutil
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import requests
from PIL import Image
from scipy.signal import find_peaks

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import as_dict, atomic_write_json, read_json  # noqa: E402
from mineral_strip import generate_mineral_strip  # noqa: E402
from nvcl_datasets import SAME_HOLE_KM, distance_km, header_collar  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parent
DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
OUTPUT_DIR = DATA_DIR / "media" / "core_strips"

NVCL_URL = "https://geossdi.dmp.wa.gov.au/NVCLDataServices/"
# Parallel tray-photo downloads per hole - NVCL gave ~16 MB/s at 8 (vs ~1.6
# single-stream), so a ~90-tray hole takes about a minute.
FETCH_WORKERS = 8
# JPEG trays decode at 1/2, 1/4 or 1/8 scale (PIL draft) as long as the
# photo stays at least this tall: a 4020 px, 4-row tray decodes at 1/4 with
# ~250 px rows - still 2x the strip's core_width_px, so the strip looks the
# same, but decoding is ~10x faster and uses 1/16 of the memory.
MIN_DECODE_HEIGHT_PX = 1000

LETTERBOX_MEAN = 15.0          # rows/cols this dark (of 255) are chrome, not core
MIN_CONTENT_FRACTION = 0.5     # sanity floor: content band must be at least half the image
SHADOW_BRIDGE_FRACTION = 0.01  # dark bands shorter than this (of the photo) are shadows inside the tray
SIDE_INSET_FRACTION = 0.03     # extra trim off each row's left/right edge (rail end-caps)
MAX_MERGED_ROWS = 3            # rows one missed rail edge can hide, at most
MAX_SHEET_HEIGHT_PX = 20000    # start a new sheet rather than exceed this (see module docstring)


def _trim_chrome(gray: np.ndarray) -> tuple[int, int, int, int]:
    """Bounding box (x0, y0, x1, y1) of the tray, excluding the title bar,
    axis strip and label gutter: the longest run of lines that aren't
    near-black letterboxing. Measured on 52 trays: the black band under the
    title is >= 1.3% of the photo's height and the axis band >= 1.4%, while
    shadows between tray rows are <= 0.4% - so gaps under 1% are bridged
    (a row-1 shadow used to cut the whole first row off)."""
    h, w = gray.shape
    y0, y1 = _content_span(gray.mean(axis=1), max(2, int(h * SHADOW_BRIDGE_FRACTION)))
    x0, x1 = _content_span(gray[y0:y1].mean(axis=0), max(2, int(w * SHADOW_BRIDGE_FRACTION)))
    return x0, y0, x1, y1


def _content_span(line_mean: np.ndarray, max_gap: int) -> tuple[int, int]:
    runs, start = [], None
    for i, lit in enumerate(np.append(line_mean > LETTERBOX_MEAN, False)):
        if lit and start is None:
            start = i
        elif not lit and start is not None:
            if runs and start - runs[-1][1] <= max_gap:
                runs[-1][1] = i
            else:
                runs.append([start, i])
            start = None
    total = len(line_mean)
    a, b = max(runs, key=lambda r: r[1] - r[0], default=(0, total))
    if b - a < total * MIN_CONTENT_FRACTION:
        return 0, total  # detection looked implausible - don't trim rather than guess wrong
    return a, b


def _rows_explained(lengths: list[int], height: float) -> float:
    """Pixels of these gaps that are a whole number of rows of `height` -
    a merged gap (a missed rail) counting half, so a height that turns most
    gaps into merges (a rail's own thickness, say) loses to the real one."""
    total = 0.0
    for length in lengths:
        n = round(length / height)
        if 1 <= n <= MAX_MERGED_ROWS and abs(length / n - height) <= 0.3 * height:
            total += length if n == 1 else length / 2
    return total


def detect_core_rows(gray_content: np.ndarray) -> list[tuple[int, int]]:
    """[(y0, y1), ...] of core rows within an already chrome-trimmed grayscale
    tray photo, top-to-bottom.

    Row boundaries are the lines where brightness changes along the WHOLE
    width - rail edges and channel walls - found as peaks in the per-line
    median of |vertical gradient|: the median ignores fractures, veins and
    labels that cross only part of a row, and unlike a brightness threshold
    it works whether the core is darker than the rails (basalt, shale) or
    paler (clay, quartz). Rows are the gaps between those lines that come
    to a whole number of rows of the tray's typical row height - a gap of
    two or three rows (a rail edge too faint to find) is split evenly.
    Trailing channels that are empty (bright bare metal, no texture) are
    dropped, so a part-filled last tray's depth goes to the rows with core.

    Checked against 52 trays from 36 holes (dark/pale/rubbly core, metal
    and plastic rails, 3- to 8-row trays): row count right on every one.
    A thin sliver of rail can remain at a row's top/bottom - cosmetic; row
    count, order and depth are what matter.
    """
    h, w = gray_content.shape
    band = gray_content[:, int(w * 0.1):int(w * 0.9)]
    profile = np.zeros(h, dtype=np.float32)
    profile[1:-1] = np.median(np.abs(band[2:] - band[:-2]), axis=1)
    profile = np.convolve(profile, np.ones(3) / 3, mode="same")
    peaks, _ = find_peaks(profile, prominence=0.4 * np.percentile(profile, 99.5), distance=max(2, h // 200))
    edges = [0, *peaks.tolist(), h]
    gaps = list(zip(edges[:-1], edges[1:]))
    lengths = [b - a for a, b in gaps]
    candidates = [length for length in lengths if length >= 0.15 * max(lengths)]
    score = {length: _rows_explained(lengths, length) for length in candidates}
    typical = max(length for length, s in score.items() if s >= 0.95 * max(score.values()))

    line_mean = gray_content.mean(axis=1)
    rows = []
    for a, b in gaps:
        n = round((b - a) / typical)
        if not 1 <= n <= MAX_MERGED_ROWS or abs((b - a) / n - typical) > 0.3 * typical:
            continue
        step = (b - a) / n
        for i in range(n):
            y0, y1 = int(a + i * step), int(a + (i + 1) * step)
            if line_mean[y0:y1].mean() > 2 * LETTERBOX_MEAN:  # not the black axis strip
                rows.append((y0, y1))

    def empty(row):
        y0, y1 = row
        core = gray_content[y0 + (y1 - y0) // 4:y1 - (y1 - y0) // 4, int(w * 0.05):int(w * 0.95)]
        return (core > 200).mean() > 0.7 and np.abs(np.diff(core, axis=1)).mean() < 7

    while rows and empty(rows[-1]):
        rows.pop()
    return rows or [(0, h)]  # nothing detected - fall back to the whole band


def process_tray_image(image_path, core_width_px: int = 120):
    """One tray photo (a path or file-like) -> list of (row_image,
    crop_box_in_source) for each detected core row, top-to-bottom. crop_box
    is in the ORIGINAL photo's pixel space even when it was decoded smaller
    (MIN_DECODE_HEIGHT_PX), so depth_lookup.py can map back to it later.

    Each returned row_image is already rotated so depth runs top-to-bottom
    (shallow/left edge -> top) at a constant width of core_width_px - ready
    to stack straight into one continuous column. Verified against a real
    row (the one with the "BH01 RC91633" label at its shallow/left edge):
    after transpose(ROTATE_270) that label lands at the top, not the
    bottom - i.e. this is 90deg clockwise, left-edge-becomes-top, not the
    other rotation direction.
    """
    img = Image.open(image_path)
    full_width = img.width
    img.draft("RGB", (1, MIN_DECODE_HEIGHT_PX))  # no-op for non-JPEG
    img = img.convert("RGB")
    scale = full_width / img.width
    gray = np.asarray(img.convert("L"), dtype=np.float32)
    cx0, cy0, cx1, cy1 = _trim_chrome(gray)

    rows = detect_core_rows(gray[cy0:cy1, cx0:cx1])
    inset = int((cx1 - cx0) * SIDE_INSET_FRACTION)

    results = []
    for ry0, ry1 in rows:
        box = (cx0 + inset, cy0 + ry0, cx1 - inset, cy0 + ry1)
        row_img = img.crop(box)
        aspect = row_img.width / row_img.height
        row_rescaled = row_img.resize(
            (max(1, int(core_width_px * aspect)), core_width_px),
            Image.Resampling.LANCZOS,
        )
        source_box = tuple(round(value * scale) for value in box)
        results.append((row_rescaled.transpose(Image.ROTATE_270), source_box))
    return results


def hole_datasets(hole_dir: Path, hole_id: str) -> list[dict]:
    """The datasets.json entries that are this hole's - the only ones whose
    tray photos go into its strip.

    NVCL answers a hole id with every dataset filed under it, ignoring case,
    and one id can cover different holes: "BH01" is a 3.6 m Bunbury bore
    (dataset "BH01") and a Behemoth hole 1,250 km away (dataset
    "BH01_Behemoth"), and data/raw/BH01 was first downloaded holding both.
    The dataset's own name is what tells them apart - not borehole.json
    (NVCL's WFS returns either hole's record for a shared id) and not the
    collar in a dataset's TSG header alone (C6's, C9's and T3's each hold
    another hole's). So: the dataset named after the hole, plus any other
    whose TSG header puts it at that one's collar (a chips run of the same
    hole, e.g. EGD002_chips). A folder with a single dataset is that one's.
    """
    path = hole_dir / "datasets.json"
    if not path.is_file():
        raise FileNotFoundError(f"{path} missing - can't tell whose tray photos these are")
    datasets = read_json(path)
    if len(datasets) <= 1:
        return datasets

    named = [item for item in datasets if item.get("dataset_name") == hole_id]
    if not named:
        names = ", ".join(str(item.get("dataset_name")) for item in datasets)
        raise ValueError(f"{hole_id}: {len(datasets)} NVCL datasets ({names}) and none named "
                         f"{hole_id}, so no telling which are this hole's")
    collars = [collar for collar in map(header_collar, named) if collar]

    def at_named_collar(item: dict) -> bool:
        return any((km := distance_km(header_collar(item), collar)) is not None and km <= SAME_HOLE_KM
                   for collar in collars)

    return [item for item in datasets if item in named or at_named_collar(item)]


def load_tray_manifest(hole_dir: Path, dataset_ids: set[str]) -> list[dict]:
    """Real per-tray-photo depth ranges for the given datasets' trays,
    straight from what download.py already fetched from NVCL's tray-depth
    log - sorted by depth, not by filename (filename order isn't guaranteed
    to be zero-padded/sortable). [] when download.py didn't fetch photos
    (--scalars-only) - build_master_hole_strip then asks NVCL directly."""
    manifest_path = hole_dir / "images_manifest.json"
    if not manifest_path.is_file():
        return []
    manifest = read_json(manifest_path)
    trays = [
        item
        for item in manifest.get("files", [])
        if item.get("kind") == "tray_image"
        and item.get("dataset_id") in dataset_ids
        and item.get("depth_from") is not None
        and item.get("depth_to") is not None
    ]
    trays.sort(key=lambda item: item["depth_from"])
    return trays


def nvcl_reader():
    """An NVCLReader sharing download.py's on-disk metadata cache."""
    from download import patch_nvcl_cache
    from nvcl_kit.param_builder import param_builder
    from nvcl_kit.reader import NVCLReader

    patch_nvcl_cache()
    cache_dir = DATA_DIR / ".cache" / "wa"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return NVCLReader(param_builder("wa", cache_path=str(cache_dir) + "/"))


def nvcl_trays(reader, datasets: list[dict]) -> list[dict]:
    """The same per-tray depth ranges load_tray_manifest reads, asked of NVCL
    directly: each dataset's "Tray Images" log and its tray-depth table."""
    trays = []
    for dataset in datasets:
        dataset_id = str(dataset.get("dataset_id"))
        for log in map(as_dict, reader.get_all_imglogs(dataset_id) or []):
            if log.get("log_name") != "Tray Images":
                continue
            for row in map(as_dict, reader.get_tray_depths(log["log_id"]) or []):
                try:
                    depth_from, depth_to = float(row["start_value"]), float(row["end_value"])
                except (KeyError, TypeError, ValueError):
                    continue
                trays.append({"dataset_id": dataset_id, "log_id": log["log_id"],
                              "sample_no": str(row["sample_no"]), "file": None,
                              "depth_from": depth_from, "depth_to": depth_to})
    trays.sort(key=lambda item: item["depth_from"])
    return trays


def fetch_tray_photo(session: requests.Session, tray: dict, attempts: int = 4) -> io.BytesIO:
    """One full-resolution tray photo from NVCL, in memory. Deliberately not
    through the reader's cache, which would keep every ~5-10 MB original.
    Retried here, not by urllib3: NVCL sometimes drops a connection
    mid-body, which urllib3's Retry doesn't cover."""
    params = {"logid": tray["log_id"], "sampleno": tray["sample_no"],
              "datasetid": tray["dataset_id"], "uncorrected": "yes"}
    for attempt in range(attempts):
        try:
            response = session.get(NVCL_URL + "getImage.html", params=params, timeout=(20, 120))
            response.raise_for_status()
            if response.content[:2] != b"\xff\xd8":
                raise ValueError(f"NVCL returned no JPEG for tray {tray['sample_no']}")
            return io.BytesIO(response.content)
        except (requests.RequestException, ValueError):
            if attempt == attempts - 1:
                raise
            time.sleep(2 ** attempt)


def build_master_hole_strip(
    hole_id: str,
    core_width_px: int = 120,
    limit: int | None = None,
    max_sheet_height_px: int = MAX_SHEET_HEIGHT_PX,
    datasets: list[dict] | None = None,
    reader=None,
):
    """Stitches every tray photo for one hole into one or more continuous
    vertical sheets - each one narrow, depth increasing top-to-bottom, no
    row ever pasted side-by-side with another - plus a depth<->pixel
    breakpoint table shared across all of them.

    limit: only the first N trays (by depth) - handy for a quick check
    before committing to a multi-hundred-metre hole's full-res output.
    datasets: the hole's own NVCL datasets, for a hole with no data/raw
    folder (the backend passes nvcl_bridge.own_datasets'); else read from
    datasets.json. reader: an NVCLReader to reuse, for holes whose photos
    weren't downloaded.
    """
    hole_dir = RAW_DIR / hole_id
    if datasets is None and (hole_dir / "datasets.json").is_file():
        datasets = hole_datasets(hole_dir, hole_id)
    elif datasets is None:  # never downloaded: the NVCL dataset named after it, else its only one
        reader = reader or nvcl_reader()
        found = [as_dict(item) for item in reader.get_dataset_list(hole_id) or []]
        datasets = [item for item in found if item.get("dataset_name") == hole_id] or found[:len(found) == 1]
    trays = load_tray_manifest(hole_dir, {str(item.get("dataset_id")) for item in datasets})
    if not trays:
        trays = nvcl_trays(reader or nvcl_reader(), datasets)
    if limit:
        trays = trays[:limit]
    if not trays:
        raise ValueError(f"{hole_id}: NVCL has no depth-known tray photos for this hole's datasets")
    photographed = {tray["dataset_id"] for tray in trays}

    def source_of(tray):
        return hole_dir / tray["file"] if tray["file"] else fetch_tray_photo(session, tray)

    all_rows: list[dict] = []  # image + metadata, one per detected core row, in depth order
    # Downloads run on FETCH_WORKERS threads while rows are cut here in depth
    # order, two photos per worker at a time - a whole hole's originals
    # (~1 GB) never sit in memory at once.
    batch = FETCH_WORKERS * 2
    with requests.Session() as session, ThreadPoolExecutor(FETCH_WORKERS) as pool:
        session.mount("https://", requests.adapters.HTTPAdapter(pool_maxsize=FETCH_WORKERS))
        for start in range(0, len(trays), batch):
            chunk = trays[start:start + batch]
            for tray, source in zip(chunk, pool.map(source_of, chunk)):
                all_rows.extend(tray_rows(tray, source, core_width_px))

    # Built in a hidden folder and swapped in whole: a rebuild never serves
    # half-written sheets, and leaves no stale ones from a previous build.
    final_dir = OUTPUT_DIR / hole_id
    out_dir = OUTPUT_DIR / f".{hole_id}.building"
    shutil.rmtree(out_dir, ignore_errors=True)
    out_dir.mkdir(parents=True)
    sheets, _ = write_sheets(hole_id, datasets, photographed, all_rows, out_dir, core_width_px,
                             max_sheet_height_px, len(trays))
    # The matching mineral-colour strip, from the hole's NVCL TSA scalar log
    # (downloaded holes only - a catalog-only hole gets photos alone).
    try:
        generate_mineral_strip(hole_id, strip_dir=out_dir)
    except Exception as exc:  # noqa: BLE001
        print(f"{hole_id}: photos only, no mineral strip ({exc})")
    previous = OUTPUT_DIR / f".{hole_id}.previous"
    shutil.rmtree(previous, ignore_errors=True)
    if final_dir.exists():
        final_dir.rename(previous)
    out_dir.rename(final_dir)
    shutil.rmtree(previous, ignore_errors=True)
    return sheets, final_dir / "depth_pixel_table.json"


def tray_rows(tray: dict, source, core_width_px: int) -> list[dict]:
    """One tray's detected core rows, each given its share of the tray's
    real depth range in proportion to its pixel length."""
    row_results = process_tray_image(source, core_width_px)
    lengths = [img.height for img, _ in row_results]  # post-rotation: height = depth extent
    total_len = sum(lengths) or 1
    depth_span = tray["depth_to"] - tray["depth_from"]
    cursor = tray["depth_from"]
    rows = []
    for (row_img, crop_box), length in zip(row_results, lengths):
        row_depth_to = cursor + depth_span * (length / total_len)
        rows.append({
            "tray_sample_no": tray["sample_no"],
            # a local file, or the NVCL getImage request it was streamed from
            "source_file": tray["file"] or f"nvcl:{tray['log_id']}/{tray['sample_no']}",
            "crop_box": list(crop_box),
            "depth_from_m": round(cursor, 4),
            "depth_to_m": round(row_depth_to, 4),
            "image": row_img,
        })
        cursor = row_depth_to
    return rows


def write_sheets(hole_id, datasets, photographed, all_rows, out_dir, core_width_px,
                 max_sheet_height_px, tray_count):
    """Stack the rows into bounded-height sheets and write the depth<->pixel
    table. The table goes last, so its presence means the strip is complete."""
    sheets: list[dict] = []
    sheet_rows: list[dict] = []
    sheet_height = 0

    def flush_sheet():
        nonlocal sheet_rows, sheet_height
        if not sheet_rows:
            return
        sheet_index = len(sheets)
        canvas = Image.new("RGB", (core_width_px, sheet_height), (20, 20, 20))
        y = 0
        for row in sheet_rows:
            canvas.paste(row["image"], (0, y))
            row["sheet_index"] = sheet_index
            row["y_from_px"] = y
            row["y_to_px"] = y + row["image"].height
            y += row["image"].height
            del row["image"]
        # libjpeg hard-caps JPEG at 65500px per side - stay under
        # max_sheet_height_px normally, but fall back to PNG rather than
        # crash if a single oversized row ever pushes a sheet past it.
        if sheet_height <= 65500:
            sheet_path = out_dir / f"sheet_{sheet_index}.jpg"
            canvas.save(sheet_path, quality=88)
        else:
            sheet_path = out_dir / f"sheet_{sheet_index}.png"
            canvas.save(sheet_path)
        sheets.append({
            "sheet_index": sheet_index,
            "file": sheet_path.name,
            "width_px": core_width_px,
            "height_px": sheet_height,
            "depth_from_m": sheet_rows[0]["depth_from_m"],
            "depth_to_m": sheet_rows[-1]["depth_to_m"],
        })
        sheet_rows = []
        sheet_height = 0

    for row in all_rows:
        row_height = row["image"].height
        if sheet_rows and sheet_height + row_height > max_sheet_height_px:
            flush_sheet()
        sheet_rows.append(row)
        sheet_height += row_height
    flush_sheet()

    table_path = out_dir / "depth_pixel_table.json"
    atomic_write_json(table_path, {
        "hole_id": hole_id,
        # The NVCL datasets these photos came from, with the collar each
        # one's TSG header gives - backend-django-old/holes/views.py checks them against
        # the holes table before showing the strip on a hole.
        "source_datasets": [
            {
                "dataset_id": item.get("dataset_id"),
                "dataset_name": item.get("dataset_name"),
                "tsg_collar": header_collar(item),
            }
            for item in datasets
            if item.get("dataset_id") in photographed
        ],
        "core_width_px": core_width_px,
        "depth_min_m": all_rows[0]["depth_from_m"],
        "depth_max_m": all_rows[-1]["depth_to_m"],
        "sheets": sheets,
        "rows": all_rows,
    })
    print(f"{hole_id}: {tray_count} trays, {len(all_rows)} rows -> {len(sheets)} sheet(s) in {out_dir}")
    return sheets, table_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hole", nargs="+", help="hole id(s), e.g. BH01_Behemoth")
    parser.add_argument("--all", action="store_true",
                        help="every hole under data/raw that has no strip yet")
    parser.add_argument("--core-width", type=int, default=120, help="constant ribbon width in px")
    parser.add_argument("--max-sheet-height", type=int, default=MAX_SHEET_HEIGHT_PX)
    parser.add_argument("--limit", type=int, default=None, help="only the first N trays (by depth)")
    args = parser.parse_args()

    if not args.hole and not args.all:
        parser.error("pass --hole <id> ... or --all")

    hole_ids = args.hole or sorted(
        p.name for p in RAW_DIR.iterdir()
        if p.is_dir() and not (OUTPUT_DIR / p.name / "depth_pixel_table.json").is_file()
    )
    reader = nvcl_reader()
    for index, hole_id in enumerate(hole_ids, start=1):
        try:
            print(f"[{index}/{len(hole_ids)}] ", end="", flush=True)
            build_master_hole_strip(
                hole_id,
                core_width_px=args.core_width,
                limit=args.limit,
                max_sheet_height_px=args.max_sheet_height,
                reader=reader,
            )
        except Exception as exc:  # noqa: BLE001 - one bad hole shouldn't stop a bulk run
            print(f"{hole_id}: skipped ({exc})", flush=True)

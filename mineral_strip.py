#!/usr/bin/env python
"""Builds a TSG mineral-group strip pixel-aligned to core_strip.py's photo strip.

This replaces the draft `prepare_mineral_by_tray.py` that never actually
read its mineral_csv_path argument - it painted 6 fixed colour bars
regardless of input. This one reads real data:

  - Your downloaded holes don't have raw *_tsg.bip files locally (checked:
    data/raw/<hole>/spectral/ only has *.f32 reflectance grids), and NVCL
    has already done the mineral classification server-side - it publishes
    the *result* as scalar-log CSVs under data/raw/<hole>/scalars/, e.g.
    Grp1_sTSAS.csv ("StartDepth,EndDepth,Grp1_sTSAS"), with values like
    CARBONATE, WHITE-MICA, CHLORITE - already mineral GROUP names, not
    numeric class IDs. So there's no pytsg/.bip decode step needed for this
    data; the "decode numeric class IDs" requirement in the spec doesn't
    apply to what NVCL actually hands us here.
  - Picks its log the way files/extract.py does for the website's per-metre
    calls, so a band here is the colour the site gives the same depth: the
    dominant TSA SWIR group (Grp1_sTSAS first), else the dominant TSA
    mineral (Min1_sTSAS...) rolled up to its group through the same MIN2GRP
    table.
  - --tir colours by the thermal-infrared classification instead
    (Grp1_sjCLST), which sees the silicates SWIR can't - SILICA,
    PLAGIOCLASE, K-FELDSPAR. It's written beside the SWIR strip, not over
    it: backend-django-old/holes/views.py serves tsg_sheet_N.png.
  - One hole folder can hold several NVCL datasets' copies of the same log
    (data/raw/BH01 was downloaded holding both BH01_Behemoth's 400 m scan,
    which the tray photos come from, and an unrelated 3.6 m Bunbury bore),
    so the copy used is the one with the most calls in the photographed
    depth range, not whichever sorts first.
  - Colours are the exact frontend/config.js MINERAL_COLOURS palette
    (copied, not re-derived - JS can't import this file, so config.js
    already keeps its own copy in sync with MIN2GRP the same way).

Depends on core_strip.py having already run for the hole (reads its
depth_pixel_table.json, so every mineral band lines up with the same rows
as the photo strip - the original draft's two files had no such link).

Usage:
    python mineral_strip.py --hole BH01_Behemoth
    python mineral_strip.py --hole BH01_Behemoth --tir
"""

from __future__ import annotations

import argparse
import csv
import glob
import sys
from collections import Counter
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import atomic_write_json, read_json  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parent
RAW_DIR = PROJECT_ROOT / "data" / "raw"
OUTPUT_DIR = PROJECT_ROOT / "data" / "media" / "core_strips"

# Species -> group. Copied from files/extract.py's MIN2GRP - keep in sync.
MIN2GRP = {
    "Chlorite": "CHLORITE", "Kaolinite": "KAOLIN", "Dickite": "KAOLIN",
    "Nacrite": "KAOLIN", "Halloysite": "KAOLIN", "Muscovite": "WHITE-MICA",
    "Paragonite": "WHITE-MICA", "Illite": "WHITE-MICA", "Phengite": "WHITE-MICA",
    "Biotite": "DARK-MICA", "Phlogopite": "DARK-MICA", "Calcite": "CARBONATE",
    "Dolomite": "CARBONATE", "Ankerite": "CARBONATE", "Siderite": "CARBONATE",
    "Magnesite": "CARBONATE", "Gypsum": "SULPHATE", "Alunite": "SULPHATE",
    "Jarosite": "SULPHATE", "Epidote": "EPIDOTE", "Zoisite": "EPIDOTE",
    "Clinozoisite": "EPIDOTE", "Hornblende": "AMPHIBOLE", "Actinolite": "AMPHIBOLE",
    "Tremolite": "AMPHIBOLE", "Riebeckite": "AMPHIBOLE", "Talc": "OTHER-MGOH",
    "Serpentine": "SERPENTINE", "Montmorillonite": "SMECTITE",
    "Nontronite": "SMECTITE", "Saponite": "SMECTITE", "Tourmaline": "TOURMALINE",
    "Rubellite": "TOURMALINE", "Prehnite": "OTHER-MGOH", "Pyrophyllite": "OTHER",
    "Topaz": "OTHER", "Diaspore": "OTHER", "Gibbsite": "OTHER",
}

# Readings that are not rock at all, or the classifier refused to call.
# Copied from files/extract.py's BLANK - keep in sync.
BLANK = {"", "NAN", "NONE", "INVALID", "ASPECTRAL", "NOTAROK", "NOT A ROCK",
         "WHITEMARKER", "WHITE MARKER", "WOOD", "VEGETATION", "NULL", "NA", "N/A"}

# Copied from frontend/config.js MINERAL_COLOURS - keep in sync.
MINERAL_COLOURS = {
    "CHLORITE": "#22c55e", "WHITE-MICA": "#fde047", "DARK-MICA": "#ef4444",
    "AMPHIBOLE": "#38bdf8", "CARBONATE": "#6366f1", "KAOLIN": "#f472b6",
    "SERPENTINE": "#14b8a6", "OTHER-MGOH": "#a3e635", "SMECTITE": "#c2410c",
    "SULPHATE": "#c084fc", "EPIDOTE": "#4d7c0f", "TOURMALINE": "#1e40af",
    "QUARTZ": "#f5f5f4", "HEMATITE": "#9f1239", "OTHER": "#8c9aa5",
    # Groups the TIR classification calls (OTHER-ALOH turns up in TSA SWIR
    # too). SILICA is TIR's quartz group and OXIDE hematite's, so they share
    # those colours.
    "SILICA": "#f5f5f4", "OXIDE": "#9f1239", "PLAGIOCLASE": "#7c3aed",
    "K-FELDSPAR": "#fda4af", "PYROXENE": "#0891b2", "OLIVINE": "#bef264",
    "OTHER-ALOH": "#d946ef", "PHOSPHATE": "#e11d48", "GARNET": "#965970",
}
UNKNOWN_COLOUR = "#4a5560"

# holes_3d.py's GROUP_LOGS / SPECIES_LOGS order - files/extract.py's for the
# website's per-metre calls, bar the TSAT logs no downloaded hole has - so a
# row gets the colour the site gives that depth: TSA SWIR group first, then
# the TSA mineral rolled up to its group through MIN2GRP.
GROUP_LOG_GLOBS = ["*Grp1_sTSAS.csv", "*Grp1_uTSAS.csv", "*Grp1_sTSAV.csv", "*Grp1_uTSAV.csv"]
SPECIES_LOG_GLOBS = ["*Min1_sTSAS.csv", "*Min1_uTSAS.csv", "*Min1_sTSAV.csv", "*Min1_uTSAV.csv"]
# --tir: the TIR classification's dominant group. Grp1, not Grp2 - Grp2 is
# the second-ranked mineral at each sample, not the dominant one.
TIR_LOG_GLOBS = ["*Grp1_sjCLST.csv", "*Grp1_ujCLST.csv"]


def hex_to_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))


def load_scalar_log(csv_path: Path) -> list[tuple[float, float, str]]:
    """[(start_depth, end_depth, value), ...], nulls dropped. Same
    StartDepth,EndDepth,<value> shape nvcl_bridge.parse_scalar_csv reads."""
    rows = []
    with csv_path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.reader(handle)
        next(reader, None)  # header
        for parts in reader:
            if len(parts) < 3:
                continue
            try:
                start, end = float(parts[0]), float(parts[1])
            except ValueError:
                continue
            value = parts[2].strip()
            if value and value.upper() not in BLANK:
                rows.append((start, end, value))
    return rows


def resolve_group(raw_value: str) -> str:
    """Species name -> group via MIN2GRP, a TSA variant by its base name
    ('Chlorite-FeMg' -> CHLORITE, 'Kaolinite-WX' -> KAOLIN) as files/extract.py
    rolls them up, or treat the value as already a group name (Grp1 logs give
    those directly, and none - WHITE-MICA, K-FELDSPAR - splits to a MIN2GRP
    key). Never returns None for a non-blank input - an unrecognised name
    still becomes its own uppercase key, so it falls back to UNKNOWN_COLOUR
    rather than vanishing."""
    species = raw_value.split("-")[0]
    if species in MIN2GRP:
        return MIN2GRP[species]
    return raw_value.upper()


def find_first(hole_dir: Path, globs: list[str]) -> Path | None:
    scalars_dir = hole_dir / "scalars"
    for pattern in globs:
        matches = sorted(scalars_dir.glob(pattern))
        if matches:
            return matches[0]
    return None


def pick_log(hole_dir: Path, globs: list[str], depth_from: float,
             depth_to: float) -> tuple[Path, list[tuple[float, float, str]]] | None:
    """(path, rows) of the first log in `globs` with a call in [depth_from,
    depth_to). download.py puts every NVCL dataset's scalars for a hole in
    one folder, so a glob can match several copies of the same log from
    different scans - even different holes that share a name - and the one
    with the most calls in range is the one describing the photographed core."""
    for pattern in globs:
        best = None
        for path in sorted((hole_dir / "scalars").glob(pattern)):
            rows = load_scalar_log(path)
            n = sum(depth_from <= (start + end) / 2 < depth_to for start, end, _ in rows)
            if n and (best is None or n > best[0]):
                best = (n, path, rows)
        if best:
            return best[1], best[2]
    return None


def load_hole_mineral_log(hole_id: str, globs: list[str], depth_from: float,
                          depth_to: float) -> tuple[Path, list[tuple[float, float, str]]]:
    """(log path, group-resolved (start, end, group) rows) from the first of
    `globs` with calls in [depth_from, depth_to)."""
    hole_dir = RAW_DIR / hole_id
    picked = pick_log(hole_dir, globs, depth_from, depth_to)
    if picked is None:
        raise FileNotFoundError(
            f"no {globs} under {hole_dir / 'scalars'} with a call in {depth_from:g}-{depth_to:g} m"
        )
    path, rows = picked
    return path, [(start, end, resolve_group(value)) for start, end, value in rows]


def dominant_group_in_range(rows, depth_from: float, depth_to: float) -> tuple[str | None, float]:
    """(dominant_group, coverage) for [depth_from, depth_to), where coverage
    is the dominant group's share of samples whose midpoint falls in range -
    same spirit as CoreTray.swir_vnir_pct_of_tray."""
    in_range = [group for start, end, group in rows if depth_from <= (start + end) / 2 < depth_to]
    if not in_range:
        return None, 0.0
    counts = Counter(in_range)
    group, n = counts.most_common(1)[0]
    return group, n / len(in_range)


def generate_mineral_strip(hole_id: str, tir: bool = False, strip_dir: Path | None = None) -> list[Path]:
    """One TSG-colour PNG per sheet core_strip.py produced, each the exact
    (width, height) of its matching sheet_N image, so the two can sit
    edge-to-edge - bands are horizontal, one per row, at that row's
    y_from_px..y_to_px within its own sheet (same layout core_strip.py uses
    for the photo, just coloured by mineral group instead of photographed).

    tir: colour by TIR_LOG_GLOBS instead, into tsg_tir_sheet_N.png and
    mineral_by_row_tir.json, so the SWIR strip the site serves is untouched.
    strip_dir: where the photo strip is, if not data/media/core_strips/<hole>
    (core_strip.py builds into a temporary folder first)."""
    out_dir = strip_dir or OUTPUT_DIR / hole_id
    table_path = out_dir / "depth_pixel_table.json"
    if not table_path.is_file():
        raise FileNotFoundError(f"{table_path} missing - run core_strip.py --hole {hole_id} first")
    table = read_json(table_path)
    globs = TIR_LOG_GLOBS if tir else GROUP_LOG_GLOBS + SPECIES_LOG_GLOBS
    log_path, mineral_rows = load_hole_mineral_log(hole_id, globs, table["depth_min_m"], table["depth_max_m"])
    log_name = log_path.stem.split("_", 1)[-1]  # '<log uuid>_Grp1_sTSAS' -> 'Grp1_sTSAS'
    sheet_prefix, calls_name = ("tsg_tir_sheet", "mineral_by_row_tir.json") if tir else ("tsg_sheet", "mineral_by_row.json")

    rows_by_sheet: dict[int, list[dict]] = {}
    for row in table["rows"]:
        rows_by_sheet.setdefault(row["sheet_index"], []).append(row)

    strip_paths = []
    row_calls = []
    for sheet in table["sheets"]:
        strip = Image.new("RGB", (sheet["width_px"], sheet["height_px"]), hex_to_rgb(UNKNOWN_COLOUR))
        for row in rows_by_sheet.get(sheet["sheet_index"], []):
            group, coverage = dominant_group_in_range(mineral_rows, row["depth_from_m"], row["depth_to_m"])
            colour = MINERAL_COLOURS.get(group, UNKNOWN_COLOUR) if group else UNKNOWN_COLOUR
            band = Image.new("RGB", (sheet["width_px"], row["y_to_px"] - row["y_from_px"]), hex_to_rgb(colour))
            strip.paste(band, (0, row["y_from_px"]))
            row_calls.append({
                "sheet_index": sheet["sheet_index"],
                "depth_from_m": row["depth_from_m"], "depth_to_m": row["depth_to_m"],
                "group": group, "coverage": round(coverage, 3),
            })
        strip_path = out_dir / f"{sheet_prefix}_{sheet['sheet_index']}.png"
        strip.save(strip_path)
        strip_paths.append(strip_path)

    atomic_write_json(out_dir / calls_name, {
        "hole_id": hole_id, "log": log_name,
        "log_file": log_path.relative_to(RAW_DIR / hole_id).as_posix(),
        "rows": row_calls,
    })
    called = sum(1 for r in row_calls if r["group"])
    coloured = sum(1 for r in row_calls if r["group"] in MINERAL_COLOURS)
    print(f"{hole_id}: {called}/{len(row_calls)} rows with a mineral call from {log_name}, "
          f"{coloured} in a palette colour -> {len(strip_paths)} sheet(s) in {out_dir}")
    return strip_paths


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--hole", required=True)
    parser.add_argument("--tir", action="store_true",
                        help="colour by the TIR classification (Grp1_sjCLST) instead, "
                             "into tsg_tir_sheet_N.png + mineral_by_row_tir.json")
    args = parser.parse_args()
    generate_mineral_strip(args.hole, tir=args.tir)

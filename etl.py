#!/usr/bin/env python
"""Convert the NVCL original package into five kinds of readable CSV."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import re
import shutil
import struct
import sys
import traceback
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator

from common import atomic_write_json, load_hole_ids, read_json, sha256_file, utc_now

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


HERE = Path(__file__).resolve().parent
NULL_MARKERS = {"", "null", "none", "nan", "na", "n/a"}
HOLE_FIELDS = [
    "hole_id",
    "hole_name",
    "longitude",
    "latitude",
    "elevation_m",
    "borehole_length_m",
    "drill_start_date",
    "drill_end_date",
    "operator",
    "driller",
    "project",
    "purpose",
    "drilling_method",
    "status",
    "dataset_count",
    "dataset_names",
    "instruments",
    "sample_count",
    "depth_from_m",
    "depth_to_m",
    "measurement_columns",
    "core_interval_count",
    "signal_series_count",
    "asset_record_count",
    "download_status",
    "etl_time",
]


@dataclass
class ScalarSource:
    log: dict
    path: Path | None
    row_count: int = 0
    non_null_count: int = 0
    file_sha256: str = ""
    depth_hash: str = ""

    @property
    def log_id(self) -> str:
        return str(self.log.get("log_id") or "")


def safe_column(value: object, fallback: str) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text or fallback


def float_or_none(value: object) -> float | None:
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def clean_value(value: object) -> str:
    text = str(value or "").strip()
    return "" if text.lower() in NULL_MARKERS else text


def human_number(value: object) -> str:
    text = clean_value(value)
    number = float_or_none(text)
    if number is None:
        return text
    return str(int(number)) if number.is_integer() else f"{number:g}"


def open_csv_writer(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("w", encoding="utf-8-sig", newline="")
    return handle, csv.writer(handle, lineterminator="\n")


def find_scalar_file(raw_dir: Path, log_id: str) -> Path | None:
    manifest_path = raw_dir / "scalar_manifest.json"
    if manifest_path.is_file():
        for item in read_json(manifest_path):
            if str(item.get("log_id") or "") == log_id and item.get("file"):
                path = raw_dir / str(item["file"])
                if path.is_file():
                    return path
    files = [
        path
        for path in sorted((raw_dir / "scalars").glob(f"{log_id}_*"))
        if path.is_file() and not path.name.endswith((".part", ".tmp"))
    ]
    return files[0] if len(files) == 1 else None


def scalar_rows(path: Path) -> Iterator[tuple[str, str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        header = next(reader, None)
        if header is None or len(header) < 3:
            raise ValueError(f"invalid scalar CSV header: {path}")
        for line_no, row in enumerate(reader, start=2):
            if len(row) < 3:
                raise ValueError(f"{path.name}:{line_no}: fewer than three columns")
            yield row[0].strip(), row[1].strip(), clean_value(row[2])


def scan_scalar(log: dict, path: Path | None) -> ScalarSource:
    result = ScalarSource(log=log, path=path)
    if path is None:
        return result
    depth_digest = hashlib.sha256()
    for start, end, value in scalar_rows(path):
        result.row_count += 1
        result.non_null_count += bool(value)
        depth_digest.update(start.encode("utf-8"))
        depth_digest.update(b"\0")
        depth_digest.update(end.encode("utf-8"))
        depth_digest.update(b"\n")
    result.file_sha256 = sha256_file(path)
    result.depth_hash = depth_digest.hexdigest()
    return result


def choose_axis(sources: Iterable[ScalarSource], special_ids: set[str]):
    candidates = [
        item
        for item in sources
        if item.path is not None and item.log_id not in special_ids and item.row_count > 0
    ]
    if not candidates:
        raise ValueError("no scalar sample axis found")
    counts = Counter((item.row_count, item.depth_hash) for item in candidates)
    axis_key = max(counts, key=lambda key: (counts[key], key[0]))
    representative = next(
        item for item in candidates if (item.row_count, item.depth_hash) == axis_key
    )
    assert representative.path is not None
    axis = []
    for sample_no, (start, end, _) in enumerate(scalar_rows(representative.path)):
        start_value = float_or_none(start)
        end_value = float_or_none(end)
        if start_value is None or end_value is None:
            raise ValueError(f"non-numeric sample depth in {representative.path.name}")
        axis.append((sample_no, start_value, end_value))
    return axis_key, axis


def build_measurement_columns(
    sources: list[ScalarSource], special_ids: set[str]
) -> tuple[list[tuple[str, ScalarSource]], int, int]:
    groups: dict[str, list[ScalarSource]] = defaultdict(list)
    order = {}
    for index, source in enumerate(sources):
        if source.log_id in special_ids or source.path is None:
            continue
        key = source.file_sha256
        groups[key].append(source)
        order.setdefault(key, index)
    ordered = [groups[key] for key in sorted(groups, key=order.get)]
    omitted_empty = sum(len(group) for group in ordered if group[0].non_null_count == 0)
    useful = [group for group in ordered if group[0].non_null_count > 0]
    names = [safe_column(group[0].log.get("log_name"), "Measurement") for group in useful]
    totals = Counter(name.casefold() for name in names)
    seen = Counter()
    columns = []
    for group, name in zip(useful, names):
        folded = name.casefold()
        seen[folded] += 1
        column = name if totals[folded] == 1 else f"{name} ({seen[folded]})"
        columns.append((column, group[0]))
    columns.sort(key=lambda item: item[0].casefold())
    duplicate_sources = sum(len(group) - 1 for group in ordered)
    return columns, duplicate_sources, omitted_empty


def values_aligned_to_axis(
    source: ScalarSource,
    axis: list[tuple[int, float, float]],
) -> list[str]:
    assert source.path is not None
    index = defaultdict(deque)
    for sample_no, start, end in axis:
        index[(start, end)].append(sample_no)
    values = [""] * len(axis)
    for start, end, value in scalar_rows(source.path):
        key = (float_or_none(start), float_or_none(end))
        if None in key or not index.get(key):
            raise ValueError(
                f"{source.path.name}: depth interval {start}-{end} is not on the common axis"
            )
        sample_no = index[key].popleft()
        values[sample_no] = value
    return values


def write_measurements(
    path: Path,
    hole_id: str,
    axis: list[tuple[int, float, float]],
    columns: list[tuple[str, ScalarSource]],
) -> int:
    values = [values_aligned_to_axis(source, axis) for _, source in columns]
    handle, writer = open_csv_writer(path)
    try:
        writer.writerow(
            ["hole_id", "sample_no", "depth_from_m", "depth_to_m"]
            + [name for name, _ in columns]
        )
        for sample_no, depth_from, depth_to in axis:
            writer.writerow(
                [hole_id, sample_no, f"{depth_from:g}", f"{depth_to:g}"]
                + [column_values[sample_no] for column_values in values]
            )
    finally:
        handle.close()
    return len(axis)


def parse_intervals(path: Path | None) -> list[tuple[int, int, str]]:
    if path is None:
        return []
    result = []
    for start, end, value in scalar_rows(path):
        start_number = float_or_none(start)
        end_number = float_or_none(end)
        if start_number is not None and end_number is not None:
            result.append((int(start_number), int(end_number), value))
    return result


def source_path(data_root: Path, raw_dir: Path, relative: object) -> str:
    if not relative:
        return ""
    absolute = (raw_dir / str(relative)).resolve()
    try:
        return absolute.relative_to(data_root.resolve()).as_posix()
    except ValueError:
        return str(absolute)


def write_core_intervals(
    path: Path,
    data_root: Path,
    raw_dir: Path,
    hole_id: str,
    datasets: list[dict],
    sources_by_id: dict[str, ScalarSource],
    axis: list[tuple[int, float, float]],
) -> int:
    tray_depths = read_json(raw_dir / "tray_depths.json") if (raw_dir / "tray_depths.json").is_file() else {}
    image_manifest = read_json(raw_dir / "images_manifest.json") if (raw_dir / "images_manifest.json").is_file() else {"files": []}
    thumbnails = {
        (str(item.get("dataset_id") or ""), str(item.get("sample_no") or "")): source_path(
            data_root, raw_dir, item.get("file")
        )
        for item in image_manifest.get("files", [])
        if item.get("kind") == "tray_thumbnail"
    }
    header = [
        "hole_id",
        "dataset_name",
        "tray_no",
        "tray_index",
        "section_no",
        "sample_start",
        "sample_end",
        "depth_from_m",
        "depth_to_m",
        "tray_depth_from_m",
        "tray_depth_to_m",
        "thumbnail_path",
    ]
    handle, writer = open_csv_writer(path)
    count = 0
    try:
        writer.writerow(header)
        for dataset_index, dataset in enumerate(datasets):
            dataset_id = str(dataset.get("dataset_id") or dataset.get("DatasetID") or "")
            dataset_name = str(dataset.get("dataset_name") or dataset.get("DatasetName") or f"Dataset {dataset_index + 1}")
            tray_id = str(dataset.get("tray_id") or "")
            section_id = str(dataset.get("section_id") or "")
            trays = parse_intervals(sources_by_id.get(tray_id).path if tray_id in sources_by_id else None)
            sections = parse_intervals(sources_by_id.get(section_id).path if section_id in sources_by_id else None)
            depth_by_tray = {
                str(item.get("sample_no")): (
                    float_or_none(item.get("depth_from")),
                    float_or_none(item.get("depth_to")),
                )
                for item in image_manifest.get("files", [])
                if item.get("kind") == "tray_thumbnail"
                and str(item.get("dataset_id") or "") == dataset_id
            }
            if not depth_by_tray:
                depth_by_tray = {
                    str(item.get("sample_no")): (
                        float_or_none(item.get("start_value")),
                        float_or_none(item.get("end_value")),
                    )
                    for entries in tray_depths.values()
                    for item in entries
                }
            layout_rows = sections if sections else trays
            for sample_start, sample_end, section_value in layout_rows:
                matches = [
                    (index, row)
                    for index, row in enumerate(trays)
                    if row[0] <= sample_start and row[1] >= sample_end
                ]
                if not matches:
                    matches = [
                        (index, row)
                        for index, row in enumerate(trays)
                        if row[0] <= sample_start <= row[1]
                    ]
                tray_index, tray = matches[0] if matches else (None, (0, 0, ""))
                tray_from, tray_to = depth_by_tray.get(str(tray_index), (None, None))
                depth_from = axis[sample_start][1] if 0 <= sample_start < len(axis) else None
                depth_to = axis[sample_end][2] if 0 <= sample_end < len(axis) else None
                writer.writerow(
                    [
                        hole_id,
                        dataset_name,
                        tray[2],
                        "" if tray_index is None else tray_index,
                        human_number(section_value) if sections else "",
                        sample_start,
                        sample_end,
                        "" if depth_from is None else f"{depth_from:g}",
                        "" if depth_to is None else f"{depth_to:g}",
                        "" if tray_from is None else f"{tray_from:g}",
                        "" if tray_to is None else f"{tray_to:g}",
                        thumbnails.get((dataset_id, str(tray_index)), ""),
                    ]
                )
                count += 1
    finally:
        handle.close()
    return count


def wavelength_column(value: object, units: object) -> str:
    number = f"{float(value):g}".replace("-", "neg_").replace(".", "p")
    unit = re.sub(r"[^A-Za-z0-9]+", "_", str(units or "unit")).strip("_").lower()
    return f"wl_{number}_{unit}"


def write_signals(
    path: Path,
    raw_dir: Path,
    hole_id: str,
    axis: list[tuple[int, float, float]],
) -> tuple[int, int]:
    spectral_logs = {
        str(item.get("log_id") or ""): item
        for item in (read_json(raw_dir / "logs_spectral.json") if (raw_dir / "logs_spectral.json").is_file() else [])
    }
    spectral_manifest = read_json(raw_dir / "spectral_manifest.json") if (raw_dir / "spectral_manifest.json").is_file() else []
    profiler_logs = {
        str(item.get("log_id") or ""): item
        for item in (read_json(raw_dir / "logs_profilometer.json") if (raw_dir / "logs_profilometer.json").is_file() else [])
    }
    profiler_manifest = read_json(raw_dir / "profilometer_manifest.json") if (raw_dir / "profilometer_manifest.json").is_file() else []

    complete_spectra = [item for item in spectral_manifest if item.get("status") == "complete"]
    all_wave_columns = []
    for item in complete_spectra:
        log = spectral_logs.get(str(item.get("log_id") or ""), {})
        for wavelength in log.get("wavelengths") or []:
            column = wavelength_column(wavelength, log.get("wavelength_units"))
            if column not in all_wave_columns:
                all_wave_columns.append(column)
    wave_index = {column: index for index, column in enumerate(all_wave_columns)}
    header = [
        "hole_id",
        "signal_name",
        "signal_type",
        "status",
        "sample_no",
        "depth_from_m",
        "depth_to_m",
        "channel_units",
        *all_wave_columns,
        "values_json",
    ]
    handle, writer = open_csv_writer(path)
    rows_written = 0
    series_count = len(spectral_manifest) + len(profiler_manifest)
    try:
        writer.writerow(header)
        for item in spectral_manifest:
            log_id = str(item.get("log_id") or "")
            log = spectral_logs.get(log_id, {})
            name = str(item.get("log_name") or log.get("log_name") or "Spectrum")
            status = str(item.get("status") or "metadata_only")
            sample_count = int(item.get("sample_count") or 0)
            band_count = int(item.get("band_count") or 0)
            if status != "complete":
                writer.writerow([hole_id, name, "spectrum", status, "", "", "", log.get("wavelength_units") or "", *([""] * len(all_wave_columns)), ""])
                rows_written += 1
                continue
            source = raw_dir / str(item.get("file") or "")
            expected = sample_count * band_count * 4
            if not source.is_file() or source.stat().st_size != expected:
                raise ValueError(f"invalid spectral source: {source}")
            columns = [
                wavelength_column(value, log.get("wavelength_units"))
                for value in (log.get("wavelengths") or [])
            ]
            if len(columns) != band_count:
                raise ValueError(f"wavelength count mismatch for {name}")
            positions = [wave_index[column] for column in columns]
            with source.open("rb") as source_handle:
                for sample_no in range(sample_count):
                    raw = source_handle.read(band_count * 4)
                    if len(raw) != band_count * 4:
                        raise ValueError(f"short spectral row in {source.name}")
                    values = struct.unpack(f"<{band_count}f", raw)
                    wave_values = [""] * len(all_wave_columns)
                    for position, value in zip(positions, values):
                        wave_values[position] = f"{value:.8g}"
                    depth_from = axis[sample_no][1] if sample_no < len(axis) else None
                    depth_to = axis[sample_no][2] if sample_no < len(axis) else None
                    writer.writerow(
                        [
                            hole_id,
                            name,
                            "spectrum",
                            status,
                            sample_no,
                            "" if depth_from is None else f"{depth_from:g}",
                            "" if depth_to is None else f"{depth_to:g}",
                            log.get("wavelength_units") or "",
                            *wave_values,
                            "",
                        ]
                    )
                    rows_written += 1

        for item in profiler_manifest:
            log_id = str(item.get("log_id") or "")
            log = profiler_logs.get(log_id, {})
            name = str(item.get("log_name") or log.get("log_name") or "Profilometer")
            status = str(item.get("status") or "metadata_only")
            if status != "complete":
                writer.writerow([hole_id, name, "profilometer", status, "", "", "", "channel", *([""] * len(all_wave_columns)), ""])
                rows_written += 1
                continue
            source = raw_dir / str(item.get("file") or "")
            if not source.is_file():
                raise ValueError(f"missing profilometer source: {source}")
            with source.open("r", encoding="utf-8") as source_handle:
                for sample_no, line in enumerate(source_handle):
                    if not line.strip():
                        continue
                    payload = json.dumps(json.loads(line), ensure_ascii=False, separators=(",", ":"))
                    depth_from = axis[sample_no][1] if sample_no < len(axis) else None
                    depth_to = axis[sample_no][2] if sample_no < len(axis) else None
                    writer.writerow([hole_id, name, "profilometer", status, sample_no, "" if depth_from is None else f"{depth_from:g}", "" if depth_to is None else f"{depth_to:g}", "channel", *([""] * len(all_wave_columns)), payload])
                    rows_written += 1
    finally:
        handle.close()
    return rows_written, series_count


def write_assets(
    path: Path,
    data_root: Path,
    raw_dir: Path,
    hole_id: str,
) -> int:
    manifest = read_json(raw_dir / "images_manifest.json") if (raw_dir / "images_manifest.json").is_file() else {"logs": [], "files": []}
    header = [
        "hole_id",
        "asset_kind",
        "status",
        "dataset_name",
        "asset_name",
        "sample_no",
        "depth_from_m",
        "depth_to_m",
        "file_path",
        "nbytes",
        "notes",
    ]
    datasets = {
        str(item.get("dataset_id") or ""): str(item.get("dataset_name") or "")
        for item in (read_json(raw_dir / "datasets.json") if (raw_dir / "datasets.json").is_file() else [])
    }
    files_by_log = Counter(str(item.get("log_id") or "") for item in manifest.get("files", []))
    handle, writer = open_csv_writer(path)
    count = 0
    try:
        writer.writerow(header)
        for item in manifest.get("files", []):
            writer.writerow(
                [
                    hole_id,
                    item.get("kind") or "asset",
                    "complete",
                    datasets.get(str(item.get("dataset_id") or ""), ""),
                    item.get("log_name") or item.get("kind") or "asset",
                    "" if item.get("sample_no") is None else item.get("sample_no"),
                    "" if item.get("depth_from") is None else item.get("depth_from"),
                    "" if item.get("depth_to") is None else item.get("depth_to"),
                    source_path(data_root, raw_dir, item.get("file")),
                    int(item.get("nbytes") or 0),
                    "",
                ]
            )
            count += 1
        for item in manifest.get("logs", []):
            log_id = str(item.get("log_id") or "")
            status = str(item.get("status") or "metadata_only")
            if status == "complete" and files_by_log[log_id]:
                continue
            writer.writerow(
                [
                    hole_id,
                    safe_column(item.get("log_name"), "image"),
                    status,
                    datasets.get(str(item.get("dataset_id") or ""), ""),
                    item.get("log_name") or "image",
                    "",
                    "",
                    "",
                    "",
                    0,
                    f"downloaded {int(item.get('downloaded') or 0)} of {int(item.get('sample_count') or 0)}",
                ]
            )
            count += 1
    finally:
        handle.close()
    return count


def dataset_instruments(datasets: list[dict]) -> str:
    result = []
    for dataset in datasets:
        description = str(dataset.get("description") or "")
        if not description:
            continue
        try:
            value = ET.fromstring(description).findtext("InstrumentName")
            if value and value.strip() not in result:
                result.append(value.strip())
        except ET.ParseError:
            continue
    return "; ".join(result)


def build_hole_summary(
    hole_id: str,
    borehole: dict,
    datasets: list[dict],
    package: dict,
    axis: list[tuple[int, float, float]],
    measurement_columns: int,
    core_count: int,
    signal_series_count: int,
    asset_count: int,
) -> dict:
    names = [str(item.get("dataset_name") or "").strip() for item in datasets]
    return {
        "hole_id": hole_id,
        "hole_name": borehole.get("name") or hole_id,
        "longitude": borehole.get("x"),
        "latitude": borehole.get("y"),
        "elevation_m": borehole.get("elevation_m"),
        "borehole_length_m": borehole.get("boreholeLength_m"),
        "drill_start_date": borehole.get("drillStartDate"),
        "drill_end_date": borehole.get("drillEndDate"),
        "operator": borehole.get("operator"),
        "driller": borehole.get("driller"),
        "project": borehole.get("project"),
        "purpose": borehole.get("purpose"),
        "drilling_method": borehole.get("drillingMethod"),
        "status": borehole.get("status"),
        "dataset_count": len(datasets),
        "dataset_names": "; ".join(name for name in names if name),
        "instruments": dataset_instruments(datasets),
        "sample_count": len(axis),
        "depth_from_m": min(row[1] for row in axis),
        "depth_to_m": max(row[2] for row in axis),
        "measurement_columns": measurement_columns,
        "core_interval_count": core_count,
        "signal_series_count": signal_series_count,
        "asset_record_count": asset_count,
        "download_status": package.get("status") or "unknown",
        "etl_time": utc_now(),
    }


def commit_staged_files(csv_root: Path, hole_id: str, staging: Path) -> None:
    targets = {
        "measurements.csv": csv_root / "measurements" / f"{hole_id}.csv",
        "core_intervals.csv": csv_root / "core_intervals" / f"{hole_id}.csv",
        "signals.csv": csv_root / "signals" / f"{hole_id}.csv",
        "assets.csv": csv_root / "assets" / f"{hole_id}.csv",
    }
    for source_name, target in targets.items():
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(staging / source_name, target)


def transform_hole(data_root: Path, raw_root: Path, hole_id: str) -> dict:
    raw_dir = raw_root / hole_id
    if not raw_dir.is_dir():
        raise FileNotFoundError(f"raw package not found: {raw_dir}")
    borehole = read_json(raw_dir / "borehole.json")
    datasets = read_json(raw_dir / "datasets.json")
    logs = read_json(raw_dir / "logs_scalar.json")
    package = read_json(raw_dir / "package_manifest.json") if (raw_dir / "package_manifest.json").is_file() else {}
    sources = [scan_scalar(log, find_scalar_file(raw_dir, str(log.get("log_id") or ""))) for log in logs]
    sources_by_id = {item.log_id: item for item in sources}
    special_ids = {
        str(value)
        for dataset in datasets
        for value in (dataset.get("tray_id"), dataset.get("section_id"))
        if value
    }
    _, axis = choose_axis(sources, special_ids)
    columns, duplicate_sources, omitted_empty = build_measurement_columns(sources, special_ids)

    csv_root = data_root / "csv"
    staging = csv_root / ".staging" / hole_id
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    try:
        write_measurements(staging / "measurements.csv", hole_id, axis, columns)
        core_count = write_core_intervals(
            staging / "core_intervals.csv",
            data_root,
            raw_dir,
            hole_id,
            datasets,
            sources_by_id,
            axis,
        )
        signal_rows, signal_series_count = write_signals(
            staging / "signals.csv", raw_dir, hole_id, axis
        )
        asset_count = write_assets(
            staging / "assets.csv", data_root, raw_dir, hole_id
        )
        for path in staging.glob("*.csv"):
            if path.stat().st_size == 0:
                raise ValueError(f"empty output: {path}")
        commit_staged_files(csv_root, hole_id, staging)
    finally:
        if staging.exists():
            shutil.rmtree(staging)

    summary = build_hole_summary(
        hole_id,
        borehole,
        datasets,
        package,
        axis,
        len(columns),
        core_count,
        signal_series_count,
        asset_count,
    )
    summary.update(
        measurement_rows=len(axis),
        signal_rows=signal_rows,
        duplicate_scalar_sources_collapsed=duplicate_sources,
        empty_scalar_sources_omitted=omitted_empty,
    )
    atomic_write_json(
        data_root / "state" / f"{hole_id}.json",
        {"status": "complete", "summary": summary},
    )
    return summary


def rebuild_holes_csv(data_root: Path) -> int:
    rows = []
    for state_path in sorted((data_root / "state").glob("*.json")):
        try:
            state = read_json(state_path)
        except (OSError, ValueError):
            continue
        summary = state.get("summary")
        hole_id = str((summary or {}).get("hole_id") or "")
        expected = [
            data_root / "csv" / folder / f"{hole_id}.csv"
            for folder in ("measurements", "core_intervals", "signals", "assets")
        ]
        if state.get("status") == "complete" and summary and all(path.is_file() for path in expected):
            rows.append(summary)
    path = data_root / "csv" / "holes.csv"
    temp = path.with_name(path.name + ".tmp")
    handle, writer = open_csv_writer(temp)
    try:
        writer.writerow(HOLE_FIELDS)
        for row in sorted(rows, key=lambda item: item["hole_id"]):
            writer.writerow([row.get(field, "") for field in HOLE_FIELDS])
    finally:
        handle.close()
    path.parent.mkdir(parents=True, exist_ok=True)
    os.replace(temp, path)
    return len(rows)


def discover_holes(raw_root: Path) -> list[str]:
    return sorted(
        path.name
        for path in raw_root.iterdir()
        if path.is_dir() and (path / "borehole.json").is_file()
    ) if raw_root.is_dir() else []


def main() -> int:
    parser = argparse.ArgumentParser(description="把 NVCL 原始包转换成 5 类可读 CSV")
    parser.add_argument("hole_ids", nargs="*", help="钻孔号；省略时处理 raw 下全部钻孔")
    parser.add_argument("--holes-file", help="钻孔号文本或 CSV")
    parser.add_argument("--out", default=str(HERE / "data"), help="数据根目录")
    parser.add_argument("--raw-root", help="原始包根目录（默认 <out>/raw）")
    args = parser.parse_args()
    data_root = Path(args.out).resolve()
    raw_root = Path(args.raw_root).resolve() if args.raw_root else data_root / "raw"
    holes = (
        load_hole_ids(args.hole_ids, args.holes_file)
        if args.hole_ids or args.holes_file
        else discover_holes(raw_root)
    )
    if not holes:
        parser.error(f"no raw packages found under {raw_root}")
    results = []
    failed = 0
    for index, hole_id in enumerate(holes, start=1):
        print(f"[{index}/{len(holes)}] CSV ETL {hole_id}", flush=True)
        try:
            summary = transform_hole(data_root, raw_root, hole_id)
            results.append(summary)
            print(
                f"[{hole_id}] complete: measurements={summary['measurement_rows']}, "
                f"columns={summary['measurement_columns']}, signals={summary['signal_rows']}",
                flush=True,
            )
        except Exception as exc:  # noqa: BLE001 - one bad hole must not stop the batch
            failed += 1
            error = f"{type(exc).__name__}: {exc}"
            atomic_write_json(
                data_root / "state" / f"{hole_id}.json",
                {
                    "status": "failed",
                    "hole_id": hole_id,
                    "error": error,
                    "traceback": traceback.format_exc(),
                },
            )
            print(f"[{hole_id}] FAIL {error}", flush=True)
    hole_count = rebuild_holes_csv(data_root)
    batch = {
        "generated_at": utc_now(),
        "requested": len(holes),
        "complete": len(results),
        "failed": failed,
        "holes_in_summary": hole_count,
    }
    atomic_write_json(data_root / "etl-batch-summary.json", batch)
    print(json.dumps(batch, ensure_ascii=False, indent=2))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

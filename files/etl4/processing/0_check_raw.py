"""Check raw files and separate single-dataset, multi-dataset, and pending boreholes.
先检查 raw 文件是否齐全，再分出单 dataset、多 dataset 和待处理钻孔。
"""

import argparse
import json
import os
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _settings import StepParser, settings, configure


# 这里只检查文件和清单，不解析大文件，也不连接数据库。 / Check only files and manifests; do not parse large payloads or connect to the database.
METADATA = {
    "borehole.json": dict,
    "datasets.json": list,
    "package_manifest.json": dict,
    "logs_scalar.json": list,
    "scalar_manifest.json": list,
    "logs_spectral.json": list,
    "spectral_manifest.json": list,
    "logs_profilometer.json": list,
    "profilometer_manifest.json": list,
    "logs_image.json": list,
    "image_logs_by_dataset.json": list,
    "images_manifest.json": dict,
    "tray_depths.json": dict,
}
CATEGORIES = {
    "scalars": ("Scalar", "scalar_manifest.json"),
    "spectral": ("Spectral", "spectral_manifest.json"),
    "profilometer": ("Profilometer", "profilometer_manifest.json"),
    "images": ("Image", "images_manifest.json"),
}
ROUTES = {
    "single": "Single dataset: steps 1–10, then steps 20–29 as needed",
    "multi": "Multiple datasets: run steps 31–35 separately after checking source evidence and the source release",
    "attention": "Resolve alerts or empty categories before selecting subsequent steps",
}


def write_text(path, text):
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def read_metadata(folder, errors):
    data = {}
    for name, expected_type in METADATA.items():
        try:
            value = json.loads((folder / name).read_text(encoding="utf-8-sig"))
            if not isinstance(value, expected_type):
                raise ValueError("Incorrect top-level JSON structure")
            if expected_type is list and any(not isinstance(row, dict) for row in value):
                raise ValueError("The list contains non-object records")
            data[name] = value
        except FileNotFoundError:
            errors.append(f"Missing {name}")
        except (OSError, ValueError) as error:
            errors.append(f"{name} is missing or unreadable: {error}")
    return data


def payload_path(folder, relative):
    """Keep manifest paths inside the current borehole directory.
    清单中的路径必须留在当前孔内。
    """
    if not isinstance(relative, str) or not relative:
        raise ValueError("Missing filename")
    path = Path(relative)
    if path.is_absolute() or path.drive or ".." in path.parts or ":" in relative:
        raise ValueError(f"Invalid file path: {relative}")
    resolved = (folder / path).resolve()
    if not resolved.is_relative_to(folder) or resolved == folder:
        raise ValueError(f"The file path escapes the borehole directory: {relative}")
    return resolved


def check_category(folder, kind, manifest, errors, warnings):
    label, manifest_name = CATEGORIES[kind]
    entries = manifest.get("files") if kind == "images" and isinstance(manifest, dict) else manifest
    if entries is None:
        if manifest is not None:
            errors.append(f"{manifest_name} is missing the files list")
        entries = []
    if not isinstance(entries, list) or any(not isinstance(row, dict) for row in entries):
        errors.append(f"{manifest_name} has an invalid files list")
        entries = []

    expected, available = set(), set()
    declared_empty = 0
    metadata_only = 0
    for entry in entries:
        relative = entry.get("file")
        status = entry.get("status", "complete" if kind == "images" else None)
        if status in ("unavailable", "metadata_only"):
            # 未提供的数据有时仍带占位文件名，不能将其当成漏下载。 / Unavailable data may still have placeholder filenames; these are not missed downloads.
            declared_empty += status == "unavailable"
            metadata_only += status == "metadata_only"
            try:
                if relative and payload_path(folder, relative).exists():
                    errors.append(f"{relative} is marked {status}, but the file exists")
            except (OSError, ValueError) as error:
                errors.append(f"Invalid {label} placeholder path: {error}")
            continue
        if status != "complete":
            errors.append(f"Incomplete {label} record: {entry.get('log_id', relative)} ({status})")
            continue
        try:
            path = payload_path(folder, relative)
            if path in expected:
                errors.append(f"{manifest_name} lists the file more than once: {relative}")
            expected.add(path)
            if not path.is_file():
                errors.append(f"The manifest marks the file as provided, but it is missing: {relative}")
                continue
            size = path.stat().st_size
            if size == 0:
                errors.append(f"Empty data file: {relative}")
            elif not isinstance(entry.get("nbytes"), int) or size != entry["nbytes"]:
                errors.append(f"File size differs from the manifest: {relative}")
            else:
                available.add(path)
        except (OSError, ValueError) as error:
            errors.append(f"Cannot check {label} file: {error}")

    # 图片清单也包含 mosaics；单有拼图不能代替 images 中的孔图。 / Image manifests also list mosaics; mosaics alone cannot replace borehole images in images/.
    actual = set()
    directories = ("images", "mosaics") if kind == "images" else (kind,)
    for directory in directories:
        for path in (folder / directory).rglob("*"):
            if path.is_file():
                actual.add(path.resolve())
    unlisted = sorted(actual - expected)
    for path in unlisted:
        label = "Found an incomplete .part download" if path.suffix == ".part" else "The file is not listed as a provided payload"
        errors.append(f"{label}: {path}")
    folder_files = sum(path.is_relative_to(folder / kind) for path in actual)
    if folder_files == 0:
        reason = "Marked unavailable by the source" if declared_empty else "Category directory is empty or missing"
        message = f"No {label} data: {reason}"
        if kind == "scalars":
            errors.append(message + "; base samples cannot be created")
        else:
            warnings.append(message + "; handle separately before using the current complete-data workflow")
    return {
        "file_count": folder_files,
        "expected_file_count": len(expected),
        "available_file_count": len(available),
        "unavailable_log_count": declared_empty,
        "metadata_only_log_count": metadata_only,
    }


def check_hole(folder):
    folder = folder.resolve()
    errors, warnings = [], []
    data = read_metadata(folder, errors)
    datasets = data.get("datasets.json", [])
    dataset_ids = [row.get("dataset_id") for row in datasets]
    if not datasets:
        errors.append("datasets.json contains no datasets; a processing route cannot be selected")
    elif any(not isinstance(value, str) or not value for value in dataset_ids):
        errors.append("A dataset is missing a valid dataset_id")
    elif len(set(dataset_ids)) != len(dataset_ids):
        errors.append("datasets.json contains duplicate dataset_id values")

    if "borehole.json" in data and not data["borehole.json"].get("nvcl_id"):
        errors.append("borehole.json is missing the borehole ID nvcl_id")
    elif "borehole.json" in data and data["borehole.json"]["nvcl_id"] != folder.name:
        errors.append("The borehole ID in borehole.json differs from the directory name")
    package = data.get("package_manifest.json", {})
    if "package_manifest.json" in data:
        if package.get("hole_id") != folder.name:
            errors.append("The borehole ID in package_manifest.json differs from the directory name")
        if package.get("status") not in ("complete", "complete_with_warnings"):
            errors.append(f"The raw package is incomplete: {package.get('status')}")
        if package.get("errors"):
            errors.append(f"The download manifest records errors: {package['errors']}")
    categories = {}
    for kind, (_, manifest_name) in CATEGORIES.items():
        categories[kind] = check_category(folder, kind, data.get(manifest_name), errors, warnings)

    # 托盘和行段是后续建立样本轴的必要输入；此处只核对引用存在。 / Trays and sections are needed for the sample axis; only check reference existence here.
    scalar_logs = {row.get("log_id") for row in data.get("scalar_manifest.json", [])}
    for dataset in datasets:
        for field in ("tray_id", "section_id"):
            if not dataset.get(field) or dataset[field] not in scalar_logs:
                errors.append(f"Dataset {dataset.get('dataset_id')} is missing the scalar record for {field}")
    route = "multi" if len(datasets) > 1 else "single"
    if errors or warnings:
        route = "attention"
    return {
        "hole_id": folder.name,
        "dataset_count": len(datasets) if "datasets.json" in data else None,
        "datasets": [{"dataset_id": row.get("dataset_id"), "dataset_name": row.get("dataset_name")} for row in datasets],
        "status": "error" if errors else "warning" if warnings else "passed",
        "route": route,
        "categories": categories,
        "errors": errors,
        "warnings": warnings,
        "source_warnings": package.get("warnings", []),
    }


def scan_raw(raw_dir):
    folders = sorted(path for path in raw_dir.iterdir() if path.is_dir())
    if not folders:
        raise ValueError("The raw directory contains no borehole directories")
    holes = []
    for folder in folders:
        try:
            result = check_hole(folder)
        except (OSError, ValueError, TypeError) as error:
            result = {
                "hole_id": folder.name, "dataset_count": None, "datasets": [],
                "status": "error", "route": "attention", "categories": {},
                "errors": [f"Cannot complete the precheck: {error}"], "warnings": [], "source_warnings": [],
            }
        holes.append(result)
    return holes


def write_reports(output_dir, raw_dir, holes, elapsed, run_id):
    output_dir.mkdir(parents=True, exist_ok=True)
    groups = {name: [h["hole_id"] for h in holes if h["route"] == name] for name in ROUTES}
    report = {
        "raw_dir": str(raw_dir),
        "run_id": run_id,
        "scope": "Checks manifests, file existence, and sizes only; payload hashes, business content, and database import are outside this precheck",
        "elapsed_seconds": round(elapsed, 3),
        "hole_count": len(holes),
        "dataset_count": sum(h["dataset_count"] or 0 for h in holes),
        "unknown_dataset_count_holes": [h["hole_id"] for h in holes if h["dataset_count"] is None],
        "groups": groups,
        "holes": holes,
    }
    write_text(output_dir / "0_raw_check.json", json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    for name, members in groups.items():
        write_text(output_dir / f"{name}_holes.txt", "".join(hole + "\n" for hole in members))

    lines = ["# Raw input precheck", "", f"Source: `{raw_dir}`", "", report["scope"], "",
             f"{len(holes)} boreholes, {report['dataset_count']} datasets; elapsed time: {elapsed:.3f} seconds.", ""]
    for name, label in ROUTES.items():
        lines.append(f"- {label}: {len(groups[name])} boreholes (`{name}_holes.txt`)")
    lines += ["", "The precheck assigns routes without moving files or starting subsequent steps. Use the same --raw-dir and --work-dir to read the original packages in subsequent steps.", "",
              "Boreholes with an entirely missing category are listed as pending. This does not imply source corruption or support for empty categories in subsequent steps.", "",
              "## Boreholes with multiple datasets", "", "| Borehole ID | Dataset count | Dataset names and IDs | Status |", "|---|---:|---|---|"]
    for hole in holes:
        if (hole["dataset_count"] or 0) > 1:
            members = "; ".join(f"{row['dataset_name']} ({row['dataset_id']})" for row in hole["datasets"])
            lines.append(f"| {hole['hole_id']} | {hole['dataset_count']} | {members} | {hole['status']} |")
    lines += ["", "## Missing data and alerts", ""]
    for hole in holes:
        if hole["errors"] or hole["warnings"]:
            count = hole["dataset_count"] if hole["dataset_count"] is not None else "unknown count"
            lines += [f"### {hole['hole_id']} ({count} datasets)", ""]
            lines.extend(f"- Alert: {message}" for message in hole["errors"])
            lines.extend(f"- Warning: {message}" for message in hole["warnings"])
            lines.append("")
    lines += ["## Classification of all boreholes", "", "| Borehole ID | Dataset count | Scalar files | Spectral files | Profilometer files | Image files | Next steps |", "|---|---:|---:|---:|---:|---:|---|"]
    for hole in holes:
        counts = [str(hole["categories"].get(kind, {}).get("file_count", "not checked")) for kind in CATEGORIES]
        count = hole["dataset_count"] if hole["dataset_count"] is not None else "unknown"
        lines.append(f"| {hole['hole_id']} | {count} | {' | '.join(counts)} | {ROUTES[hole['route']]} |")
    write_text(output_dir / "0_raw_check.md", "\n".join(lines) + "\n")
    # 名单和报告全部写好，入口才可使用这一轮结果。 / Entry points may use this run only after all lists and reports have been written.
    write_text(output_dir / "0_status.json", json.dumps({"status": "complete", "run_id": run_id}))
    return groups


def main():
    parser = StepParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output-dir", type=Path, help="Report and classified-list directory; defaults to this batch working directory")
    args = parser.parse_args()
    configure(args)
    raw_dir = settings.raw_dir
    output_dir = args.output_dir.resolve() if args.output_dir else settings.processing_dir / "raw_check"
    if not raw_dir.is_dir():
        parser.error(f"The raw directory does not exist: {raw_dir}")
    if output_dir.is_relative_to(raw_dir):
        parser.error("Save reports outside the raw directory to keep them separate from raw files")
    started = time.perf_counter()
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        run_id = uuid.uuid4().hex
        write_text(output_dir / "0_status.json", json.dumps({"status": "running", "run_id": run_id}))
        holes = scan_raw(raw_dir)
        groups = write_reports(output_dir, raw_dir, holes, time.perf_counter() - started, run_id)
    except (OSError, ValueError) as error:
        print(f"Precheck failed: {error}", file=sys.stderr)
        return 2
    print(f"Checked {len(holes)} boreholes and identified {sum(h['dataset_count'] or 0 for h in holes)} datasets.")
    for name, label in ROUTES.items():
        print(f"{label}: {len(groups[name])} boreholes")
    for hole in holes:
        if (hole["dataset_count"] or 0) > 1:
            print(f"[Multiple datasets] {hole['hole_id']}: {hole['dataset_count']}")
        for message in hole["errors"]:
            print(f"[Alert] {hole['hole_id']}: {message}")
        for message in hole["warnings"]:
            print(f"[Warning] {hole['hole_id']}: {message}")
    print(f"Report: {output_dir / '0_raw_check.md'}")
    return 2 if any(h["errors"] for h in holes) else 1 if any(h["warnings"] for h in holes) else 0


if __name__ == "__main__":
    sys.exit(main())

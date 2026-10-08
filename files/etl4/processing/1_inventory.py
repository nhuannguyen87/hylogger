"""Inventory raw files for the selected boreholes before content validation.
清点选定钻孔的原始文件，为后续内容校验建立清单。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from collections import Counter
from processing._etl_common import (
    METADATA,
    digest,
    fail_main,
    pipeline_id,
    run_holes,
    sha_file,
    source_json,
    source_path,
    write_json,
    write_text,
)
from _settings import settings


def inventory_hole(hole):
    metadata = {name: source_json(hole, name) for name in METADATA}
    datasets = metadata["datasets.json"]
    if len(datasets) != 1:
        raise ValueError("This approved processing phase requires one dataset per input hole")
    package = metadata["package_manifest.json"]
    references = {}
    for kind, name in (
        ("scalar", "scalar_manifest.json"),
        ("spectral", "spectral_manifest.json"),
        ("profile", "profilometer_manifest.json"),
        ("image", "images_manifest.json"),
    ):
        entries = metadata[name]["files"] if kind == "image" else metadata[name]
        for entry in entries:
            relative = entry.get("file")
            if not relative:
                continue
            source_path(hole, relative)
            if relative in references:
                raise ValueError(f"Payload listed more than once: {relative}")
            if not entry.get("sha256") or len(entry["sha256"]) != 64:
                raise ValueError(f"Missing payload SHA256: {relative}")
            references[relative] = {"kind": kind, "manifest": name, "entry": entry}
    files, seen = [], set()
    raw_folder = source_path(hole, "borehole.json").parent
    for path in sorted(raw_folder.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"Symbolic links are not permitted in raw packages: {path.name}")
        if not path.is_file():
            continue
        relative = path.relative_to(raw_folder).as_posix()
        path = source_path(hole, relative)
        ref = references.get(relative)
        if ref is None and relative not in (*METADATA, "download-report.txt"):
            raise ValueError(f"Unlisted source file needs review: {relative}")
        stat = path.stat()
        if ref and stat.st_size != ref["entry"]["nbytes"]:
            raise ValueError(f"File size differs from manifest: {relative}")
        files.append(
            {
                "path": relative,
                "size_bytes": stat.st_size,
                "kind": ref["kind"] if ref else "metadata",
                "expected_sha256": ref["entry"]["sha256"] if ref else sha_file(path),
                "manifest_reference": ref,
            }
        )
        seen.add(relative)
    missing = set(references) - seen
    if missing:
        raise ValueError(f"Manifest payload missing: {sorted(missing)}")
    source_id = digest(
        [{k: f[k] for k in ("path", "size_bytes", "expected_sha256")} for f in files]
    )
    code_id = pipeline_id(dataset_id=datasets[0]["dataset_id"])
    folder = settings.prepared_dir / hole / source_id[:16] / code_id[:16]
    result = {
        "step": 1,
        "hole_id": hole,
        "status": "passed",
        "source_snapshot_id": source_id,
        "pipeline_id": code_id,
        "snapshot_id_basis": "manifest payload hashes plus actual metadata hashes; payload hashes verified in step 2",
        "source_package_fingerprint": package["content_fingerprint"],
        "source_dataset_id": datasets[0]["dataset_id"],
        "file_count": len(files),
        "file_counts_by_kind": dict(Counter(f["kind"] for f in files)),
        "source_bytes": sum(f["size_bytes"] for f in files),
        "files": files,
    }
    write_json(folder / "1_inventory.json", result)
    write_json(
        settings.prepared_dir / hole / "latest_processing.json",
        {
            "relative_directory": folder.relative_to(settings.prepared_dir).as_posix(),
            "source_snapshot_id": source_id,
            "pipeline_id": code_id,
            "purpose": "processing workspace pointer, not a production publication pointer",
        },
    )
    write_text(
        folder / "1_inventory.md",
        f"# {hole} Input inventory\n\n"
        f"Status: passed. {len(files)} files, {result['source_bytes']:,} bytes.\n\n"
        "This step checks source scope, file inventory, and sizes; step 2 validates payload hashes and structure.\n",
    )
    print(f"  {len(files)} files; {result['source_bytes']:,} bytes; {folder}", flush=True)


def main():
    run_holes("step 1 inventory", inventory_hole)


if __name__ == "__main__":
    fail_main(main)

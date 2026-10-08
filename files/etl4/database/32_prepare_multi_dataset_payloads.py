"""Prepare each dataset with an explicitly supplied parsing context.
逐个准备 dataset，显式传入解析上下文。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import copy
from _db_common import ETL
from _db_files import digest, inside, read, sha, write
from _run_reports import main_guard
from _multi_common import audit, raw_metadata, require
from _settings import StepParser
from _settings import settings, configure, add_hole_arguments, selected_holes, raw_path, path_reference, resolve_reference
from processing._etl_common import DatasetContext, atomic_bytes, pipeline_id, write_json
from processing.payload_validation import validate_hole
from processing.metadata_normalization import normalize_hole
from processing.sample_alignment import align_hole
from processing.columnar_storage import build_hole


def selected_metadata(raw, dataset):
    metadata = copy.deepcopy(raw)
    dataset_id = dataset["source_dataset_id"]
    log_ids = dataset["log_ids"]
    metadata["datasets.json"] = [
        d for d in metadata["datasets.json"] if d["dataset_id"] == dataset_id
    ]
    for kind, names in {
        "scalar": ("logs_scalar.json", "scalar_manifest.json"),
        "spectral": ("logs_spectral.json", "spectral_manifest.json"),
        "profile": ("logs_profilometer.json", "profilometer_manifest.json"),
        "image": ("logs_image.json", "image_logs_by_dataset.json"),
    }.items():
        for name in names:
            metadata[name] = [x for x in metadata[name] if x["log_id"] in log_ids[kind]]
    metadata["images_manifest.json"] = {
        k: [x for x in v if x["dataset_id"] == dataset_id]
        for k, v in metadata["images_manifest.json"].items()
    }
    metadata["tray_depths.json"] = {
        k: v for k, v in metadata["tray_depths.json"].items() if k in log_ids["image"]
    }
    return metadata


def dataset_input_identity(metadata, dataset, files):
    # 共享清单的其他 dataset 变化，不属于本 dataset 的数据身份。 / Changes to other datasets in a shared manifest do not affect this dataset identity.
    projected = {name: value for name, value in metadata.items() if name != "package_manifest.json"}
    return digest(
        {
            "dataset": dataset["source_dataset_id"],
            "metadata": projected,
            "membership": {
                kind: {key: value for key, value in proof.items() if key != "path"}
                for kind, proof in dataset["membership_evidence"].items()
            },
            "payloads": [
                {k: f[k] for k in ("path", "size_bytes", "expected_sha256")}
                for f in files
                if f["kind"] != "metadata"
            ],
        }
    )


def inventory(hole, metadata, dataset, processing_id, prepared_dir):
    references = {}
    for kind, name in [
        ("scalar", "scalar_manifest.json"),
        ("spectral", "spectral_manifest.json"),
        ("profile", "profilometer_manifest.json"),
        ("image", "images_manifest.json"),
    ]:
        entries = metadata[name]["files"] if kind == "image" else metadata[name]
        for e in entries:
            if e.get("file"):
                require(e["file"] not in references, "Duplicate raw file reference")
                references[e["file"]] = {"kind": kind, "manifest": name, "entry": e}
    files = []
    for relative_path in sorted(set(references) | set(metadata) | {"download-report.txt"}):
        path = raw_path(hole, relative_path)
        require(path.is_file() and not path.is_symlink(), "Missing or indirect source file")
        stat = path.stat()
        ref = references.get(relative_path)
        if ref:
            require(stat.st_size == ref["entry"]["nbytes"], "Source size differs from manifest")
        files.append(
            {
                "path": relative_path,
                "size_bytes": stat.st_size,
                "kind": ref["kind"] if ref else "metadata",
                "manifest_reference": ref,
                "expected_sha256": ref["entry"]["sha256"] if ref else sha(path),
            }
        )
    source_id = dataset_input_identity(metadata, dataset, files)
    folder = (
        prepared_dir
        / "multidataset"
        / hole
        / dataset["source_dataset_id"]
        / source_id[:16]
        / processing_id[:16]
    )
    return folder, {
        "step": 1,
        "hole_id": hole,
        "status": "passed",
        "pipeline_id": processing_id,
        "source_snapshot_id": source_id,
        "source_dataset_id": dataset["source_dataset_id"],
        "file_count": len(files),
        "source_bytes": sum(f["size_bytes"] for f in files),
        "files": files,
        "membership_evidence": dataset["membership_evidence"],
        "raw_metadata_scope": "Full hole-level documents; parsers receive an explicit dataset projection",
    }


def reuse_prepared_result(folder, hole, input_manifest, dataset_name):
    completed = folder / "32_unit.json"
    if completed.exists():
        cached = read(completed)
        require(
            all(
                sha(folder / relative_path) == checksum
                for relative_path, checksum in cached["prepared_hashes"].items()
            ),
            "Cached prepared bytes changed",
        )
        for item in input_manifest["files"]:
            require(
                sha(raw_path(hole, item["path"])) == item["expected_sha256"], "Source content changed"
            )
        print(f"Reuse verified dataset {dataset_name}", flush=True)
        return cached


def save_prepared_result(unit, folder, input_manifest, processing_id, metadata):
    hashes = {
        p.relative_to(folder).as_posix(): sha(p)
        for p in sorted(folder.rglob("*"))
        if p.is_file() and p.name != "32_unit.json"
    }
    summary = {
        **unit,
        "status": "passed",
        "prepared_directory": path_reference(folder),
        "pipeline_id": processing_id,
        "dataset_id": metadata["dataset"]["id"],
        "dataset_revision_id": metadata["dataset_revision"]["id"],
        "axis_id": metadata["proposed_axis_id"],
        "source_snapshot_id": input_manifest["source_snapshot_id"],
        "prepared_hashes": hashes,
        "report_sha256": {
            n: hashes[n]
            for n in (
                "2_integrity.json",
                "3_metadata_report.json",
                "4_alignment_report.json",
                "5_storage_report.json",
            )
        },
    }
    write_json(folder / "32_unit.json", summary)
    return summary


def prepare_unit(unit, source, prepared_dir, database_dir):
    hole = unit["hole_id"]
    raw = raw_metadata(hole)
    for name, checksum in source["raw_metadata_sha256"][hole].items():
        require(sha(raw_path(hole, name)) == checksum, "Raw metadata changed since step 31")
    for proof in unit["membership_evidence"].values():
        if "path" in proof:
            require(
                sha(database_dir / proof["path"]) == proof["sha256"], "Membership evidence changed"
            )
    projected = selected_metadata(raw, unit)
    processing_id = pipeline_id(
        {"image_depth_policy": "decimal_1e-5_or_same_float32"}, unit["source_dataset_id"]
    )
    folder, input_manifest = inventory(hole, projected, unit, processing_id, prepared_dir)
    # 小型来源文档随版本封存，旧版本不再引用会更新的整孔清单。 / Seal small source documents with each version so older versions do not reference changing hole manifests.
    for item in input_manifest["files"]:
        if item["kind"] == "metadata":
            snapshot_path = folder / "source_metadata" / item["path"]
            item["source_object_key"] = path_reference(snapshot_path)
    saved = reuse_prepared_result(folder, hole, input_manifest, unit["source_dataset_name"])
    if saved is not None:
        return saved
    baseline = {
        **unit,
        "scalar_log_count": len(projected["scalar_manifest.json"]),
        "spectral_array_count": sum(
            bool(x.get("file")) for x in projected["spectral_manifest.json"]
        ),
    }
    context = DatasetContext(hole, folder, input_manifest, projected, baseline)
    print(f"Prepare {hole}/{unit['source_dataset_name']}", flush=True)
    for item in input_manifest["files"]:
        if "source_object_key" in item:
            atomic_bytes(resolve_reference(item["source_object_key"]), raw_path(hole, item["path"]).read_bytes())
    write_json(folder / "1_inventory.json", input_manifest)
    validate_hole(hole, context=context)
    normalize_hole(
        hole,
        context=context,
        source_metadata=raw,
        dataset_selection={
            "source_dataset_id": unit["source_dataset_id"],
            "log_ids": unit["log_ids"],
            "membership_evidence": unit["membership_evidence"],
        },
        orientation_missing_reason="downhole_survey_not_provided",
        orientation_missing_reason_source="source_package_inspection; dataset XML orientation retained separately",
    )
    align_hole(hole, image_depth_policy="decimal_1e-5_or_same_float32", context=context)
    build_hole(hole, context=context)
    metadata = read(folder / "3_normalized_metadata.json")
    return save_prepared_result(unit, folder, input_manifest, processing_id, metadata)


def main():
    parser = StepParser()
    add_hole_arguments(parser)
    args = parser.parse_args()
    configure(args)
    source = audit()
    selected = selected_holes(args, source["scope"])
    require(set(selected) <= set(source["scope"]), "Selected holes are not in the step 31 manifest")

    units = []
    for unit in source["datasets"]:
        if unit["hole_id"] in selected:
            units.append(prepare_unit(unit, source, settings.prepared_dir, settings.database_dir))
            write(
                settings.multi_work_dir / "32_progress.json",
                {"status": "in_progress", "datasets": units},
            )
    require(units, "No selected datasets")
    write(
        settings.multi_work_dir / "32_prepared.json",
        {
            "status": "passed",
            "source_audit_sha256": sha(settings.multi_work_dir / "31_source_audit.json"),
            "datasets": units,
        },
    )
    print(f"Prepared {len(units)} independent datasets.")


if __name__ == "__main__":
    main_guard(main)

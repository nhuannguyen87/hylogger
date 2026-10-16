"""Seal prepared media packages, run the first acceptance, and write media records for the final version.
封存媒体准备包、执行第一次验收，并为最终版本写入媒体记录。
"""

import shutil
import hashlib
import uuid
from _db_common import insert, pick, uid
from _db_files import digest, encoded, inside, read, sha, write
from _run_reports import report
from _settings import settings, path_reference, raw_asset_path
from _media_common import media_code_hash, require, stage
from _media_manifest import pack_document, unpack_document
from _ingest import add_asset


def remap(value, ids):
    if isinstance(value, uuid.UUID):
        return ids.get(str(value), str(value))
    if isinstance(value, str):
        return ids.get(value, value)
    if isinstance(value, list):
        return [remap(v, ids) for v in value]
    if isinstance(value, dict):
        return {k: remap(v, ids) for k, v in value.items()}
    return value


MEDIA_RULE_VERSION = "sample-media-v2"
MEDIA_RULE_VERSIONS = {}  # 键为来源 dataset ID，只覆盖受影响的处理规则。 / Key overrides by source dataset ID and change only the affected processing rules.


def media_rule_version(unit):
    return MEDIA_RULE_VERSIONS.get(unit.get("source_dataset_id"), MEDIA_RULE_VERSION)


def media_identity(unit, arrays, rows, rule_version=None):
    """Derive identity only from dataset inputs and rules; store the actual output digest separately.
    身份只依赖该 dataset 的输入和规则；实际输出另存摘要。
    """
    identity = digest(
        {
            "dataset_id": unit["dataset_id"],
            "source_revision_id": unit["source_revision_id"],
            "source_snapshot_id": unit["source_snapshot_id"],
            "rules": rule_version or media_rule_version(unit),
        }
    )
    spectra = [
        pick(
            a,
            "log_id",
            "sha256",
            "shape",
            "dtype_code",
            "matrix_order",
            "sample_map",
            "blocks",
            "value_unit",
            "scaling_applied",
        )
        for a in arrays
    ]
    crops = [
        pick(
            r,
            "source_log_id",
            "source_frame_id",
            "source_section_id",
            "region_ordinal",
            "source_sha256",
            "x_px",
            "y_px",
            "width_px",
            "height_px",
            "crop_sha256",
            "pixel_sha256",
            "recipe",
            "sample_no_from",
            "sample_no_to",
            "region_status",
            "indicator_method",
            "indicator_version",
            "indicator_parameters",
            "source_depth_direction",
            "direction_status",
        )
        for r in rows
    ]
    return {
        "input_key": identity,
        "content_sha256": digest(
            {
                "arrays": sorted(spectra, key=lambda a: a["log_id"]),
                "rows": sorted(
                    crops,
                    key=lambda r: (r["source_log_id"], r["source_frame_id"], r["region_ordinal"]),
                ),
            }
        ),
    }


def package_step(destination, number):
    return unpack_document(read(destination / "steps" / f"{number}.json"))


def prepared_context(unit):
    """Rebuild base import arguments from the package manifest and use its file references.
    从包内清单重建基础导入的参数，文件按清单引用。
    """
    from _media_common import source_folder

    folder = source_folder(unit)
    for relative, checksum in unit["prepared_hashes"].items():
        require(
            sha(inside(folder, folder / relative)) == checksum,
            f"Prepared artifact changed: {relative}",
        )
    return {
        "prepared_hashes": unit["prepared_hashes"],
        "summary": unit["prepared_summary"],
        "folder": folder,
        "metadata": read(folder / "3_normalized_metadata.json"),
        "alignment": read(folder / "4_alignment_report.json"),
        "storage": read(folder / "5_storage_report.json"),
    }


def package_contexts(package, prepared_work=None):
    """Read prepared results for single or multiple datasets from the same manifest; handle older packages here.
    单、多 dataset 都从同一份包清单取得准备结果。旧包只在此读取。
    """
    from _prepared_inputs import prepared_holes

    prepared_work = prepared_work or settings.work_dir
    contexts, legacy = {}, None
    for dataset_id, selected in package["datasets"].items():
        unit = selected["unit"]
        if "prepared_hashes" in unit:
            if not unit.get("prepared_object_key"):
                unit["prepared_object_key"] = path_reference(
                    prepared_work / "processing/outputs" / unit["prepared_directory"])
            context = prepared_context(unit)
        else:
            if legacy is None:
                legacy = {h["metadata"]["dataset"]["id"]: h for h in prepared_holes(work_dir=prepared_work)}
            require(dataset_id in legacy, "A package dataset is not in the preparation manifest")
            context = legacy[dataset_id]
            unit["prepared_object_key"] = path_reference(context["folder"])
        metadata = context["metadata"]
        revision = metadata["dataset_revision"]
        require(
            metadata["dataset"]["id"] == dataset_id
            and revision["id"] == unit["source_revision_id"]
            and revision["source_snapshot_id"] == unit["source_snapshot_id"]
            and context["alignment"]["axis_id"] == unit["source_axis_id"]
            and context["alignment"]["sample_count"] == unit["sample_count"],
            "The media package and prepared files differ in dataset, version, or sample axis",
        )
        contexts[dataset_id] = context
    return contexts


def prepared_acceptance_content(package, contexts):
    """Bind acceptance to input content, independently of timestamps, source code, and local directories.
    验收绑定输入内容；时间、源码和本机目录不决定验收对象。
    """
    return {
        "package_sha256": package["key"],
        "datasets": {
            dataset: {
                "prepared_hashes": context["prepared_hashes"],
                "source_revision_id": context["metadata"]["dataset_revision"]["id"],
                "raw_assets": [pick(a, "source_relative_path", "byte_size", "sha256")
                               for a in context["storage"]["raw_assets"]],
            }
            for dataset, context in sorted(contexts.items())
        },
    }


def accept_prepared_package(package, prepared_work=None):
    """Run the first local acceptance by collecting processing checks and rechecking the complete package and its references.
    第一次本地验收：汇总处理检查，重新核对完整准备包及其引用。
    """
    path = settings.reports_dir / f"local_acceptance_1_{package['key']}.json"
    write(path, {"stage": 1, "status": "running", "package_sha256": package["key"]})
    try:
        contexts = package_contexts(package, prepared_work)
        for number, statuses in ((20, {"passed"}), (21, {"passed"}), (22, {"passed"}),
                                 (23, {"passed"}), (25, {"reviewed"}),
                                 (26, {"passed"}), (27, {"passed"})):
            require(f"steps/{number}.json" in package["manifest"]["files"],
                    f"Media step {number} is not in the sealed manifest")
            require(package_step(package["directory"], number).get("status") in statuses,
                    f"Media step {number} is incomplete")
        for dataset, context in contexts.items():
            folder = context["folder"]
            metadata = context["metadata"]["dataset_revision"]
            reports = ("1_inventory.json", "2_integrity.json", "3_metadata_report.json",
                       "4_alignment_report.json", "5_storage_report.json")
            for name in reports:
                value = read(folder / name)
                require(value["status"] in ("passed", "passed_with_issues")
                        and value["pipeline_id"] == metadata["pipeline_id"]
                        and value["source_snapshot_id"] == metadata["source_snapshot_id"],
                        f"The preparation report failed or combines different inputs: {name}")
                require(context["prepared_hashes"].get(name) == sha(folder / name),
                        f"The preparation report is not in the acceptance manifest: {name}")
            storage = context["storage"]
            for asset in storage["raw_assets"]:
                source = raw_asset_path(context["summary"]["hole_id"], asset)
                require(source.stat().st_size == asset["byte_size"] and sha(source) == asset["sha256"],
                        "Raw files do not match the prepared results")
            required = ["3_normalized_metadata.json", "4_sample_axis.npz", "4_axis_bindings.json",
                        "4_core_intervals.json", "4_image_frames.json"]
            required.extend(a["relative_path"] for a in storage["assets"])
            require(all(name in context["prepared_hashes"] for name in required),
                    "The preparation manifest is missing required outputs")
            for asset in storage["assets"]:
                target = inside(folder, folder / asset["relative_path"])
                require(target.stat().st_size == asset["byte_size"]
                        and sha(target) == asset["sha256"] == context["prepared_hashes"][asset["relative_path"]],
                        "Prepared outputs do not match the storage manifest")
        content = prepared_acceptance_content(package, contexts)
        result = {"stage": 1, "status": "passed", "content": content,
                  "content_sha256": digest(content), "dataset_count": len(contexts)}
        write(path, result)
        return path, result
    except Exception as error:
        write(path, {"stage": 1, "status": "failed", "package_sha256": package["key"],
                     "error_type": type(error).__name__})
        raise


def verify_media_sources(conn, unit, arrays, rows):
    """Check package media declarations against actual logs, images, and sample sources in the base version.
    核对包内媒体声明与基础版本的实际日志、图片和样本来源。
    """
    for array in arrays:
        source = conn.execute(
            """SELECT a.sha256,a.byte_size,s.wavelength_count FROM core.scan_log l
            JOIN core.log_asset la ON la.log_id=l.id JOIN core.asset a ON a.id=la.asset_id
            JOIN core.spectral_stream s ON s.id=l.spectral_stream_id
            WHERE l.id=%s AND l.dataset_revision_id=%s AND l.source_log_id=%s
            AND a.representation='raw' AND a.logical_path=%s""",
            (array["source_log_pk"], unit["source_revision_id"], array["log_id"], array["file"]),
        ).fetchone()
        require(
            source
            and source["sha256"] == array["sha256"]
            and source["wavelength_count"] == array["band_count"]
            and array["sample_count"] == unit["sample_count"]
            and source["byte_size"] == array["sample_count"] * array["band_count"] * 4,
            "Spectral source, dimensions or dataset differs",
        )
    for row in rows:
        source = conn.execute(
            """SELECT a.sha256,a.logical_path,l.source_log_id,f.image_ordinal
            FROM core.image_frame f JOIN core.asset a ON a.id=f.asset_id
            JOIN core.scan_log l ON l.id=f.log_id
            WHERE f.id=%s AND f.dataset_revision_id=%s""",
            (row["source_frame_id"], unit["source_revision_id"]),
        ).fetchone()
        require(
            source
            and source["logical_path"] == row["source_path"]
            and source["source_log_id"] == row["source_log_id"]
            and source["image_ordinal"] == row["image_ordinal"]
            and source["sha256"] == row["source_sha256"] == row["recipe"]["source_sha256"],
            "Crop refers to a different source frame",
        )


def check_package(destination, manifest):
    """Check the selected input package and organize the required manifests by dataset.
    核对本次输入包，并按 dataset 整理后续要用的清单。
    """
    require(destination.name == digest(manifest), "Package manifest identity differs")
    for relative, item in manifest["files"].items():
        path = inside(destination, destination / relative)
        require(
            path.is_file()
            and path.stat().st_size == item["byte_size"]
            and sha(path) == item["sha256"],
            f"Packaged file differs: {relative}",
        )
    source = package_step(destination, 20)
    units = source["datasets"]
    dataset_ids = [unit["dataset_id"] for unit in units]
    require(
        dataset_ids
        and len(dataset_ids) == len(set(dataset_ids))
        and set(dataset_ids) == set(manifest["datasets"]),
        "Package dataset membership differs",
    )
    arrays = package_step(destination, 22)["arrays"]
    rows = package_step(destination, 27)["rows"]
    datasets = {unit["dataset_id"]: {"unit": unit, "arrays": [], "rows": []} for unit in units}
    for field, items in (("arrays", arrays), ("rows", rows)):
        for item in items:
            require(item["dataset_id"] in datasets, "Media belongs to an unlisted dataset")
            datasets[item["dataset_id"]][field].append(item)
    for unit in units:
        dataset = unit["dataset_id"]
        selected = datasets[dataset]
        recorded = manifest["datasets"][dataset]
        # 未单独覆盖时，仍按包内保存的共同规则复查历史版本。 / Without an individual override, recheck historical versions using the shared rules saved in the package.
        rules = recorded.get("rule_version", manifest["contract"])
        identity = media_identity(
            unit,
            selected["arrays"],
            selected["rows"],
            rules,
        )
        require(
            identity == pick(recorded, "input_key", "content_sha256"),
            "Package content identity differs",
        )
    return {
        "key": destination.name,
        "directory": destination,
        "manifest": manifest,
        "source": source,
        "datasets": datasets,
    }


def prepare_package(work=None, evidence_dir=None):
    work = settings.media_work_dir if work is None else work
    evidence_dir = settings.media_evidence_dir if evidence_dir is None else evidence_dir
    require(
        stage(22, work)["status"] == "passed" and stage(27, work)["status"] == "passed",
        "Incomplete media preparation",
    )
    payloads = {}
    steps = {n: stage(n, work) for n in range(20, 28)}
    # 封存后的引用指向包内证据，不依赖准备时的临时目录。 / Sealed references point to evidence inside the package without relying on temporary preparation directories.
    for number, field in ((21, "spectra"), (22, "arrays")):
        for array in steps[number][field]:
            for proof in array.get("references", []):
                original = inside(settings.database_dir, settings.database_dir / proof["path"])
                relative = original.relative_to(evidence_dir).as_posix()
                proof["path"] = "evidence/" + relative
    for number, value in steps.items():
        payloads[f"steps/{number}.json"] = encoded(pack_document(value))
    files = {
        f"evidence/{p.relative_to(evidence_dir).as_posix()}": p
        for p in evidence_dir.rglob("*")
        if p.is_file()
    }
    for path in work.glob("*_review_*.png"):
        files["review/" + path.name] = path
    for row in steps[27]["rows"]:
        files[row["crop_work_path"]] = inside(work, work / row["crop_work_path"])
    inventory = {
        name: {"sha256": hashlib.sha256(data).hexdigest(), "byte_size": len(data)}
        for name, data in payloads.items()
    }
    inventory.update(
        {
            name: {"sha256": sha(path), "byte_size": path.stat().st_size}
            for name, path in sorted(files.items())
        }
    )
    datasets = {}
    for unit in steps[20]["datasets"]:
        dataset = unit["dataset_id"]
        arrays = [a for a in steps[22]["arrays"] if a["dataset_id"] == dataset]
        rows = [r for r in steps[27]["rows"] if r["dataset_id"] == dataset]
        datasets[dataset] = {
            **media_identity(unit, arrays, rows),
            "rule_version": media_rule_version(unit),
        }
    manifest = {
        "contract": MEDIA_RULE_VERSION,
        "datasets": datasets,
        "source_release_id": steps[20]["source_release_id"],
        "files": inventory,
    }
    key = digest(manifest)
    destination = settings.database_dir / "media_releases" / key
    for relative, data in payloads.items():
        target = inside(destination, destination / relative)
        if target.exists():
            require(sha(target) == inventory[relative]["sha256"], "Packaged evidence was changed")
        else:
            write(target, data.decode("utf-8"))
    for relative, source in files.items():
        target = inside(destination, destination / relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            require(sha(target) == inventory[relative]["sha256"], "Packaged artifact was changed")
        else:
            shutil.copyfile(source, target)
    write(destination / "manifest.json", manifest)
    package = check_package(destination, manifest)
    report(
        "media_package_run.json",
        {
            "package": key,
            "code_sha256": media_code_hash(),
            "datasets": datasets,
            "status": "passed",
        },
    )
    return package


def insert_media_records(conn, package, dataset_id, rev, ids):
    """Write media records into the specified version; the caller prepares base records in the same transaction.
    向指定版本写媒体记录；基础记录由调用方在同一事务内准备。
    """
    key, dest, manifest = package["key"], package["directory"], package["manifest"]
    selected = package["datasets"][dataset_id]
    u, spectra, rows = selected["unit"], selected["arrays"], selected["rows"]
    axis = ids[u["source_axis_id"]]
    # 证据文件可共用，资产记录仍归属于各自的数据版本。 / Evidence files may be shared, but asset records still belong to their respective data versions.
    evidence = {}
    for relative, meta in manifest["files"].items():
        if relative.startswith("crops/"):
            continue
        evidence[relative] = add_asset(
            conn,
            rev,
            "evidence",
            "media/" + relative,
            "media_evidence",
            meta["byte_size"],
            meta["sha256"],
            path_reference(dest / relative),
            "processing_evidence",
            {"media_package_sha256": key},
        )
    for a in spectra:
        asset = conn.execute(
            "SELECT id FROM core.asset WHERE dataset_revision_id=%s AND representation='raw' AND logical_path=%s",
            (rev, a["file"]),
        ).fetchone()["id"]
        array = uid("spectral-array", rev, a["log_id"])
        log = ids[a["source_log_pk"]]
        insert(
            conn,
            "spectral_array",
            {
                "id": array,
                "dataset_revision_id": rev,
                "log_id": log,
                "asset_id": asset,
                "spectral_stream_id": ids[a["spectral_stream_id"]],
                "sample_count": a["sample_count"],
                "channel_count": a["band_count"],
                **pick(
                    a,
                    "dtype_code",
                    "matrix_order",
                    "data_offset_bytes",
                    "sample_stride_bytes",
                    "channel_stride_bytes",
                    "layout_status",
                    "value_unit",
                    "scaling_applied",
                ),
                "evidence_asset_id": evidence["steps/21.json"],
                "evidence": {
                    "binding_basis": a["binding_basis"],
                    "references": a["references"],
                    "official_commit": a["contract_commit"],
                    "verification_scope": a["verification_scope"],
                },
            },
        )
        sm = a["sample_map"]
        insert(
            conn,
            "spectral_sample_map",
            {
                "id": uid("spectral-map", array, 0),
                "dataset_revision_id": rev,
                "spectral_array_id": array,
                "axis_id": axis,
                **pick(sm, "source_row_from", "source_row_to", "sample_no_from", "sample_no_to"),
                "mapping_kind": "identity_range",
                "binding_status": "verified",
                "evidence_asset_id": evidence["steps/21.json"],
                "basis": a["binding_basis"],
            },
        )
        for block in a["blocks"]:
            insert(conn, "spectral_block", {"spectral_array_id": array, **block})
    for r in rows:
        region = uid(
            "image-region", rev, r["source_log_id"], r["source_frame_id"], r["region_ordinal"]
        )
        log = conn.execute(
                "SELECT id FROM core.scan_log WHERE dataset_revision_id=%s AND source_log_id=%s AND log_kind=%s",
                (rev, r["source_log_id"], "image"),
            ).fetchone()["id"]
        insert(
            conn,
            "image_region",
            {
                "id": region,
                "dataset_revision_id": rev,
                "image_frame_id": ids[r["source_frame_id"]],
                "region_kind": "core_row",
                **pick(
                    r,
                    "region_ordinal",
                    "x_px",
                    "y_px",
                    "width_px",
                    "height_px",
                    "coordinate_basis",
                    "region_status",
                    "detection_method",
                    "review_basis",
                ),
                "valid_area": None,
                "evidence_asset_id": evidence["steps/25.json"],
            },
        )
        insert(
            conn,
            "image_sample_mapping",
            {
                "id": uid("image-map", region, 0),
                "dataset_revision_id": rev,
                "image_region_id": region,
                "axis_id": axis,
                "section_interval_id": ids[r["source_section_id"]],
                **pick(
                    r,
                    "sample_no_from",
                    "sample_no_to",
                    "anchor_points",
                    "source_depth_direction",
                    "direction_status",
                    "direction_basis",
                    "mapping_level",
                    "mapping_status",
                    "mapping_method",
                    "indicator_method",
                    "indicator_version",
                    "indicator_status",
                    "indicator_parameters",
                    "error_px",
                    "error_depth_m",
                ),
                "direction_evidence_asset_id": evidence["steps/26.json"],
                "evidence_asset_id": evidence["steps/26.json"],
            },
        )
        rel = r["crop_work_path"]
        meta = manifest["files"][rel]
        asset = add_asset(
            conn,
            rev,
            "canonical",
            rel,
            "core_row_image",
            meta["byte_size"],
            meta["sha256"],
            path_reference(dest / rel),
            "lossless_source_crop",
            {
                "region_id": region,
                "source_frame_id": ids[r["source_frame_id"]],
                "recipe": r["recipe"],
                "pixel_sha256": r["pixel_sha256"],
            },
        )
        insert(
            conn,
            "log_asset",
            {
                "log_id": log,
                "asset_id": asset,
                "dataset_revision_id": rev,
                "role": "derived_core_row",
            },
        )
        insert(
            conn,
            "image_region_asset",
            {
                "region_id": region,
                "dataset_revision_id": rev,
                "asset_id": asset,
                "log_id": log,
                "representation_kind": "native_lossless_png",
                "width_px": r["width_px"],
                "height_px": r["height_px"],
                "transform": r["recipe"],
                "recipe_version": 1,
            },
        )
    return {
        "dataset_id": u["dataset_id"],
        "dataset_revision_id": rev,
        "axis_id": axis,
        "action": "inserted",
    }

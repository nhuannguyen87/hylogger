"""Prepare final-version inputs, map related IDs, and import individual datasets.
最终版本的输入准备、关联 ID 映射和单 dataset 导入。
"""

import copy

from _db_common import insert, uid
from _db_files import digest, read, sha
from _ingest import import_hole, prepared_content
from _media_common import require
from _media_import import (
    check_package, insert_media_records, remap, verify_media_sources,
    package_contexts, prepared_acceptance_content,
)
from _settings import settings, input_directory, path_reference

FINAL_RULE_VERSION = "single-final-v1"


def load_final_inputs(batch):
    """Read explicitly listed sealed packages; locate older preparation manifests through the specified work directory.
    读取明确列出的封存包；旧包通过指定工作目录找到准备清单。
    """
    result = []
    seen = set()
    require(batch.get("packages"), "The batch has no input packages")
    for item in batch["packages"]:
        destination = input_directory(item["directory"])
        package = check_package(destination, read(destination / "manifest.json"))
        work = input_directory(item["prepared_work_dir"])
        contexts = package_contexts(package, work)
        content = prepared_acceptance_content(package, contexts)
        acceptance_path = settings.reports_dir / f"local_acceptance_1_{package['key']}.json"
        if not acceptance_path.exists():
            acceptance_path = destination.parent.parent / "reports" / acceptance_path.name
        require(acceptance_path.is_file(), "First local acceptance is missing; run step 28 --package-only first")
        acceptance = read(acceptance_path)
        require(acceptance.get("stage") == 1 and acceptance.get("status") == "passed"
                and acceptance.get("content") == content
                and acceptance.get("content_sha256") == digest(content),
                "First local acceptance does not match the current inputs; rerun step 28 --package-only")
        for dataset_id, selected in package["datasets"].items():
            require(dataset_id not in seen, "The batch contains a duplicate dataset")
            seen.add(dataset_id)
            unit = selected["unit"]
            context = contexts[dataset_id]
            rules = batch.get("rule_versions", {}).get(unit["source_dataset_id"], FINAL_RULE_VERSION)
            require(isinstance(rules, str) and bool(rules.strip()), "The processing rule version must not be empty")
            item = make_final_context(context, package, dataset_id, rules)
            item["prepared_acceptance"] = {
                "package_sha256": package["key"],
                "content_sha256": acceptance["content_sha256"],
                "report_path": path_reference(acceptance_path),
            }
            result.append(item)
    unknown = set(batch.get("rule_versions", {})) - {x["unit"]["source_dataset_id"] for x in result}
    require(not unknown, "Rule version overrides include datasets outside this batch")
    return result


def make_final_context(source, package, dataset_id, rules=FINAL_RULE_VERSION):
    """Replace only internal relation IDs; preserve source documents and scientific values in Parquet files.
    只更换内部关联 ID；来源文档和 Parquet 科学数值保持原样。
    """
    selected = package["datasets"][dataset_id]
    unit = selected["unit"]
    media = package["manifest"]["datasets"][dataset_id]
    original = source["metadata"]["dataset_revision"]
    identity = {
        "dataset_id": dataset_id,
        "source_snapshot_id": original["source_snapshot_id"],
        "preparation_rules": original["pipeline_id"],
        "media_input_key": media["input_key"],
        "final_rules": rules,
        "raw_files": sorted(
            [a["source_relative_path"], a["byte_size"], a["sha256"]]
            for a in source["storage"]["raw_assets"]
        ),
    }
    key = digest(identity)
    revision = uid("final-revision", dataset_id, key)
    content = digest({
        "prepared": prepared_content(source),
        "bindings": sha(source["folder"] / "4_axis_bindings.json"),
        "media": media["content_sha256"],
    })
    documents = {
        name: read(source["folder"] / name)
        for name in ("4_core_intervals.json", "4_axis_bindings.json", "4_image_frames.json")
    }
    ids = {original["id"]: revision}
    ids[unit["source_axis_id"]] = uid("final-axis", revision)
    for kind, records in (
        ("log", source["metadata"]["logs"]),
        ("stream", source["metadata"]["spectral_streams"]),
        ("interpretation", source["metadata"]["interpretation_sets"]),
        ("interval", documents["4_core_intervals.json"]),
        ("frame", documents["4_image_frames.json"]),
    ):
        for record in records:
            ids[record["id"]] = uid("final-record", revision, kind, record["id"])
    context = copy.deepcopy(source)
    metadata = context["metadata"]
    for name in metadata:
        # NVCL 原始 JSON 是来源证据，不能把其中的标识改成内部标识。 / Original NVCL JSON is source evidence; do not replace its identifiers with internal IDs.
        if name not in ("raw_metadata", "borehole", "dataset"):
            metadata[name] = remap(metadata[name], ids)
    metadata["dataset_revision"].update(
        pipeline_id=key, final_identity=identity, content_sha256=content,
        prepared_revision_id=original["id"],
    )
    context["alignment"] = remap(context["alignment"], ids)
    context["summary"] = remap(context["summary"], ids)
    context["summary"].setdefault("source_file_count", len(context["storage"]["raw_assets"]))
    context["summary"].setdefault("canonical_file_count", len(context["storage"]["assets"]))
    context["documents"] = remap(documents, ids)
    context["verified_spectral_paths"] = [a["file"] for a in selected["arrays"]]
    bindings = context["documents"]["4_axis_bindings.json"]
    for array in selected["arrays"]:
        log = next(l for l in metadata["logs"] if l["id"] == ids[array["source_log_pk"]])
        log.update(
            array_layout_status="verified_sample_major", per_sample_publication_allowed=True,
            axis_binding_status="verified", binding_basis=array["binding_basis"],
        )
        bindings[:] = [b for b in bindings if b["source_log_id"] != array["log_id"]]
        bindings.append({
            "source_log_id": array["log_id"], "axis_id": ids[unit["source_axis_id"]],
            "status": "verified", "basis": array["binding_basis"],
        })
    return {
        "context": context, "package": package, "unit": unit, "ids": ids,
        "dataset_id": dataset_id, "dataset_revision_id": revision,
        "axis_id": ids[unit["source_axis_id"]], "input_digest": content,
    }


def import_final_dataset(conn, item, fail_after_samples=False):
    """Use the transaction held by the caller to write one complete version without cloning a base version.
    调用方持有事务；只写一个完整版本，不调用基础版本复制逻辑。
    """
    selected = item["package"]["datasets"][item["dataset_id"]]
    result = import_hole(
        conn, item["context"], fail_after_samples=fail_after_samples,
        content_digest=item["input_digest"],
    )
    ids = item["ids"]
    verify_media_sources(
        conn, remap(item["unit"], ids), remap(selected["arrays"], ids), remap(selected["rows"], ids),
    )
    if result["action"] == "inserted":
        insert_media_records(conn, item["package"], item["dataset_id"], item["dataset_revision_id"], ids)
        context = item["context"]
        insert(conn, "dataset_segment", {
            "id": uid("full-source-segment", item["dataset_revision_id"]),
            "dataset_revision_id": item["dataset_revision_id"],
            "borehole_id": context["metadata"]["borehole"]["id"],
            "branch_id": None, "axis_id": item["axis_id"],
            "sample_no_from": 0, "sample_no_to": item["unit"]["sample_count"] - 1,
            "sequence_no": None,
            "evidence": {"scope": "complete independent dataset; no 3D clipping",
                         "source_dataset_id": item["unit"]["source_dataset_id"]},
        })
    return {**result, "dataset_id": item["dataset_id"], "axis_id": item["axis_id"]}


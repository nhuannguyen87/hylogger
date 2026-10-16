"""Store shared media metadata once and expand it into the dictionaries used by each processing step.
媒体清单只保存一份公共信息；读取时还原为各步骤使用的字典。
"""

from collections import defaultdict


COLLECTIONS = ("datasets", "spectra", "arrays", "frames", "rows")
DATASET_FIELDS = (
    "hole_id", "source_dataset_id", "source_dataset_name", "source_revision_id",
    "source_axis_id", "source_snapshot_id", "sample_count", "prepared_directory",
    "prepared_hashes", "prepared_summary", "prepared_object_key",
)
FRAME_FIELDS = (
    "id", "source_path", "source_sha256", "source_log_id", "log_id",
    "core_interval_id", "image_ordinal", "row_count", "width_px", "height_px",
    "template_key", "coordinate_basis", "detection_method", "review_basis",
)
HEADER = "media_manifest"


def entries(document):
    for name in COLLECTIONS:
        for item in document.get(name, []):
            yield name, item


def frame_id(name, item):
    return item.get("source_frame_id") or (item.get("id") if name == "frames" else None)


def shared_fields(items, fields):
    # 只提取每项都有且值完全相同的字段，行高等差异仍留在原位。 / Extract only fields present and identical in every item; keep differences such as row height on each item.
    first = items[0]
    return {
        key: first[key] for key in fields
        if key in first and all(key in item and item[key] == first[key] for item in items)
    }


def pack_document(document):
    if HEADER in document:
        raise ValueError("Media document is already packed")
    result = dict(document)
    for name in COLLECTIONS:
        if name in result:
            result[name] = [dict(item) for item in result[name]]
    datasets = defaultdict(list)
    frames = defaultdict(list)
    owners = {}
    for name, item in entries(result):
        dataset = item["dataset_id"]
        datasets[dataset].append(item)
        fid = frame_id(name, item)
        if fid is not None:
            if owners.setdefault(fid, dataset) != dataset:
                raise ValueError("Frame belongs to different datasets")
            frames[fid].append(item)
    contexts = {}
    for dataset, items in datasets.items():
        common = shared_fields(items, DATASET_FIELDS)
        contexts[dataset] = common
        for item in items:
            for key in common:
                del item[key]
    frame_contexts = {}
    for fid, items in frames.items():
        # frames 列表保留 id 作为引用；rows 列表保留 source_frame_id。 / Keep id as the reference in frames and source_frame_id as the reference in rows.
        common = shared_fields(items, tuple(k for k in FRAME_FIELDS if k != "id"))
        frame_contexts[fid] = {"dataset_id": owners[fid], "fields": common}
        for item in items:
            for key in common:
                del item[key]
    result[HEADER] = {"version": 2, "datasets": contexts, "frames": frame_contexts}
    return result


def unpack_document(document):
    if HEADER not in document:
        return document  # 旧封存包保持原样，不改写文件或旧指纹。 / Preserve older sealed packages without rewriting files or their fingerprints.
    header = document[HEADER]
    if header.get("version") != 2:
        raise ValueError("Unsupported media manifest version")
    result = {key: value for key, value in document.items() if key != HEADER}
    for name in COLLECTIONS:
        if name not in result:
            continue
        restored = []
        for item in result[name]:
            dataset = item["dataset_id"]
            if dataset not in header["datasets"]:
                raise ValueError("Missing dataset context")
            common = header["datasets"][dataset]
            if set(common) - set(DATASET_FIELDS):
                raise ValueError("Unexpected dataset context field")
            fields = dict(common)
            fid = frame_id(name, item)
            if fid is not None:
                frame = header["frames"].get(fid)
                if frame is None or frame["dataset_id"] != dataset:
                    raise ValueError("Missing or cross-dataset frame context")
                if set(frame["fields"]) - (set(FRAME_FIELDS) - {"id"}):
                    raise ValueError("Unexpected frame context field")
                fields.update(frame["fields"])
            if fields.keys() & item.keys():
                raise ValueError("Conflicting media context fields")
            restored.append({**fields, **item})
        result[name] = restored
    return result

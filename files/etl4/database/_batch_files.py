"""Shared batch file format for steps 38 and 39; verify delivery files without recomputing business data.
38、39 共用的批次文件格式；这里只核对交付文件，不重算业务数据。
"""

import json
import os
from pathlib import Path
import re
import tempfile
import time

from _db_files import digest, encoded, sha
from _settings import safe_child

FORMAT = "etl4-batch-v1"

# Order data tables for restoration; rebuild release membership later during the cloud merge. / 数据表按恢复顺序排列；发布归属由以后云端合并时重新建立。
DATA_TABLES = (
    "borehole", "borehole_revision", "dataset", "dataset_revision", "sample_axis",
    "core_interval", "scan_sample", "spectral_stream", "interpretation_set",
    "interpretation_input", "metric_definition", "scan_log", "log_axis_binding",
    "asset", "log_asset", "data_chunk", "image_frame", "source_document",
    "source_reference", "quality_issue", "borehole_branch", "dataset_segment",
    "survey_station", "spectral_array", "spectral_sample_map", "spectral_block",
    "image_region", "image_sample_mapping", "image_region_asset",
)
EVIDENCE_TABLES = ("revision_validation", "asset_location")
RELEASE_TABLES = ("data_release", "release_dataset", "active_release",
                  "borehole_composite", "borehole_composite_part", "dataset_succession")
ADMIN_TABLES = ("schema_migration", "ingest_run")
KNOWN_TABLES = set(DATA_TABLES + EVIDENCE_TABLES + RELEASE_TABLES + ADMIN_TABLES)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def checksum(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def object_key(sha256):
    require(checksum(sha256), 'The asset is missing a valid SHA256')
    return f"objects/sha256/{sha256[:2]}/{sha256}"


def write_json(path, value):
    """Numbered entry points provide explicit paths; replace temporary files atomically to avoid partial receipts.
    路径由编号入口明确给出；临时文件替换避免留下半份回执。
    """
    path = Path(path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(encoded(value))
        # Windows may briefly deny replacement while a file is in use; keep atomic replacement instead of directly overwriting the old file. / Windows 偶尔会因短暂文件占用拒绝替换；始终保留原子替换，不直接覆盖旧文件。
        for attempt in range(4):
            try:
                os.replace(temporary, path)
                break
            except PermissionError:
                if attempt == 3:
                    raise
                time.sleep((0.05, 0.15, 0.3)[attempt])
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def file_info(path, kind):
    return {"sha256": sha(path), "byte_size": Path(path).stat().st_size, "kind": kind}


def read_json(path):
    # Reject duplicate keys so separate stages cannot interpret the same manifest differently. / 拒绝重复键，避免两个阶段对同一清单产生不同理解。
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, f"The batch JSON contains a duplicate key: {key}")
            result[key] = value
        return result

    return json.loads(Path(path).read_text(encoding="utf-8-sig"), object_pairs_hook=pairs)


def check_file_info(value):
    require(isinstance(value, dict) and checksum(value.get("sha256")), 'Invalid delivery file digest')
    require(type(value.get("byte_size")) is int and value["byte_size"] >= 0, 'Invalid delivery file size')


def local_file(directory, relative):
    require(isinstance(relative, str) and "\\" not in relative and ":" not in relative,
            'Delivery files must use relative paths with forward slashes')
    return safe_child(directory, relative)


def load_export(directory):
    """Validate received export files without reading the original images or spectra referenced by the manifest.
    接收导出文件的边界检查，不读取清单引用的原始图片或光谱。
    """
    directory = Path(directory).resolve()
    path = directory / "manifest.json"
    manifest = read_json(path)
    require(manifest.get("format") == FORMAT, 'Unsupported batch format')
    require(isinstance(manifest.get("identity"), dict)
            and manifest.get("batch_id") == digest(manifest["identity"]), 'The batch ID does not match its identity')
    identity = manifest["identity"]
    require(identity.get("format") == FORMAT, 'The batch identity format does not match')
    files = manifest.get("files")
    require(isinstance(files, dict) and files, 'The batch contains no structured files')
    for relative, value in files.items():
        require(relative.startswith(("tables/", "evidence/", "schema/")) or relative == "composites.json",
                'The manifest contains non-delivery files or private run files')
        source = local_file(directory, relative)
        check_file_info(value)
        require(source.is_file() and source.stat().st_size == value["byte_size"]
                and sha(source) == value["sha256"], f"The delivery file is missing or its content changed: {relative}")
    schema = manifest.get("schema")
    require(isinstance(schema, dict) and isinstance(schema.get("tables"), dict)
            and set(schema["tables"]) == KNOWN_TABLES, 'The batch database schema has an incomplete table scope')
    migrations = schema.get("migrations")
    require(isinstance(migrations, list), 'The batch is missing its migration manifest')
    migration_hashes = {}
    for migration in migrations:
        name = migration.get("name")
        require(isinstance(name, str) and name not in migration_hashes
                and checksum(migration.get("sha256")), 'A migration name is duplicated or its digest is invalid')
        migration_hashes[name] = migration["sha256"]
        relative = "schema/" + name
        require(relative in files and files[relative]["sha256"] == migration["sha256"],
                'The migration file digest does not match the schema manifest')
    require(migration_hashes == identity.get("schema_migrations"), 'The migration manifest does not match the batch identity')
    require(isinstance(manifest.get("tables"), dict)
            and set(manifest["tables"]) == set(DATA_TABLES), 'The batch has an incomplete data table scope')
    for name, table in manifest["tables"].items():
        require(re.fullmatch(r"[a-z][a-z0-9_]*", name) is not None, 'Invalid data table name')
        relative = table.get("path")
        require(relative == f"tables/{name}.jsonl.gz" and relative in files, 'The data table file is not in the delivery manifest')
        require(all(table.get(key) == files[relative][key] for key in ("sha256", "byte_size")),
                'The data table digest does not match the file manifest')
    require({path for path in files if path.startswith("tables/")}
            == {table["path"] for table in manifest["tables"].values()}, 'The delivery files contain unlisted data tables')
    datasets = manifest.get("datasets")
    require(isinstance(datasets, list) and datasets, 'The batch contains no datasets')
    revisions = {item["dataset_revision_id"] for item in datasets}
    require(len(revisions) == len(datasets)
            and len({item["dataset_id"] for item in datasets}) == len(datasets), 'The batch contains duplicate datasets')
    dataset_identities = []
    for item in datasets:
        require(checksum(item.get("input_digest")), 'Invalid dataset input identity')
        dataset_identities.append({key: item[key] for key in ("dataset_id", "dataset_revision_id", "input_digest")})
    require(sorted(dataset_identities, key=lambda item: item["dataset_id"])
            == identity.get("datasets"), 'The dataset manifest does not match the batch identity')
    ids, objects = set(), {}
    assets = manifest.get("assets")
    require(isinstance(assets, list), 'The batch is missing its asset manifest')
    for asset in assets:
        check_file_info(asset)
        require(asset.get("asset_id") and asset["asset_id"] not in ids, 'An asset ID is missing or duplicated')
        require(asset.get("dataset_revision_id") in revisions, 'The asset belongs to a revision outside this batch')
        require(asset.get("object_key") == object_key(asset["sha256"]), 'The asset object key does not match its content identity')
        ids.add(asset["asset_id"])
        content = (asset["sha256"], asset["byte_size"])
        require(objects.setdefault(asset["object_key"], content) == content, 'The same object key has conflicting content')
    return manifest, sha(path)


def load_sources(directory, manifest, manifest_sha256):
    """Store local paths separately; the cloud does not need this mapping.
    本机路径单独保存，云端不需要这份映射。
    """
    source = read_json(Path(directory) / "local_sources.json")
    require(source.get("batch_id") == manifest["batch_id"]
            and source.get("manifest_sha256") == manifest_sha256, 'The local path mapping belongs to another export')
    expected = {item["object_key"]: (item["sha256"], item["byte_size"]) for item in manifest["assets"]}
    objects = source.get("objects")
    require(isinstance(objects, dict) and set(objects) == set(expected), 'The local path mapping does not match the asset scope')
    for key, value in objects.items():
        require((value.get("sha256"), value.get("byte_size")) == expected[key], 'The local asset mapping has a different content identity')
        require(isinstance(value.get("path"), str) and Path(value["path"]).is_absolute(), 'Upload sources must explicitly use absolute local paths')
    return objects

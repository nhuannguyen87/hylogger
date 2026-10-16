"""Preview local cleanup for an uploaded and merged batch; delete individual files only with --apply.
预览已上传并合并批次的本机清理范围；只有 --apply 才逐个删除文件。
"""

import base64
import gzip
import json
import os
from pathlib import Path
import stat
import sys
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _batch_files import checksum, load_export, load_sources, read_json, require, write_json
from _db_files import digest, sha
from _settings import SOURCE_ROOT, StepParser, configure


def absolute_path(value):
    path = Path(value)
    require(path.is_absolute() and ".." not in path.parts, 'Cleanup paths must be absolute and contain no .. segments')
    return Path(os.path.abspath(path))


def inspect_path(path):
    """Do not resolve links to other targets; directory junctions must not provide a cleanup route.
    不把链接解析成别的目标；目录中的 junction 也不能作为清理通道。
    """
    path = absolute_path(path)
    current = Path(path.anchor)
    result = current.lstat()
    for part in path.parts[1:]:
        current = current / part
        result = current.lstat()
        reparse = getattr(result, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        require(not stat.S_ISLNK(result.st_mode) and not reparse, 'The cleanup path contains a link or junction: ' + str(current))
    return result


def root_path(value, label):
    path = absolute_path(value)
    require(path != path.parent and not SOURCE_ROOT.is_relative_to(path), label + 'must not be a drive root or an ancestor of the source code directory')
    info = inspect_path(path)
    require(stat.S_ISDIR(info.st_mode), label + 'must be an existing directory')
    return path


def checked_json(path):
    info = inspect_path(path)
    require(stat.S_ISREG(info.st_mode), 'The manifest or receipt is not a regular file')
    return read_json(path)


def expected_upload_objects(directory, manifest, manifest_sha256, prefix):
    objects = {}
    for asset in manifest["assets"]:
        key = "/".join(filter(None, (prefix, asset["object_key"])))
        objects[key] = (asset["sha256"], asset["byte_size"])
    base = "/".join(filter(None, (prefix, "batches", manifest["batch_id"], manifest_sha256)))
    for name, value in manifest["files"].items():
        objects[base + "/" + name] = (value["sha256"], value["byte_size"])
    objects[base + "/manifest.json"] = (manifest_sha256, (directory / "manifest.json").stat().st_size)
    return objects


def check_uploaded(directory, manifest, manifest_sha256, receipt):
    require(receipt.get("format") == "etl4-batch-upload-v1" and receipt.get("status") == "complete",
            'This batch does not have a complete upload receipt')
    require(receipt.get("batch_id") == manifest["batch_id"] and receipt.get("manifest_sha256") == manifest_sha256,
            'The upload receipt does not match this batch manifest')
    require(all(isinstance(receipt.get(name), str) and receipt[name] for name in ("bucket", "region")),
            'The upload receipt is missing its target location')
    require(isinstance(receipt.get("prefix"), str), 'The upload receipt is missing its object prefix')
    expected = expected_upload_objects(directory, manifest, manifest_sha256, receipt["prefix"])
    rows = receipt.get("objects")
    require(isinstance(rows, list) and len(rows) == len(expected)
            and receipt.get("expected_objects") == len(expected)
            and receipt.get("completed_objects") == len(expected), 'The upload receipt does not cover all objects in this batch')
    seen = set()
    for row in rows:
        key = row.get("object_key")
        require(key in expected and key not in seen, 'Upload receipt objects are duplicated or outside this batch')
        require((row.get("sha256"), row.get("byte_size")) == expected[key]
                and row.get("checksum_type") == "FULL_OBJECT", 'An upload receipt object has a different identity or checksum type')
        try:
            native = base64.b64decode(row.get("checksum", ""), validate=True)
        except (ValueError, TypeError):
            native = b""
        algorithm = row.get("checksum_algorithm")
        require((algorithm == "CRC32" and len(native) == 4)
                or (algorithm == "SHA256" and native.hex() == row["sha256"]), 'The upload receipt is missing a valid server checksum')
        seen.add(key)


def check_merged(manifest, manifest_sha256, upload, upload_path, merge):
    require(merge.get("format") == "etl4-batch-merge-v1" and merge.get("status") == "complete",
            'This batch does not have a successful cloud merge receipt')
    require(merge.get("batch_id") == manifest["batch_id"] and merge.get("manifest_sha256") == manifest_sha256
            and merge.get("upload_receipt_sha256") == sha(upload_path), 'The merge receipt does not reference this upload result')
    require(all(merge.get(key) == upload[key] for key in ("bucket", "region", "prefix")),
            'The upload and merge receipts have different target locations')
    require(merge.get("datasets") == manifest["identity"]["datasets"] and merge.get("release_id"),
            'The cloud-committed dataset scope is incomplete')
    target = merge.get("cloud_target")
    require(isinstance(target, dict) and target.get("host") and target.get("dbname")
            and type(target.get("port")) is int and 0 < target["port"] < 65536, 'The merge receipt is missing its cloud database target')
    values = {key: target[key] for key in ("host", "port", "dbname")}
    require(target.get("fingerprint") == digest(values), 'The cloud database target fingerprint does not match')


def cleanup_list(directory, manifest, manifest_sha256):
    path = directory / "cleanup_files.json"
    if not os.path.lexists(path):
        return []
    value = checked_json(path)
    require(value.get("format") == "etl4-batch-cleanup-files-v1"
            and value.get("batch_id") == manifest["batch_id"]
            and value.get("manifest_sha256") == manifest_sha256, 'The temporary-file cleanup manifest does not belong to this batch')
    rows = value.get("files")
    require(isinstance(rows, list), 'The temporary-file cleanup manifest is missing files')
    seen = set()
    for row in rows:
        path = absolute_path(row["path"])
        require(path not in seen and row.get("kind") == "temporary" and checksum(row.get("sha256"))
                and type(row.get("byte_size")) is int and row["byte_size"] >= 0, 'The temporary-file cleanup manifest has invalid content')
        seen.add(path)
    return rows


def asset_roles(directory, manifest):
    """Read source kinds from the sealed asset table; a file inside work is not necessarily a temporary output.
    来源类型取自已封存的资产表，不能仅凭文件位于 work 就当成临时产物。
    """
    expected = {asset["asset_id"]: asset for asset in manifest["assets"]}
    seen, roles = set(), {}
    with gzip.open(directory / manifest["tables"]["asset"]["path"], "rt", encoding="utf-8") as stream:
        for line in stream:
            asset = json.loads(line)
            asset_id = asset.get("id")
            require(asset_id in expected and asset_id not in seen, 'The asset table and public asset manifest cover different scopes')
            selected = expected[asset_id]
            require(all(asset.get(key) == selected[key] for key in ("dataset_revision_id", "sha256", "byte_size")),
                    'The asset table content identity does not match the public asset manifest')
            roles.setdefault(selected["object_key"], set()).add(asset.get("representation", "unknown"))
            seen.add(asset_id)
    require(seen == set(expected), 'The asset table is missing a source kind for a file in this batch')
    return roles


def registered_directories(exports_root):
    directories = []
    for current, folders, files in os.walk(exports_root, followlinks=False):
        current = Path(current)
        inspect_path(current)
        # The registry must not hide other batches under directory links. / 注册区不允许隐藏在目录链接下的其他批次。
        for name in folders:
            inspect_path(current / name)
        if "manifest.json" in files or "local_sources.json" in files:
            directories.append(current)
    return sorted(directories)


def other_batch_complete(directory, manifest, manifest_sha256):
    upload_path, merge_path = directory / "upload_receipt.json", directory / "merge_receipt.json"
    upload = checked_json(upload_path) if os.path.lexists(upload_path) else None
    merge = checked_json(merge_path) if os.path.lexists(merge_path) else None
    for receipt, expected_format in ((upload, "etl4-batch-upload-v1"), (merge, "etl4-batch-merge-v1")):
        if receipt is not None:
            require(receipt.get("format") == expected_format and receipt.get("batch_id") == manifest["batch_id"]
                    and receipt.get("manifest_sha256") == manifest_sha256
                    and receipt.get("status") in ("partial", "pending", "failed", "complete"),
                    'Another registered batch has a damaged receipt or unknown status')
    if not upload or not merge or upload["status"] != "complete" or merge["status"] != "complete":
        return False
    check_uploaded(directory, manifest, manifest_sha256, upload)
    check_merged(manifest, manifest_sha256, upload, upload_path, merge)
    return True


def scan_registry(exports_root, own_directory):
    references, controls, blockers = {}, {}, []
    try:
        directories = registered_directories(exports_root)
    except (OSError, ValueError) as exc:
        return {}, {}, ['The batch registry directory could not be read completely: ' + type(exc).__name__]
    require(own_directory in directories, "This batch's export directory is not inside the shared batch registry directory")
    for directory in directories:
        try:
            inspect_path(directory / "manifest.json")
            manifest, manifest_sha256 = load_export(directory)
            sources = load_sources(directory, manifest, manifest_sha256)
            rows = list(sources.values()) + cleanup_list(directory, manifest, manifest_sha256)
            for relative in list(manifest["files"]) + ["manifest.json", "local_sources.json", "upload_receipt.json",
                                                       "merge_receipt.json", "cleanup_files.json"]:
                path = directory / relative
                if os.path.lexists(path):
                    inspect_path(path)
                    controls[str(path)] = sha(path)
            if directory != own_directory and not other_batch_complete(directory, manifest, manifest_sha256):
                for row in rows:
                    path = absolute_path(row["path"])
                    references.setdefault(path, []).append(str(directory))
        except (OSError, ValueError, KeyError, TypeError) as exc:
            blockers.append(f"Registry data for this or another batch could not be confirmed: {directory.name} ({type(exc).__name__})")
    return references, controls, blockers


def file_identity(info):
    return {name: int(getattr(info, name)) for name in ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns", "st_nlink")}


def protected_file(path, protected):
    if path in protected or any(path.is_relative_to(SOURCE_ROOT / name) for name in ("database", "processing")):
        return True
    parts = {part.lower() for part in path.parts}
    if parts & {".git", ".codex", ".aws", ".venv", "pgdata", "runtime", "backups", "backup", "reports", "evidence"}:
        return True
    name = path.name.lower()
    if any(word in name for word in ("manifest", "receipt", "acceptance", "report")):
        return True
    if path.suffix.lower() in {".py", ".pyc", ".pyo", ".sql", ".md", ".toml", ".dump", ".backup"}:
        return True
    return any((folder / "PG_VERSION").exists() for folder in path.parents)


def inspect_candidate(row, work_root, raw_root, include_raw, protected, references):
    path = absolute_path(row["path"])
    if protected_file(path, protected):
        return None, 'Keep source code, database, or evidence files'
    if path in references:
        return None, 'Still referenced by other incomplete batches: ' + ", ".join(references[path])
    in_work = path != work_root and path.is_relative_to(work_root)
    in_raw = raw_root is not None and path != raw_root and path.is_relative_to(raw_root)
    if row.get("kind") in ("evidence", "unknown"):
        return None, 'Keep acceptance evidence or assets with unspecified source kinds'
    if row.get("kind") == "raw" and not (include_raw and in_raw):
        return None, 'Keep original assets by default; explicitly select their raw root directory to remove them'
    if row.get("kind") == "temporary":
        if not in_work:
            return None, 'The temporary file is outside the explicit work directory'
    elif not in_work and not (include_raw and in_raw):
        return None, 'Keep raw data by default or because its path is outside the explicit cleanup root'
    if not os.path.lexists(path):
        return None, 'The file no longer exists'
    info = inspect_path(path)
    require(stat.S_ISREG(info.st_mode), 'The cleanup target is not a regular file: ' + str(path))
    if info.st_nlink != 1 or info.st_ino == 0:
        return None, 'Keep the file because it has hard links or its identity cannot be confirmed'
    before = file_identity(info)
    require(info.st_size == row["byte_size"] and sha(path) == row["sha256"], 'The candidate file content differs from the manifest: ' + str(path))
    require(file_identity(inspect_path(path)) == before, 'The file changed during verification: ' + str(path))
    return {"path": str(path), "kind": row.get("kind", "asset"), "sha256": row["sha256"],
            "byte_size": row["byte_size"], "file_identity": before}, None


def delete_checked_file(row):
    """Recheck the path, content, and file identity before deletion; unlink only the current item without recursion.
    删除前再次检查路径、内容和文件身份；仅 unlink 当前这一项，不递归。
    """
    path = absolute_path(row["path"])
    require(file_identity(inspect_path(path)) == row["file_identity"], 'The file identity changed before deletion: ' + str(path))
    require(sha(path) == row["sha256"], 'The file content changed before deletion: ' + str(path))
    require(file_identity(inspect_path(path)) == row["file_identity"], 'The path or file changed before deletion: ' + str(path))
    path.unlink()


def collect_cleanup_files(directory, manifest, manifest_sha256, sources, temporary_list):
    """List this batch's files by asset purpose; temporary files require a separate explicit manifest.
    按资产用途列出本批文件，临时文件必须另有明确清单。
    """
    roles = asset_roles(directory, manifest)
    rows = []
    for key, value in sources.items():
        selected = roles[key]
        if "evidence" in selected:
            kind = "evidence"
        elif "raw" in selected:
            kind = "raw"
        elif selected == {"canonical"}:
            kind = "canonical"
        else:
            kind = "unknown"
        rows.append({**value, "kind": kind})
    if temporary_list is not None:
        require(absolute_path(temporary_list) == directory / "cleanup_files.json",
                "The temporary cleanup manifest must be cleanup_files.json inside this batch's registry directory")
        require(Path(temporary_list).is_file(), 'The specified temporary-file cleanup manifest does not exist')
        rows.extend(cleanup_list(directory, manifest, manifest_sha256))
    return rows


def inspect_cleanup_files(rows, work_root, raw_root, include_raw, protected, references):
    """Check each path once and return candidates, retained items, and blocking reasons separately.
    同一路径只检查一次，分别返回候选、保留项和阻断原因。
    """
    candidates, kept, blockers = [], [], []
    seen = {}
    for row in rows:
        path = absolute_path(row["path"])
        content = (row["sha256"], row["byte_size"])
        require(path not in seen or seen[path] == content, 'The same path has different content identities in the cleanup manifest')
        if path in seen:
            continue
        seen[path] = content
        try:
            candidate, reason = inspect_candidate(row, work_root, raw_root, include_raw, protected, references)
            if candidate:
                candidates.append(candidate)
            else:
                kept.append({"path": str(path), "reason": reason})
        except (OSError, ValueError) as error:
            reason = str(error) if isinstance(error, ValueError) else 'The file cannot be read: ' + str(path)
            blockers.append(reason)
    return candidates, kept, blockers


def cleanup_batch(directory, exports_root, work_root, *, raw_root=None, include_raw=False,
                  upload_receipt=None, merge_receipt=None, temporary_list=None, apply=False):
    """Verify delivery receipts and prepare a file preview; delete individual files only when apply is explicit.
    核对交付回执、生成文件预览；仅显式 apply 时进入逐文件删除。
    """
    directory = absolute_path(directory)
    exports_root, work_root = root_path(exports_root, 'Batch registry directory'), root_path(work_root, 'Work directory')
    require(directory != exports_root and directory.is_relative_to(exports_root), 'This batch must have its own directory inside the shared registry')
    inspect_path(directory / "manifest.json")
    require(not include_raw or raw_root is not None, 'Cleaning raw data requires both --include-raw and --raw-root')
    raw_root = root_path(raw_root, 'Raw directory') if raw_root is not None else None
    if raw_root is not None:
        require(not raw_root.is_relative_to(work_root) and not work_root.is_relative_to(raw_root), 'The work and raw directories must not overlap')
    manifest, manifest_sha256 = load_export(directory)
    inspect_path(directory / "local_sources.json")
    sources = load_sources(directory, manifest, manifest_sha256)
    upload_path = absolute_path(upload_receipt) if upload_receipt else directory / "upload_receipt.json"
    merge_path = absolute_path(merge_receipt) if merge_receipt else directory / "merge_receipt.json"
    upload, merge = checked_json(upload_path), checked_json(merge_path)
    check_uploaded(directory, manifest, manifest_sha256, upload)
    check_merged(manifest, manifest_sha256, upload, upload_path, merge)
    rows = collect_cleanup_files(directory, manifest, manifest_sha256, sources, temporary_list)
    references, controls, blockers = scan_registry(exports_root, directory)
    controls.update({str(upload_path): sha(upload_path), str(merge_path): sha(merge_path)})
    report_path = directory / "cleanup_report.json"
    if os.path.lexists(report_path):
        previous = checked_json(report_path)
        require(previous.get("format") == "etl4-batch-cleanup-v1"
                and previous.get("batch_id") == manifest["batch_id"]
                and previous.get("manifest_sha256") == manifest_sha256,
                'The cleanup report path already contains other content; it was not overwritten')
    protected = {Path(path) for path in controls} | {report_path, directory / "cleanup_files.json"}
    require(report_path not in {absolute_path(row["path"]) for row in rows}, 'The cleanup report path conflicts with an asset source')
    report = {"format": "etl4-batch-cleanup-v1", "status": "preview", "batch_id": manifest["batch_id"],
              "manifest_sha256": manifest_sha256, "upload_receipt_sha256": sha(upload_path),
              "merge_receipt_sha256": sha(merge_path), "cloud_target": merge["cloud_target"],
              "exports_root": str(exports_root), "work_root": str(work_root),
              "raw_root": str(raw_root) if raw_root else None, "include_raw": include_raw,
              "candidates": [], "kept": [], "blockers": blockers, "deleted": [],
              "checked_at": datetime.now(timezone.utc).isoformat()}
    report["candidates"], report["kept"], file_blockers = inspect_cleanup_files(
        rows, work_root, raw_root, include_raw, protected, references)
    report["blockers"].extend(file_blockers)
    report["candidate_bytes"] = sum(row["byte_size"] for row in report["candidates"])
    if report["blockers"]:
        report["status"] = "blocked"
    write_json(report_path, report)
    if not apply:
        return report
    require(not report["blockers"], 'Cleanup has unresolved items; no files were deleted. See cleanup_report.json')
    report["status"] = "partial"
    write_json(report_path, report)
    try:
        # Reread registry data between preview and execution to detect new batch references to changed paths. / 在预览内容与执行之间再次读取注册资料，避免新批次引用已改变的路径。
        new_references, new_controls, new_blockers = scan_registry(exports_root, directory)
        new_controls.update({str(upload_path): sha(upload_path), str(merge_path): sha(merge_path)})
        require(not new_blockers and new_controls == controls and new_references == references,
                'The batch registry changed before cleanup; deletion did not continue')
        for row in report["candidates"]:
            delete_checked_file(row)
            report["deleted"].append({"path": row["path"], "byte_size": row["byte_size"]})
            write_json(report_path, report)
    except BaseException as exc:
        report["error_type"] = type(exc).__name__
        write_json(report_path, report)
        raise
    report.update(status="complete", deleted_bytes=sum(row["byte_size"] for row in report["deleted"]),
                  completed_at=datetime.now(timezone.utc).isoformat())
    write_json(report_path, report)
    return report


def main():
    parser = StepParser(description=__doc__.splitlines()[0])
    parser.add_argument("--export-dir", type=Path, required=True)
    parser.add_argument("--exports-root", type=Path, required=True, help='Shared export directory registering all local batches')
    parser.add_argument("--work-root", type=Path, required=True, help='Allow cleanup only of listed work files inside this directory')
    parser.add_argument("--raw-root", type=Path, help='Explicit raw data root directory; retained by default')
    parser.add_argument("--include-raw", action="store_true", help='Also clean up listed raw files whose delivery is complete')
    parser.add_argument("--cleanup-list", type=Path, help='cleanup_files.json in this batch directory, listing temporary files eligible for cleanup')
    parser.add_argument("--upload-receipt", type=Path)
    parser.add_argument("--merge-receipt", type=Path)
    parser.add_argument("--apply", action="store_true", help='Perform verified per-file deletion; generate only a preview when omitted')
    args = parser.parse_args()
    configure(args)
    result = cleanup_batch(args.export_dir, args.exports_root, args.work_root, raw_root=args.raw_root,
                           include_raw=args.include_raw, upload_receipt=args.upload_receipt,
                           merge_receipt=args.merge_receipt, temporary_list=args.cleanup_list, apply=args.apply)
    print(f"Cleanup status: {result['status']}; candidates: {len(result['candidates'])} files, {result['candidate_bytes']} bytes; "
          f"deleted {len(result['deleted'])} files. See this batch's cleanup_report.json")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(str(exc) if isinstance(exc, ValueError) else f"Cleanup incomplete: {type(exc).__name__}", file=sys.stderr)
        raise SystemExit(1)

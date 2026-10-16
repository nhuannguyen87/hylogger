"""Provide file I/O, rule versions, and dataset context for processing steps.
处理步骤使用的文件读写、规则版本和 dataset 上下文。
"""

from __future__ import annotations
import csv
import hashlib
import importlib.metadata
import json
import os
import platform
import sys
import tempfile
import time
import uuid
from pathlib import Path

CODE = Path(__file__).resolve().parent
sys.path.insert(0, str(CODE.parent))
import copy
from _settings import SOURCE_ROOT, StepParser, raw_path
from _settings import settings, configure, validate_hole_names, add_hole_arguments, selected_holes
from dataclasses import dataclass


SOURCE = SOURCE_ROOT
BASELINE = CODE / "preparation_baseline.json"
# v3 将入库的指标显示名称改为英文，避免与旧中文内容共用版本。 / v3 uses English metric display names so they do not share a revision with older Chinese content.
PROCESSING_RULE_VERSION = "etl4-preparation-v3"
PROCESSING_RULE_VERSIONS = {}  # 仅某个来源 dataset 改规则时，在这里填写其版本。 / Set a dataset-specific version here only when its processing rules change.
SOURCE_HOLES = (
    "05KCD001",
    "07THD002",
    "07THD003",
    "09ATD015",
    "09ATD019",
    "12CADD001",
    "14KDD001",
    "15EIS001",
)

ALLOWED = ("05KCD001", "07THD002", "07THD003", "09ATD015", "09ATD019")
METADATA = (
    "borehole.json",
    "datasets.json",
    "logs_scalar.json",
    "scalar_manifest.json",
    "logs_spectral.json",
    "spectral_manifest.json",
    "logs_profilometer.json",
    "profilometer_manifest.json",
    "logs_image.json",
    "image_logs_by_dataset.json",
    "images_manifest.json",
    "tray_depths.json",
    "package_manifest.json",
)


def _pairs(pairs):
    result = {}
    for k, v in pairs:
        if k in result:
            raise ValueError(f"Duplicate JSON key: {k}")
        result[k] = v
    return result


def parse_json(text):
    def invalid(value):
        raise ValueError(f"Non-finite JSON token: {value}")

    return json.loads(text, object_pairs_hook=_pairs, parse_constant=invalid)


def read_json(path):
    return parse_json(Path(path).read_text(encoding="utf-8-sig"))


def json_bytes(value):
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n"
    ).encode("utf-8")


def digest(value):
    return hashlib.sha256(json_bytes(value)).hexdigest()


def sha_file(path):
    from _file_checks import hash_file

    return hash_file(path)


def inside(root, path):
    root, path = Path(root).resolve(), Path(path).resolve()
    if not path.is_relative_to(root) or path == root:
        raise ValueError(f"Path leaves the permitted directory: {path}")
    return path


def source_path(hole, relative):
    validate_hole_names([hole])
    root = raw_path(hole, "borehole.json").parent
    if not (root / "borehole.json").is_file() or not (root / "datasets.json").is_file():
        raise ValueError(f"Missing source package metadata: {hole}")
    relative_path = Path(relative)
    if relative_path.is_absolute() or ".." in relative_path.parts:
        raise ValueError(f"Source path must be relative without traversal: {relative}")
    return inside(root, root / relative_path)


def atomic_bytes(path, data):
    path = inside(settings.processing_dir, path)
    path.parent.mkdir(parents=True, exist_ok=True)
    sealed = any(
        (parent / "5_storage_report.json").is_file()
        for parent in path.parents
        if parent.is_relative_to(settings.processing_dir)
    )
    if path.is_file() and sealed:
        previous = path.read_bytes()
        if previous == data:
            return
        if path.suffix == ".json":
            before, after = parse_json(previous.decode("utf-8")), parse_json(data.decode("utf-8"))
            if isinstance(before, dict) and isinstance(after, dict):
                audit_fields = {
                    "environment",
                    "python_version",
                    "recorded_at",
                    "verification_code_sha256",
                }
                before = {key: value for key, value in before.items() if key not in audit_fields}
                after = {key: value for key, value in after.items() if key not in audit_fields}
                if before == after:
                    return
        raise ValueError(f"The same processing version produced different content; check the rule version: {path.name}")
    fd, name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def write_json(path, value):
    atomic_bytes(path, json_bytes(value))


def write_text(path, value):
    atomic_bytes(path, value.encode("utf-8"))


def pipeline_id(parameters=None, dataset_id=None):
    # 规则改变时提升版本；源码和环境仅写入独立运行记录。 / Bump versions for rule changes; record source code and environment only in separate run records.
    rules = PROCESSING_RULE_VERSIONS.get(dataset_id, PROCESSING_RULE_VERSION)
    return digest({"rules": rules, "parameters": parameters or {}})


@dataclass
class DatasetContext:
    """Provide parsing inputs for one dataset without temporarily changing other modules.
    单个 dataset 的解析输入，避免临时修改其他模块。
    """

    hole: str
    folder: Path
    inventory: dict
    metadata: dict
    baseline: dict

    def check_hole(self, hole):
        if hole != self.hole:
            raise ValueError("Dataset context belongs to another hole")


def environment():
    return {
        "python": platform.python_version(),
        **{name: importlib.metadata.version(name) for name in ("numpy", "Pillow", "pyarrow")},
    }


def baseline_for(hole, context=None):
    if context is not None:
        context.check_hole(hole)
        return context.baseline
    datasets = source_json(hole, "datasets.json")
    if len(datasets) != 1:
        raise ValueError("Multiple datasets require explicit DatasetContext")
    dataset = datasets[0]
    scalar = source_json(hole, "scalar_manifest.json")
    by_id = {item["log_id"]: item for item in scalar}
    trays = list(scalar_rows(hole, by_id[dataset["tray_id"]]))
    sections = list(scalar_rows(hole, by_id[dataset["section_id"]]))
    end = float(trays[-1][1][1])
    if not end.is_integer():
        raise ValueError("Last tray sample index is not an integer")
    return {
        "sample_count": int(end) + 1,
        "scalar_log_count": len(scalar),
        "spectral_array_count": sum(
            bool(item.get("file")) for item in source_json(hole, "spectral_manifest.json")
        ),
        "thumbnail_count": len(trays),
        "section_count": len(sections),
    }


def arguments(description):
    parser = StepParser(description=description)
    add_hole_arguments(parser)
    args = parser.parse_args()
    configure(args)
    selection = settings.processing_dir / "selected_holes.json"
    previous = read_json(selection)["holes"] if selection.exists() else list(ALLOWED)
    args.holes = selected_holes(args, previous)
    if selection.exists() and not description.startswith("step 1 ") and args.holes != previous:
        raise ValueError("Processing scope differs from step 1; use the original list or a separate working directory")
    return args


def source_json(hole, name, context=None):
    if context is not None:
        context.check_hole(hole)
        return copy.deepcopy(context.metadata[name])
    return read_json(source_path(hole, name))


def snapshot(hole, context=None):
    if context is not None:
        context.check_hole(hole)
        return context.folder, context.inventory
    latest = read_json(settings.prepared_dir / hole / "latest_processing.json")
    folder = inside(settings.prepared_dir, settings.prepared_dir / latest["relative_directory"])
    if not folder.is_relative_to((settings.prepared_dir / hole).resolve()):
        raise ValueError("Processing pointer belongs to another hole")
    inventory = read_json(folder / "1_inventory.json")
    if inventory["pipeline_id"] != pipeline_id(dataset_id=inventory["source_dataset_id"]):
        raise ValueError("The processing rule version changed; prepare this borehole again from step 1")
    return folder, inventory


def require_report(folder, name):
    report = read_json(folder / name)
    if report["status"] not in ("passed", "passed_with_issues"):
        raise ValueError(f"Prerequisite has not passed: {name}")
    return report


def report_base(inventory, step, previous=None):
    return {
        "hole_id": inventory["hole_id"],
        "step": step,
        "pipeline_id": inventory["pipeline_id"],
        "source_snapshot_id": inventory["source_snapshot_id"],
        "report_format": 2,
        "previous_step": read_json(previous)["step"] if previous else None,
    }


def stable_id(*parts):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "etl4:" + ":".join(map(str, parts))))


def issue(code, scope, detail, severity="warning", evidence=None):
    return {
        "code": code,
        "scope": scope,
        "severity": severity,
        "detail": detail,
        "evidence": evidence,
    }


class HashedLines:
    def __init__(self, stream):
        self.stream, self.sha256, self.first = stream, hashlib.sha256(), True

    def __iter__(self):
        for line in self.stream:
            self.sha256.update(line)
            text = line.decode("utf-8-sig" if self.first else "utf-8")
            self.first = False
            yield text


def scalar_rows(hole, entry):
    """Reread and check content during alignment; step 2 has already validated full formats.
    对齐时按需重读并检查内容，步骤 2 已完成完整格式校验。
    """
    with source_path(hole, entry["file"]).open("rb") as stream:
        lines = HashedLines(stream)
        reader = csv.reader(lines, strict=True)
        next(reader)
        for index, row in enumerate(reader):
            if len(row) != 3:
                raise ValueError(f"Malformed scalar row: {entry['file']} / {index}")
            yield index, row
        if lines.sha256.hexdigest() != entry["sha256"]:
            raise ValueError(f"Source hash changed: {entry['file']}")


def run_holes(description, fn):
    args = arguments(description)
    for index, hole in enumerate(args.holes, 1):
        print(f"[{index}/{len(args.holes)}] {hole}: {description}", flush=True)
        try:
            fn(hole)
        except Exception as exc:
            write_json(
                settings.prepared_dir / "failures" / f"{hole}_{time.time_ns()}.json",
                {
                    "hole_id": hole,
                    "step_description": description,
                    "exception_type": type(exc).__name__,
                    "detail": str(exc),
                    "pipeline_id": pipeline_id(),
                    "status": "failed",
                    "completed_holes": args.holes[:index - 1],
                    "remaining_holes": args.holes[index - 1:],
                },
            )
            raise
    if description.startswith("step 1 "):
        write_json(settings.processing_dir / "selected_holes.json", {"holes": args.holes})
    print("Completed: " + ", ".join(args.holes), flush=True)


def fail_main(fn):
    from _file_checks import run_record

    try:
        run_record(run_report_directory, fn)
    except Exception as exc:
        print(f"STOP: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        raise SystemExit(1)


def run_report_directory():
    return settings.processing_dir / "runs"

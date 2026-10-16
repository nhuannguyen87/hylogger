"""Initialize run settings in numbered entry points; importing this module does not parse arguments.
运行配置由编号入口初始化；导入本模块不会解析命令行。
"""

import argparse
import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parent
BASE_HOLES = ("05KCD001", "07THD002", "07THD003", "09ATD015", "09ATD019")


def add_hole_arguments(parser):
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--holes", nargs="+", help="Explicitly selected borehole IDs")
    group.add_argument("--holes-file", type=Path, help="Borehole list from step 0, one ID per line")


def selected_holes(args, default=None):
    if args.holes_file is not None:
        holes = args.holes_file.read_text(encoding="utf-8-sig").splitlines()
        holes = validate_hole_names([hole.strip() for hole in holes if hole.strip()])
        folder = args.holes_file.parent
        status_file = folder / "0_status.json"
        if status_file.exists():
            status = json.loads(status_file.read_text(encoding="utf-8"))
            report = json.loads((folder / "0_raw_check.json").read_text(encoding="utf-8"))
            group = args.holes_file.stem.removesuffix("_holes")
            if status.get("status") != "complete" or status.get("run_id") != report.get("run_id"):
                raise ValueError("Step 0 is incomplete; rerun the precheck before using the list")
            if group not in ("single", "multi") or holes != report["groups"][group]:
                raise ValueError("The list does not match step 0 classification; pending boreholes cannot enter a normal batch")
        return holes
    if args.holes is not None:
        return validate_hole_names(args.holes)
    return validate_hole_names(default) if default is not None else None


def batch_label(holes):
    """Keep report filenames short; batch labels do not contribute to data version identities.
    报告文件名保持短小；批次标签不参与数据版本计算。
    """
    names = sorted(validate_hole_names(holes))
    content = json.dumps(names, ensure_ascii=False).encode("utf-8")
    return "batch_" + hashlib.sha256(content).hexdigest()[:12]


def validate_hole_names(holes):
    """Validate explicitly selected borehole IDs so directory paths cannot be mistaken for IDs.
    检查本次明确选择的孔号，避免把目录路径当作孔号。
    """
    if not isinstance(holes, (list, tuple)) or not holes:
        raise ValueError("Explicitly select at least one borehole")
    if any(not isinstance(h, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", h) for h in holes):
        raise ValueError("Borehole IDs may contain only letters, digits, underscores, and hyphens")
    if len(holes) != len(set(holes)):
        raise ValueError("The input list contains duplicate borehole IDs")
    return list(holes)


@dataclass
class RunSettings:
    """Run one step per process with shared utilities using its selected directories and database.
    一个进程运行一个步骤，公共工具使用该步骤选定的目录和数据库。
    """

    raw_dir: Path = field(default_factory=lambda: Path(os.environ.get("ETL4_RAW_DIR", SOURCE_ROOT)).resolve())
    db_config: Path | None = field(default_factory=lambda: Path(os.environ["ETL4_DB_CONFIG"]).resolve() if os.environ.get("ETL4_DB_CONFIG") else None)
    work_dir: Path = field(
        default_factory=lambda: Path(
            os.environ.get("ETL4_WORK_DIR", SOURCE_ROOT / "refactor_work")
        ).resolve()
    )
    dbname: str = field(
        default_factory=lambda: os.environ.get("ETL4_DBNAME", "etl4_local_batch")
    )

    @property
    def database_dir(self):
        return self.work_dir / "database"

    @property
    def processing_dir(self):
        return self.work_dir / "processing"

    @property
    def prepared_dir(self):
        return self.processing_dir / "outputs"

    @property
    def runtime_dir(self):
        return self.database_dir / "runtime"

    @property
    def config_file(self):
        return self.db_config or self.runtime_dir / "local_config.json"

    @property
    def local_roots(self):
        # 根目录键带位置摘要，切换批次时不会误读另一个工作目录。 / Root keys include a location hash to avoid reading another working directory when switching batches.
        roots = {"etl4": SOURCE_ROOT}
        for kind, path in (("raw", self.raw_dir), ("work", self.work_dir)):
            key = kind + "_" + hashlib.sha256(str(path.resolve()).encode("utf-8")).hexdigest()[:12]
            roots[key] = path.resolve()
        return roots

    @property
    def reports_dir(self):
        return self.database_dir / "reports"

    @property
    def media_work_dir(self):
        return self.database_dir / "media_work"

    @property
    def media_evidence_dir(self):
        return self.database_dir / "media_evidence"

    @property
    def multi_work_dir(self):
        return self.database_dir / "multi_work"

    @property
    def multi_evidence_dir(self):
        return self.multi_work_dir / "evidence"

    @property
    def multi_media_dir(self):
        return self.multi_work_dir / "media"


settings = RunSettings()


def safe_child(root, relative):
    """Restrict manifest paths to the root, rejecting absolute paths, parent traversal, and escaping links.
    清单只保存相对路径，不能通过绝对路径、.. 或链接越出根目录。
    """
    root = Path(root).resolve()
    relative = Path(relative)
    if relative.is_absolute() or relative.drive or ".." in relative.parts:
        raise ValueError("File paths must be relative paths within the root directory")
    path = (root / relative).resolve()
    if path == root or not path.is_relative_to(root):
        raise ValueError("The file path escapes the root directory")
    return path


def raw_path(hole, relative):
    validate_hole_names([hole])
    return safe_child(safe_child(settings.raw_dir, hole), relative)


def path_reference(path):
    """Preserve existing paths and reference external raw/work directories by root keys without drive letters.
    旧路径保持原样；外部 raw/work 用根目录键定位，不写入盘符。
    """
    path = Path(path).resolve()
    for key, root in settings.local_roots.items():
        if path != root and path.is_relative_to(root):
            relative = path.relative_to(root).as_posix()
            return relative if key == "etl4" else f"@{key}/{relative}"
    raise ValueError("The file is outside the configured raw, work, and etl4 directories")


def local_location(reference):
    if reference.startswith("@"):
        root_key, separator, object_key = reference[1:].partition("/")
        if not separator:
            raise ValueError("The file reference is missing its relative path")
    else:
        root_key, object_key = "etl4", reference
    if root_key not in settings.local_roots:
        raise ValueError("Unknown file root; use the --raw-dir and --work-dir that generated this batch manifest")
    safe_child(settings.local_roots[root_key], object_key)
    return {"root_key": root_key, "object_key": object_key}


def local_asset_path(location):
    key = location.get("root_key", "etl4")
    if key not in settings.local_roots:
        raise ValueError("The asset root directory is not defined in the current configuration")
    return safe_child(settings.local_roots[key], location["object_key"])


def resolve_reference(reference):
    return local_asset_path(local_location(reference))


def raw_asset_path(hole, asset):
    if asset.get("source_object_key"):
        return resolve_reference(asset["source_object_key"])
    return raw_path(hole, asset["source_relative_path"])


def input_directory(value):
    """Allow explicit absolute CLI/batch directories while keeping manifest references relative and restricted.
    命令行/批次目录允许显式绝对路径，清单内部仍使用受限相对引用。
    """
    path = Path(value)
    if path.is_absolute():
        if path.resolve() not in settings.local_roots.values():
            path_reference(path)
        return path.resolve()
    return resolve_reference(value)


def configure(options):
    """Configure once after parsing arguments; subsequent processing functions receive directories as needed.
    入口解析完参数后调用一次，后续业务函数按需显式接收目录。
    """
    work = Path(options.work_dir).resolve()
    raw = Path(getattr(options, "raw_dir", settings.raw_dir)).resolve()
    if work == work.parent or SOURCE_ROOT.is_relative_to(work):
        raise ValueError("The working directory must be a separate subdirectory and cannot contain the source root")
    if any(work.is_relative_to(SOURCE_ROOT / name) for name in ("database", "processing")):
        raise ValueError("The working directory cannot overlap database or processing code")
    if raw.is_relative_to(work) or (raw != SOURCE_ROOT and work.is_relative_to(raw)):
        raise ValueError("The working directory and raw directory cannot overlap")
    database = options.dbname
    if database == "etl4_core":
        raise ValueError("Specify a local batch database name; the existing etl4_core delivery database cannot be overwritten")
    settings.work_dir = work
    settings.raw_dir = raw
    selected_config = getattr(options, "db_config", settings.db_config)
    settings.db_config = Path(selected_config).resolve() if selected_config else None
    settings.dbname = database
    return settings


class StepParser(argparse.ArgumentParser):
    """Give numbered entry points the same working-directory and database arguments.
    所有编号入口接受相同的工作目录和数据库参数。
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.add_argument("--raw-dir", type=Path, default=settings.raw_dir, help="Raw data organized by borehole, read directly without copying")
        self.add_argument("--work-dir", type=Path, default=settings.work_dir, help="Separate working directory")
        self.add_argument("--dbname", default=settings.dbname, help="Separate database name")
        self.add_argument("--db-config", type=Path, default=settings.db_config, help="Connection settings for an existing local instance; optional when a DSN is set")

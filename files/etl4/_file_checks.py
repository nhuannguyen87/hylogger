"""Deduplicate file hashes within one check and count actual file scans.
一次校验中的文件哈希去重和实际扫描计数。
"""

import hashlib
import time
import json
import platform
import sys
import uuid
from importlib.metadata import version, PackageNotFoundError
from contextlib import ContextDecorator
from contextvars import ContextVar
from functools import wraps
from pathlib import Path
from datetime import datetime, timezone

_session = ContextVar("etl4_file_check_session", default=None)


class FileChecks(ContextDecorator):
    def __init__(self):
        self.cache = {}
        self.scans = 0
        self.bytes_read = 0
        self.cache_hits = 0

    def __enter__(self):
        self.started = time.perf_counter()
        self.parent = _session.get()
        self.token = _session.set(self)
        return self

    def __exit__(self, *error):
        _session.reset(self.token)
        self.elapsed_seconds = time.perf_counter() - self.started
        if self.parent is not None:
            self.parent.scans += self.scans
            self.parent.bytes_read += self.bytes_read
            self.parent.cache_hits += self.cache_hits

    def stats(self):
        return {
            "sha256_file_scans": self.scans,
            "sha256_bytes_read": self.bytes_read,
            "same_check_cache_hits": self.cache_hits,
            "elapsed_seconds": time.perf_counter() - self.started,
        }


def hash_file(path):
    path = Path(path).resolve()
    before = path.stat()
    key = (path, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
    session = _session.get()
    if session is not None and key in session.cache:
        session.cache_hits += 1
        return session.cache[key]
    checksum = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            checksum.update(block)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    ):
        raise ValueError(f"File changed while being read: {path.name}")
    value = checksum.hexdigest()
    if session is not None:
        session.scans += 1
        session.bytes_read += before.st_size
        session.cache[key] = value
    return value


def fresh_file_checks(function):
    @wraps(function)
    def checked(*args, **kwargs):
        # 每次独立导入或发布重新读取，缓存不跨调用保存。 / Reread files for each independent import or publication; do not cache across calls.
        with FileChecks():
            return function(*args, **kwargs)

    return checked


def run_record(directory, function):
    """Save run records separately from data identities and prepared results.
    运行记录单独保存，不写入数据身份或准备结果。
    """
    record = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "python_version": platform.python_version(),
        "status": "running",
    }
    record["dependencies"] = {}
    for package in ("numpy", "pyarrow", "psycopg", "Pillow"):
        try:
            record["dependencies"][package] = version(package)
        except PackageNotFoundError:
            record["dependencies"][package] = None
    script = Path(sys.argv[0])
    if script.is_file():
        record.update(script=script.name, code_sha256=hash_file(script))
    with FileChecks() as checks:
        try:
            result = function()
            record["status"] = "passed"
            return result
        except SystemExit as error:
            if error.code in (None, 0):
                record = None
            else:
                record.update(status="failed", error_type=type(error).__name__)
            raise
        except BaseException as error:
            record.update(status="failed", error_type=type(error).__name__)
            raise
        finally:
            if record is not None:
                # 入口已经选定配置，再将运行记录写入对应目录。 / Write the run record to the directory selected by the entry point configuration.
                folder = Path(directory() if callable(directory) else directory)
                folder.mkdir(parents=True, exist_ok=True)
                record.update(checks.stats())
                record["finished_at"] = datetime.now(timezone.utc).isoformat()
                path = folder / f"{time.time_ns()}_{uuid.uuid4().hex[:8]}.json"
                path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")

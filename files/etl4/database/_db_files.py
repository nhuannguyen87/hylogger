"""JSON encoding, file access, and content digests for database manifests.

数据库清单的 JSON 编码、文件读写和内容摘要。
"""

import hashlib
import json
import os
import tempfile
import uuid
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from _settings import settings


def serial(value):
    if isinstance(value, (datetime, date, uuid.UUID, Path)):
        return str(value)
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else str(value)
    raise TypeError(type(value).__name__)


def encoded(value):
    return (
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False, default=serial
        )
        + "\n"
    ).encode("utf-8")


def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def sha(path):
    from _file_checks import hash_file

    return hash_file(path)


def inside(root, path):
    root, path = Path(root).resolve(), Path(path).resolve()
    if path == root or not path.is_relative_to(root):
        raise ValueError("Path outside permitted root")
    return path


def write(path, value):
    path = inside(settings.database_dir, path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = value.encode("utf-8") if isinstance(value, str) else encoded(value)
    fd, name = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)

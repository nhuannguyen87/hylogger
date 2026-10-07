"""
Minimal stand-in for database5553/etl4/database/_db_common.py - only the
pieces _media_common.py and _media_reader.py actually import via their
`from _db_common import *`. The original also carries the ETL4 *ingest*
pipeline's helpers (schema hashing, subprocess runners, report writers,
config-file loading); none of that applies to a read-only Django view, so
it isn't reproduced here.

Paths and credentials come from Django settings (ETL4_DATA_DIR,
ETL4_READ_DSN in hylogger/settings.py) instead of the original's
runtime/local_config.json, since this now runs inside the site rather than
as a standalone operator tool.
"""

import hashlib
import json
from pathlib import Path

from django.conf import settings

ETL = Path(settings.ETL4_DATA_DIR)
ROOT = ETL / "database"
PREPARED = ETL / "processing" / "outputs"  # unused by the read path; kept so untouched code referencing it doesn't NameError
ALLOWED = ("05KCD001", "07THD002", "07THD003", "09ATD015", "09ATD019")


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def inside(root, path):
    root, path = Path(root).resolve(), Path(path).resolve()
    if path == root or not path.is_relative_to(root):
        raise ValueError("Path outside permitted root")
    return path


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write(path, value):
    raise NotImplementedError("read-only bridge - nothing here writes into etl4/")

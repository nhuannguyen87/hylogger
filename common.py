#!/usr/bin/env python
"""Shared helpers used by download.py and etl.py.

Reconstructed from the call sites in those two files. If the original module
turns up, prefer it over this one.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence


# --------------------------------------------------------------------------
# time
# --------------------------------------------------------------------------

def utc_now() -> str:
    """Current UTC time as an ISO-8601 string, e.g. 2026-08-24T04:11:07Z."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------
# atomic writes
#
# Every writer below goes to a sibling .tmp file, fsyncs, then renames over
# the target. os.replace is atomic within a filesystem, so a reader (or a
# re-run after Ctrl-C) never sees a half-written file. This matters here
# because download.py treats "file exists and is the right size" as "already
# downloaded" and would otherwise trust a truncated file.
# --------------------------------------------------------------------------

def _atomic_write(target: Path, write) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(target.name + ".tmp")
    try:
        with temp.open("wb") as handle:
            write(handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, target)
    except BaseException:
        temp.unlink(missing_ok=True)
        raise
    return target


def atomic_write_bytes(path: str | os.PathLike[str], payload: bytes) -> Path:
    """Write raw bytes atomically. Used for JPEGs and cached responses."""
    return _atomic_write(Path(path), lambda handle: handle.write(bytes(payload)))


def atomic_write_text(
    path: str | os.PathLike[str],
    payload: str,
    encoding: str = "utf-8",
) -> Path:
    """Write text atomically with LF endings, regardless of platform."""
    data = str(payload).encode(encoding)
    return _atomic_write(Path(path), lambda handle: handle.write(data))


def atomic_write_json(path: str | os.PathLike[str], payload: Any) -> Path:
    """Serialise to JSON and write atomically.

    Non-serialisable values are coerced via as_dict first, so nvcl_kit
    objects can be handed straight to this function.
    """
    text = json.dumps(payload, ensure_ascii=False, indent=2, default=as_dict) + "\n"
    return atomic_write_text(path, text)


# --------------------------------------------------------------------------
# reading
# --------------------------------------------------------------------------

def read_json(path: str | os.PathLike[str]) -> Any:
    """Read a UTF-8 JSON file and return the parsed object.

    Tolerates a UTF-8 BOM, which Windows-written files often carry.
    """
    with Path(path).open("r", encoding="utf-8-sig") as handle:
        return json.load(handle)


def sha256_file(path: str | os.PathLike[str], chunk_size: int = 1 << 20) -> str:
    """Hex SHA-256 of a file, read in chunks so large files stay off the heap."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


# --------------------------------------------------------------------------
# object coercion
# --------------------------------------------------------------------------

def as_dict(value: Any) -> Any:
    """Convert an nvcl_kit result object into plain JSON-safe data.

    nvcl_kit hands back SimpleNamespace-ish objects; this walks them into
    dicts, lists and scalars so they can be written straight to JSON.
    Private attributes and callables are dropped.
    """
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, dict):
        return {str(key): as_dict(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [as_dict(item) for item in value]
    if hasattr(value, "_asdict"):  # namedtuple
        return {key: as_dict(item) for key, item in value._asdict().items()}
    if hasattr(value, "__dict__"):
        return {
            key: as_dict(item)
            for key, item in vars(value).items()
            if not key.startswith("_") and not callable(item)
        }
    if hasattr(value, "__slots__"):
        return {
            key: as_dict(getattr(value, key))
            for key in value.__slots__
            if not key.startswith("_") and hasattr(value, key)
        }
    return str(value)


# --------------------------------------------------------------------------
# filenames
# --------------------------------------------------------------------------

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


def safe_name(value: object, fallback: str = "item", max_length: int = 60) -> str:
    """Turn an arbitrary label into a filesystem-safe filename fragment.

    Accents are folded to ASCII, runs of unsafe characters collapse to a
    single underscore, and the result is truncated. Returns `fallback` if
    nothing usable survives.
    """
    text = str(value or "").strip()
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    text = _UNSAFE.sub("_", text).strip("._-")
    if len(text) > max_length:
        text = text[:max_length].rstrip("._-")
    if not text or text.upper() in {
        "CON", "PRN", "AUX", "NUL",
        *(f"COM{n}" for n in range(1, 10)),
        *(f"LPT{n}" for n in range(1, 10)),
    }:
        return fallback
    return text


# --------------------------------------------------------------------------
# hole ids
# --------------------------------------------------------------------------

_HEADER_NAMES = {"hole_id", "holeid", "hole", "id", "nvcl_id", "nvclid", "borehole_id"}


def _ids_from_file(path: str | os.PathLike[str]) -> list[str]:
    """Pull hole IDs from a plain text file (one per line) or a CSV.

    For a CSV, a hole_id/nvcl_id column is used if the header names one,
    otherwise the first column. Blank lines and # comments are ignored.
    """
    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(f"holes file not found: {source}")

    rows: list[str] = []
    with source.open("r", encoding="utf-8-sig", newline="") as handle:
        if source.suffix.lower() in {".csv", ".tsv"}:
            delimiter = "\t" if source.suffix.lower() == ".tsv" else ","
            table = [row for row in csv.reader(handle, delimiter=delimiter) if row]
            if not table:
                return []
            header = [cell.strip().casefold().replace(" ", "_") for cell in table[0]]
            column = next(
                (index for index, name in enumerate(header) if name in _HEADER_NAMES),
                None,
            )
            body = table[1:] if column is not None else table
            column = column or 0
            rows = [row[column].strip() for row in body if len(row) > column]
        else:
            rows = [line.strip() for line in handle]
            if rows and rows[0].casefold().replace(" ", "_") in _HEADER_NAMES:
                rows = rows[1:]

    return [value for value in rows if value and not value.startswith("#")]


def load_hole_ids(
    hole_ids: Sequence[str] | None = None,
    holes_file: str | os.PathLike[str] | None = None,
) -> list[str]:
    """Combine IDs given on the command line with any listed in a file.

    Order is preserved and duplicates dropped, keeping the first occurrence.
    Command-line IDs come first. download.py relies on the order via
    holes.index(...), so this must stay stable.
    """
    collected = [str(value).strip() for value in (hole_ids or [])]
    if holes_file:
        collected.extend(_ids_from_file(holes_file))

    seen: set[str] = set()
    result: list[str] = []
    for value in collected:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


# --------------------------------------------------------------------------
# misc
# --------------------------------------------------------------------------

def iter_chunks(items: Iterable[Any], size: int) -> Iterable[list[Any]]:
    """Yield lists of at most `size` items. Handy for batching API calls."""
    batch: list[Any] = []
    for item in items:
        batch.append(item)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch

"""Apply this case only to a fresh processing copy."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _support import replace_once, case_main


def apply(code_dir):
    replace_once(code_dir, 'processing/_etl_common.py',
        r'''def scalar_rows(hole, entry):''',
        r'''def scalar_csv_reader(lines, log_id=None):
    # Preserve unquoted commas in reviewed text columns; the first two fields remain depths.
    if log_id in {"8aa08235-f728-419f-b578-ca9e146fe5e", "4d1f0ec0-76a2-4a81-88b1-fa6b12bbd45", "af32891f-c043-41d8-8c08-5648fba52e8", "357bc113-7070-4b2b-b4a3-04679c9a885", "e2abbc70-f418-4482-8caf-45503ff2895"}:
        return (line.rstrip("\r\n").split(",", 2) for line in lines)
    # Preserve literal unmatched quotes in this source; retain three-field and complete-axis checks.
    if log_id == "da0ae1d6-0450-4dbc-989d-da1cea577c3":
        return csv.reader(lines, strict=True, quoting=csv.QUOTE_NONE)
    return csv.reader(lines, strict=True)


def scalar_rows(hole, entry):''', count=1)
    replace_once(code_dir, 'processing/_etl_common.py',
        r'''reader = csv.reader(lines, strict=True)''',
        r'''reader = scalar_csv_reader(lines, entry["log_id"])''', count=1)
    replace_once(code_dir, 'processing/payload_validation.py',
        r'''    baseline_for,
''',
        r'''    baseline_for,
    scalar_csv_reader,
''', count=1)
    replace_once(code_dir, 'processing/payload_validation.py',
        r'''def validate_scalar(path, source_type):''',
        r'''def validate_scalar(path, source_type, log_id=None):''', count=1)
    replace_once(code_dir, 'processing/payload_validation.py',
        r'''reader = csv.reader(lines, strict=True)''',
        r'''reader = scalar_csv_reader(lines, log_id)''', count=1)
    replace_once(code_dir, 'processing/payload_validation.py',
        r'''validate_scalar(path, scalar_meta[entry["log_id"]]["log_type"])''',
        r'''validate_scalar(path, scalar_meta[entry["log_id"]]["log_type"], entry["log_id"])''', count=1)


if __name__ == "__main__":
    case_main('08_literal_text_csv')

"""Apply this case only to a fresh processing copy."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _support import replace_once, case_main


def apply(code_dir):
    replace_once(code_dir, 'processing/_etl_common.py',
        r'''def scalar_rows(hole, entry):''',
        r'''def interval_log_ids(dataset, manifest):
    ids = {dataset["tray_id"], dataset["section_id"]}
    # Two sources retain byte-identical interval log aliases.
    if any(sum(e["log_name"] == name for e in manifest) > 1 for name in ("Tray", "Section")):
        by_id = {entry["log_id"]: entry for entry in manifest}
        for name, role in (("Tray", "tray_id"), ("Section", "section_id")):
            canonical = by_id[dataset[role]]
            aliases = [entry for entry in manifest if entry["log_name"] == name]
            if not aliases or canonical["log_id"] not in {e["log_id"] for e in aliases} or any(not entry.get("sha256") or entry["sha256"] != canonical["sha256"] for entry in aliases):
                raise ValueError("Reviewed interval aliases differ from the dataset-declared source")
            ids.update(entry["log_id"] for entry in aliases)
    return ids


def scalar_rows(hole, entry):''', count=1)
    replace_once(code_dir, 'processing/metadata_normalization.py',
        r'''    METADATA,
''',
        r'''    METADATA,
    interval_log_ids,
''', count=1)
    replace_once(code_dir, 'processing/metadata_normalization.py',
        r'''if lid in (d["tray_id"], d["section_id"])''',
        r'''if lid in interval_log_ids(d, raw["scalar_manifest.json"])''', count=1)
    replace_once(code_dir, 'processing/sample_alignment.py',
        r'''    baseline_for,
''',
        r'''    baseline_for,
    interval_log_ids,
''', count=1)
    replace_once(code_dir, 'processing/sample_alignment.py',
        r'''e["log_id"] not in (dataset["tray_id"], dataset["section_id"])''',
        r'''e["log_id"] not in interval_log_ids(dataset, manifest)''', count=1)


if __name__ == "__main__":
    case_main('03_duplicate_interval_logs')

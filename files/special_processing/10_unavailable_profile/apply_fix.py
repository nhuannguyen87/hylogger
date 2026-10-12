"""Apply this case only to a fresh processing copy."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _support import replace_once, case_main


def apply(code_dir):
    replace_once(code_dir, 'processing/1_inventory.py',
        r'''            source_path(hole, relative)
''',
        r'''            path = source_path(hole, relative)
            # Keep unavailable metadata without inventing data.
            if kind == "profile" and entry.get("status") == "unavailable":
                if path.exists() or entry.get("sha256") or entry.get("nbytes") != 0 or entry.get("records") != 0:
                    raise ValueError(f"Unavailable profile has contradictory payload evidence: {relative}")
                continue
''', count=1)
    replace_once(code_dir, 'processing/metadata_normalization.py',
        r'''if manifest.get("file") else []''',
        r'''if manifest.get("file") and manifest.get("status") != "unavailable" else []''', count=1)
    replace_once(code_dir, 'processing/metadata_normalization.py',
        r'''"axis_binding_status": "pending_step_4" if kind != "image" else "not_a_dense_axis",''',
        r'''"axis_binding_status": ("metadata_only" if not paths else "pending_step_4") if kind != "image" else "not_a_dense_axis",''', count=1)
    replace_once(code_dir, 'processing/metadata_normalization.py',
        r'''elif kind == "profile":
                stats = observed[paths[0]]''',
        r'''elif kind == "profile":
                stats = observed[paths[0]] if paths else {}''', count=1)
    replace_once(code_dir, 'processing/metadata_normalization.py',
        r'''"observed_channel_count": stats["observed_channel_count"]''',
        r'''"observed_channel_count": stats.get("observed_channel_count")''', count=1)
    replace_once(code_dir, 'processing/sample_alignment.py',
        r'''    for profile in raw["profilometer_manifest.json"]:
''',
        r'''    for profile in raw["profilometer_manifest.json"]:
        if profile.get("status") == "unavailable":
            continue
''', count=1)
    replace_once(code_dir, 'processing/sample_alignment.py',
        r'''        for e in raw["profilometer_manifest.json"]
''',
        r'''        for e in raw["profilometer_manifest.json"] if e.get("status") != "unavailable"
''', count=1)
    replace_once(code_dir, 'processing/columnar_storage.py',
        r'''if log["log_kind"] not in ("scalar", "profile"):''',
        r'''if log["log_kind"] not in ("scalar", "profile") or not log["payload_paths"]:''', count=1)


if __name__ == "__main__":
    case_main('10_unavailable_profile')

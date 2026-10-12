"""Apply this case only to a fresh processing copy."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _support import replace_once, case_main


def apply(code_dir):
    replace_once(code_dir, 'processing/sample_alignment.py',
        r'''            if not np.array_equal(numeric_values(hole, entry), expected):
                raise ValueError("virtual_section differs from declared Section intervals")''',
        r'''            actual_virtual = numeric_values(hole, entry)
            if not np.array_equal(actual_virtual, expected):
                # SPECIAL_VIRTUAL_FALLBACK: Entry for explicit special handling.
                tray_counts = {}
                within_tray = np.empty(n, dtype=np.int32)
                for section in sections:
                    parent = section["parent_interval_id"]
                    tray_counts[parent] = tray_counts.get(parent, 0) + 1
                    within_tray[section["sample_no_from"]:section["sample_no_to"] + 1] = tray_counts[parent]
                if not np.array_equal(actual_virtual, within_tray):
                    raise ValueError("virtual_section does not match verified tray-local section numbering")
                issues.append(issue("VIRTUAL_SECTION_NUMBERED_WITHIN_TRAY", hole,
                    "All source virtual_section values exactly match one-based section order within each declared tray. Original virtual_section values and global Section labels are both preserved.",
                    "info", {"source_log_id": entry["log_id"], "sample_count": n}))''', count=1)


if __name__ == "__main__":
    case_main('02_tray_local_sections')

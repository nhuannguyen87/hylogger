"""Apply this case only to a fresh processing copy."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _support import replace_once, case_main


def apply(code_dir):
    replace_once(code_dir, 'processing/sample_alignment.py',
        r'''            or s["decreasing_start_count"]''',
        r'''            or (s["decreasing_start_count"] and dataset["dataset_id"] not in {"6a23f66a-00ff-44ae-9d3d-662a1c2a653", "340f4c72-49fb-43cd-8a30-c6efbedeff6"})''', count=1)
    replace_once(code_dir, 'processing/sample_alignment.py',
        r'''    issues, position_evidence = [], []
''',
        r'''    issues, position_evidence = [], []
    decreases = (np.flatnonzero(np.diff(depth) < 0) + 1).tolist()
    if decreases:
        # Reviewed tray overlap keeps original depth and sample order.
        reviewed_resets = {"6a23f66a-00ff-44ae-9d3d-662a1c2a653": [6030], "340f4c72-49fb-43cd-8a30-c6efbedeff6": [27800]}
        if decreases != reviewed_resets.get(dataset["dataset_id"]) or not set(decreases).issubset(set(tray_starts.tolist())) or not set(decreases).issubset(set(section_starts.tolist())):
            raise ValueError("Unexpected nonmonotone depth outside the reviewed tray boundary")
        issues.append(issue("SOURCE_TRAYS_OVERLAP_IN_DEPTH", hole,
            "All dense logs share the same source depth vector. The depth reset occurs at the declared tray and section boundary; original values and sample order are preserved.",
            "warning", {"sample_numbers": decreases, "previous_depth_m": float(depth[decreases[0] - 1]), "next_depth_m": float(depth[decreases[0]])}))
''', count=1)


if __name__ == "__main__":
    case_main('07_reviewed_depth_resets')

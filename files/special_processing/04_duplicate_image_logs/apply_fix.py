"""Apply this case only to a fresh processing copy."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _support import replace_once, case_main


def apply(code_dir):
    replace_once(code_dir, 'processing/sample_alignment.py',
        r'''    for log in image_logs:
''',
        r'''    # Preserve duplicate log metadata and bind one identical image sequence.
    if len(image_logs) > 1:
        image_logs = sorted(image_logs, key=lambda row: row["log_id"])
        signatures = []
        for image_log in image_logs:
            items = sorted((f for f in image_manifest["files"] if f["log_id"] == image_log["log_id"]),
                           key=lambda f: int(f["sample_no"]))
            declared = {int(r["sample_no"]): r for r in raw["tray_depths.json"][image_log["log_id"]]}
            if len(declared) != len(raw["tray_depths.json"][image_log["log_id"]]) or set(declared) != set(range(len(trays))) or [int(f["sample_no"]) for f in items] != list(range(len(trays))):
                raise ValueError("Duplicate image log has an incomplete depth sequence")
            signatures.append([
                (f["dataset_id"], int(f["sample_no"]), float(f["depth_from"]), float(f["depth_to"]),
                 float(declared[int(f["sample_no"])]["start_value"]),
                 float(declared[int(f["sample_no"])]["end_value"]), stats[f["file"]]["sha256"])
                for f in items
            ])
        if any(signature != signatures[0] for signature in signatures[1:]):
            raise ValueError("Duplicate image logs differ in image content or source associations")
        issues.append(issue("IDENTICAL_IMAGE_LOGS", hole,
            "Both source image sequences and metadata are retained. The lexically first log supplies display frames after every ordinal, depth bound and image SHA256 matched.",
            "info", {"selected_log_id": image_logs[0]["log_id"], "duplicate_log_ids": [row["log_id"] for row in image_logs[1:]],
                     "compared_images": len(signatures[0])}))
        image_logs = image_logs[:1]
    for log in image_logs:
''', count=1)
    replace_once(code_dir, 'processing/sample_alignment.py',
        r'''len(trays) != base["thumbnail_count"]''',
        r'''len(trays) != base.get("tray_count", base["thumbnail_count"])''', count=1)
    replace_once(code_dir, 'processing/_etl_common.py',
        r'''        "thumbnail_count": len(trays),''',
        r'''        # Validate every file but count display trays once.
        "thumbnail_count": len(trays) * sum(e["log_name"] == "Tray Thumbnail Images" for e in source_json(hole, "images_manifest.json")["logs"]),
        "tray_count": len(trays),''', count=1)


if __name__ == "__main__":
    case_main('04_duplicate_image_logs')

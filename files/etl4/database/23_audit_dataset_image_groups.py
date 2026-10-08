"""Build an image manifest by dataset, image log, and tray.
按 dataset、图片日志和托盘建立图片清单。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _run_reports import main_guard
from _db_files import read, sha
from _settings import configure
from _media_common import decode, require, save_stage, source_folder, source_path, units


def main():
    frames = []
    seen = set()
    for u in units():
        groups = read(source_path(u, "image_logs_by_dataset.json"))
        folder = source_folder(u)
        intervals = read(folder / "4_core_intervals.json")
        for f in read(folder / "4_image_frames.json"):
            matches = [
                g
                for g in groups
                if g["dataset_id"] == u["source_dataset_id"] and g["log_id"] == f["source_log_id"]
            ]
            require(len(matches) == 1, "Ambiguous dataset/image log association")
            key = (u["dataset_id"], f["source_log_id"], f["image_ordinal"])
            require(key not in seen, "Duplicate image identity")
            seen.add(key)
            sections = sorted(
                (
                    s
                    for s in intervals
                    if s["interval_kind"] == "section"
                    and s["parent_interval_id"] == f["core_interval_id"]
                ),
                key=lambda s: s["ordinal"],
            )
            require(sections, "Tray has no source Sections")
            im = decode(source_path(u, f["source_path"]))
            require(im.size == (f["width_px"], f["height_px"]), "Source dimensions differ")
            frames.append(
                {
                    **u,
                    **f,
                    "source_sha256": sha(source_path(u, f["source_path"])),
                    "sections": sections,
                    "row_count": len(sections),
                }
            )
    save_stage(
        23,
        {
            "status": "passed",
            "frames": frames,
            "frame_count": len(frames),
            "section_count": sum(f["row_count"] for f in frames),
        },
    )


if __name__ == "__main__":
    from _settings import StepParser

    args = StepParser().parse_args()
    configure(args)
    main_guard(main)

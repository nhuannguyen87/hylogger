"""Link image rows to actual sample intervals and retain the approximate-position explanation.
将图片行关联到真实样本区间，保留近似定位的说明。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _run_reports import main_guard
from _settings import configure
from _media_rows import bind_row
from _media_common import confirmed_direction, require, save_stage, stage, units


def main():
    reviewed = stage(25)
    require(reviewed["status"] == "reviewed", "Image regions need recorded visual review")
    direction = stage(20)["direction"]
    rows = []
    covered = {u["dataset_id"]: 0 for u in units()}
    for r in reviewed["rows"]:
        row = bind_row(r, confirmed_direction(r["hole_id"], direction))
        covered[r["dataset_id"]] += row["sample_no_to"] - row["sample_no_from"] + 1
        rows.append(row)
    for u in units():
        require(
            covered[u["dataset_id"]] == u["sample_count"],
            "Incomplete or repeated row sample coverage",
        )
    save_stage(
        26, {"status": "passed", "rows": rows, "sample_counts": covered, "direction": direction}
    )


if __name__ == "__main__":
    from _settings import StepParser

    args = StepParser().parse_args()
    configure(args)
    main_guard(main)

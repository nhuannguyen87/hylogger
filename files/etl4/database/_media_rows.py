"""Share image row boundary and sample association algorithms across single and multiple datasets.
单 dataset 和多 dataset 共用的图片行边界、样本关联算法。
"""

import numpy as np
from _media_common import INDICATOR, require


def row_boundaries(image, row_count):
    """Look for bright dividers near expected row boundaries and use equal-height rows when dividers are unclear.
    在预计行间位置寻找亮度分隔线，不明显时按等高模板划分。
    """
    height = image.height
    require(row_count > 0 and image.width > 16, "Image too small for section layout")
    pitch = height / row_count
    profile = np.median(np.asarray(image, dtype=float)[:, 8:-8, :].mean(axis=2), axis=1)
    boundaries = [0]
    details = []
    for index in range(1, row_count):
        center = round(index * pitch)
        radius = max(2, round(0.12 * pitch))
        options = range(max(3, center - radius), min(height - 3, center + radius + 1))
        scores = {y: float(profile[y] - (profile[y - 3] + profile[y + 3]) / 2) for y in options}
        require(scores, "Image too small for section layout")
        best = max(scores, key=scores.get)
        cut = best if scores[best] >= 12 else center
        boundaries.append(cut)
        details.append({
            "expected": center, "cut": cut, "ridge_score": scores[best],
            "method": "local_divider_ridge" if scores[best] >= 12 else "reviewed_equal_pitch",
        })
    boundaries.append(height)
    require(
        all(0 <= a < b <= height and b - a <= pitch * 1.35 for a, b in zip(boundaries, boundaries[1:])),
        "Suspect row bounds",
    )
    return boundaries, details


def bind_row(row, direction):
    """Use the source section axis, tray, and sample range without deduplicating depths.
    使用来源 section 的轴、托盘和样本范围，不按深度去重。
    """
    section = row["section"]
    require(
        section["axis_id"] == row["source_axis_id"]
        and section["parent_interval_id"] == row["core_interval_id"],
        "Wrong tray/axis",
    )
    a, b = section["sample_no_from"], section["sample_no_to"]
    require(0 <= a <= b < row["sample_count"], "Row sample range outside source axis")
    return {
        **row, "sample_no_from": a, "sample_no_to": b,
        "mapping_level": "section_interval", "mapping_status": "metadata_associated",
        "mapping_method": "source_section_order_with_layout_template", **direction,
        "indicator_method": "sample_index_linear", "indicator_version": 1,
        "indicator_status": "approximate", "indicator_parameters": INDICATOR,
        "anchor_points": None, "error_px": None, "error_depth_m": None,
    }

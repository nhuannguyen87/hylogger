"""Create lossless row images and compare every pixel after reading them back.
生成无损行图，回读后逐像素核对。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import hashlib
from collections import defaultdict
from _db_files import inside, sha
from _run_reports import main_guard
from _settings import settings, configure
from _media_common import decode, require, save_stage, source_path, stage


def build_crops(source_rows, work):
    """Decode each source image once and keep the returned rows in their input order.
    同一张原图只解码一次，返回清单仍保持输入行顺序。
    """
    grouped = defaultdict(list)
    for index, row in enumerate(source_rows):
        grouped[source_path(row, row["source_path"]).resolve()].append((index, row))
    rows = [None] * len(source_rows)
    for original, items in grouped.items():
        checksum = sha(original)
        require(all(row["source_sha256"] == checksum for _, row in items), "Changed source image")
        with decode(original) as im:
            for index, row in items:
                rows[index] = build_crop(row, im, work)
    return rows


def build_crop(r, im, work):
    """Save and read back one row to confirm that PNG encoding preserves its pixels.
    保存一行并回读，确认 PNG 编码没有改变像素。
    """
    x = r["x_px"]
    y = r["y_px"]
    w = r["width_px"]
    h = r["height_px"]
    require(
        x >= 0 and y >= 0 and x + w <= im.width and y + h <= im.height, "Crop outside source"
    )
    crop = im.crop((x, y, x + w, y + h))
    rel = f"crops/{r['dataset_id']}/{r['source_log_id']}/{r['row_key']}.png"
    p = inside(work, work / rel)
    p.parent.mkdir(parents=True, exist_ok=True)
    crop.save(p, format="PNG", optimize=False, compress_level=6)
    check = decode(p)
    require(
        check.size == crop.size and check.tobytes() == crop.tobytes(),
        "Crop pixels changed during encoding",
    )
    size = p.stat().st_size
    return (
        {
            **r,
            "crop_work_path": rel,
            "crop_sha256": sha(p),
            "crop_byte_size": size,
            "pixel_sha256": hashlib.sha256(crop.tobytes()).hexdigest(),
            "recipe": {
                "version": 1,
                "format": "PNG",
                "mode": "RGB",
                "crop_box_half_open": [x, y, x + w, y + h],
                "source_sha256": r["source_sha256"],
                "exif_transform": "none",
                "rotation_deg": 0,
                "flip_x": False,
                "coordinate_space": "native_decoded_pixels",
                "pixel_equality_verified": True,
            },
        }
    )


def main(work=None):
    work = settings.media_work_dir if work is None else work
    rows = build_crops(stage(26, work)["rows"], work)
    save_stage(
        27,
        {
            "status": "passed",
            "rows": rows,
            "row_asset_count": len(rows),
            "crop_bytes": sum(row["crop_byte_size"] for row in rows),
            "lossless_decoded_pixel_equality": True,
        },
        work=work,
    )


if __name__ == "__main__":
    from _settings import StepParser

    args = StepParser().parse_args()
    configure(args)
    main_guard(main)

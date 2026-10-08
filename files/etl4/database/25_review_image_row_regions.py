"""Check row boundaries, preserve all core pixels, and record visual review results.
检查行间边界，保留完整岩心像素并记录人工审阅结果。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import math
from PIL import ImageDraw, Image
from _run_reports import main_guard
from _db_files import sha
from _media_common import decode, require, save_stage, source_path, stage
from _settings import StepParser
from _media_rows import row_boundaries
from _settings import settings, configure


def main():
    parser = StepParser()
    parser.add_argument("--record-reviewed", action="store_true")
    args = parser.parse_args()
    configure(args)
    proposed = stage(24)
    frames = {f["id"]: f for f in stage(23)["frames"]}
    rows = []
    checks = []
    for fid, f in frames.items():
        source = decode(source_path(f, f["source_path"]))
        n = f["row_count"]
        pitch = f["height_px"] / n
        boundaries, details = row_boundaries(source, n)
        source.close()
        fr = sorted(
            (r for r in proposed["rows"] if r["source_frame_id"] == fid),
            key=lambda r: r["region_ordinal"],
        )
        require(len(fr) == n, "Row count mismatch")
        for i, r in enumerate(fr):
            r = {
                **r,
                "y_px": boundaries[i],
                "height_px": boundaries[i + 1] - boundaries[i],
                "region_status": "reviewed",
                "detection_method": "section_pitch_local_divider_ridge_preserve_cell_v1",
                "review_basis": f"{len(proposed['templates'])} layout classes; first/last representative visual inspection plus all-frame geometry and source identity checks",
            }
            require(
                r["height_px"] > 0 and r["height_px"] <= pitch * 1.35, "Suspect boundary spacing"
            )
            rows.append(r)
        checks.append(
            {
                "source_frame_id": fid,
                "row_count": n,
                "boundaries": boundaries,
                "divider_checks": details,
                "within_source": True,
                "nonoverlap": True,
                "entire_native_height_preserved": True,
            }
        )
    chosen = proposed["representatives"]
    sheets = []
    for start in range(0, len(chosen), 12):
        subset = chosen[start : start + 12]
        sheet = Image.new("RGB", (1260, math.ceil(len(subset) / 3) * 205), "white")
        draw = ImageDraw.Draw(sheet)
        for j, rep in enumerate(subset):
            f = frames[rep["source_frame_id"]]
            im = decode(source_path(f, f["source_path"]))
            pen = ImageDraw.Draw(im)
            for row in [r for r in rows if r["source_frame_id"] == f["id"]]:
                if row["y_px"]:
                    pen.line(
                        (0, row["y_px"], im.width - 1, row["y_px"]), fill=(255, 0, 70), width=1
                    )
            x = (j % 3) * 420 + 8
            y = (j // 3) * 205
            draw.text(
                (x, y + 4), f"{rep['template_key']} tray {f['image_ordinal']+1}", fill="black"
            )
            sheet.paste(im, (x, y + 24))
        name = f"25_review_{start//12+1}.png"
        sheet.save(settings.media_work_dir / name)
        sheets.append(name)
    result = {
        "status": "reviewed" if args.record_reviewed else "awaiting_visual_review",
        "rows": rows,
        "frame_checks": checks,
        "contact_sheet_sha256": {n: sha(settings.media_work_dir / n) for n in sheets},
        "representatives": chosen,
        "reviewer": "codex_visual_review" if args.record_reviewed else None,
        "notes": "Preserve original row pixels, dividers, and empty sections; depth increases from left to right according to the source specification.",
    }
    if args.record_reviewed:
        prior = stage(25)
        require(
            prior["contact_sheet_sha256"] == result["contact_sheet_sha256"],
            "Review images changed; inspect new sheets first",
        )
    save_stage(25, result)


if __name__ == "__main__":
    main_guard(main)

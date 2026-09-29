"""Step 4: establish the point-depth axis and prove sample/interval associations."""
import io
import math
from decimal import Decimal

import numpy as np

from _etl_common import (
    atomic_bytes, baseline_for, fail_main, issue, read_json, report_base, require_report,
    run_holes, scalar_rows, sha_file, snapshot, source_json, stable_id, write_json, write_text,
)


def numeric_values(hole, entry):
    return np.asarray([float("nan") if r[2] in ("", "null") else float(r[2])
                       for _, r in scalar_rows(hole, entry)], dtype=np.float64)


def depth_difference(a, b):
    # Both exports use decimal depths; avoid binary rounding at the 0.00001 m boundary.
    return Decimal(str(a)) - Decimal(str(b))


def expand_intervals(hole, entry, n, kind, axis_id):
    assignments = np.full(n, -1, dtype=np.int32)
    intervals, next_start = [], 0
    for ordinal, row in scalar_rows(hole, entry):
        a, b = float(row[0]), float(row[1])
        if not a.is_integer() or not b.is_integer():
            raise ValueError(f"Noninteger sample-index interval in {kind}")
        start, end = int(a), int(b)
        if start != next_start or end < start or end >= n:
            raise ValueError(f"Gap, overlap or out-of-range {kind}: {start}..{end}")
        assignments[start:end + 1] = ordinal
        intervals.append({"id": stable_id(axis_id, kind, ordinal), "axis_id": axis_id,
                          "interval_kind": kind, "ordinal": ordinal, "source_label": row[2],
                          "source_log_id": entry["log_id"], "source_data_row": ordinal,
                          "sample_no_from": start, "sample_no_to": end,
                          "source_interval_convention": "closed", "parent_interval_id": None})
        next_start = end + 1
    if next_start != n or (assignments < 0).any():
        raise ValueError(f"{kind} does not cover the sample axis exactly")
    return intervals, assignments


def align_hole(hole):
    folder, inv = snapshot(hole)
    integrity = require_report(folder, "2_integrity.json")
    metadata_report = require_report(folder, "3_metadata_report.json")
    if sha_file(folder / "3_normalized_metadata.json") != metadata_report["normalized_metadata_sha256"]:
        raise ValueError("Normalized metadata changed after step 3")
    meta = read_json(folder / "3_normalized_metadata.json")
    dataset = meta["raw_metadata"]["datasets.json"][0]
    manifest = meta["raw_metadata"]["scalar_manifest.json"]
    by_id = {e["log_id"]: e for e in manifest}
    stats = {e["path"]: e for e in integrity["files"]}
    n = baseline_for(hole)["sample_count"]
    dense = [e for e in manifest if e["log_id"] not in (dataset["tray_id"], dataset["section_id"])]
    anchor = min(dense, key=lambda e: e["log_id"])
    anchor_stats = stats[anchor["file"]]
    for entry in dense:
        s = stats[entry["file"]]
        if s["row_count"] != n or not s["all_coordinates_are_points"] or s["decreasing_start_count"]:
            raise ValueError(f"Not an aligned nondecreasing point log: {entry['log_id']}")
        if s["start_vector_sha256"] != anchor_stats["start_vector_sha256"] or s["end_vector_sha256"] != anchor_stats["end_vector_sha256"]:
            raise ValueError(f"Depth-vector mismatch: {entry['log_id']}")
    depth = np.asarray([float(row[0]) for _, row in scalar_rows(hole, anchor)], dtype=np.float64)
    axis_id = meta["proposed_axis_id"]
    trays, tray_index = expand_intervals(hole, by_id[dataset["tray_id"]], n, "tray", axis_id)
    sections, section_index = expand_intervals(hole, by_id[dataset["section_id"]], n, "section", axis_id)
    for interval in trays + sections:
        a, b = interval["sample_no_from"], interval["sample_no_to"]
        interval.update({"observed_depth_min_m": float(depth[a]), "observed_depth_max_m": float(depth[b])})
    for section in sections:
        a, b = section["sample_no_from"], section["sample_no_to"]
        if tray_index[a] != tray_index[b]:
            raise ValueError("Section crosses a tray boundary")
        section["parent_interval_id"] = trays[int(tray_index[a])]["id"]
    samples = np.arange(n, dtype=np.int64)
    tray_starts = np.asarray([t["sample_no_from"] for t in trays], dtype=np.int64)
    section_starts = np.asarray([s["sample_no_from"] for s in sections], dtype=np.int64)
    tray_sample = samples - tray_starts[tray_index] + 1
    section_sample = samples - section_starts[section_index] + 1
    issues, position_evidence = [], []
    for name, expected in (("TraySamp", tray_sample), ("SecSamp", section_sample)):
        entries = [e for e in manifest if e["log_name"] == name]
        if not entries:
            raise ValueError(f"Missing reviewed alignment evidence: {name}")
        for entry in entries:
            values = numeric_values(hole, entry)
            if not np.array_equal(values, expected):
                raise ValueError(f"Position evidence disagrees: {entry['log_id']}")
            position_evidence.append({"source_log_id": entry["log_id"], "role": name,
                                      "method": "all rows equal interval-relative one-based sample number"})
    distance_entries = sorted([e for e in manifest if e["log_name"] == "SecDist (mm)"], key=lambda e: e["log_id"])
    distance = np.full(n, np.nan)
    if distance_entries:
        candidates = [numeric_values(hole, e) for e in distance_entries]
        if all(np.array_equal(candidates[0], candidate, equal_nan=True) for candidate in candidates[1:]):
            distance = candidates[0]
            position_evidence.extend({"source_log_id": e["log_id"], "role": "section_distance_mm"} for e in distance_entries)
        else:
            issues.append(issue("SECTION_DISTANCE_ASSOCIATION_UNRESOLVED", hole, "Conflicting source logs preserved; canonical distance left null."))
    for entry in manifest:
        if entry["log_name"] == "virtual_section":
            expected = np.asarray([float(sections[int(i)]["source_label"]) for i in section_index])
            if not np.array_equal(numeric_values(hole, entry), expected):
                raise ValueError("virtual_section differs from declared Section intervals")
    profile_checks = []
    for profile in meta["raw_metadata"]["profilometer_manifest.json"]:
        p = stats[profile["file"]]
        cache = folder / p["positive_min_cache"]
        if sha_file(cache) != p["positive_min_cache_sha256"]:
            raise ValueError("Validated profile cache changed")
        positive_min = np.asarray(read_json(cache), dtype=np.float64)
        if len(positive_min) != n:
            raise ValueError("Profile count differs from sample axis")
        entries = [e for e in manifest if e["log_name"] == "prof_min"]
        if not entries:
            raise ValueError("Missing prof_min alignment evidence")
        for entry in entries:
            values = numeric_values(hole, entry)
            differences = np.abs(values - positive_min)
            if not np.isfinite(differences).all() or not (differences <= 1e-4).all():
                raise ValueError("Profile positive minima do not align with prof_min")
            profile_checks.append({"profile_log_id": profile["log_id"], "scalar_log_id": entry["log_id"],
                                   "sample_count": n, "max_absolute_difference": float(differences.max()),
                                   "tolerance": 1e-4,
                                   "method": "minimum strictly positive profile value; all-zero row represented by 0; alignment evidence only"})
    image_frames = []
    image_manifest = meta["raw_metadata"]["images_manifest.json"]
    image_logs = [e for e in image_manifest["logs"] if e["log_name"] == "Tray Thumbnail Images"]
    for log in image_logs:
        files = sorted([f for f in image_manifest["files"] if f["log_id"] == log["log_id"]], key=lambda f: int(f["sample_no"]))
        depths = {int(e["sample_no"]): e for e in meta["raw_metadata"]["tray_depths.json"][log["log_id"]]}
        if len(files) != len(trays) or [int(f["sample_no"]) for f in files] != list(range(len(trays))):
            raise ValueError("Thumbnail ordinal/count does not match the tray sequence")
        for ordinal, file in enumerate(files):
            if file["dataset_id"] != dataset["dataset_id"]:
                raise ValueError("Image dataset ID mismatch")
            tray = trays[ordinal]
            reported = depths[ordinal]
            a, b = float(file["depth_from"]), float(file["depth_to"])
            if (a, b) != (float(reported["start_value"]), float(reported["end_value"])):
                raise ValueError("Image manifest/tray_depths mismatch")
            delta_from = depth_difference(a, tray["observed_depth_min_m"])
            delta_to = depth_difference(b, tray["observed_depth_max_m"])
            if abs(delta_from) > Decimal("0.00001") or abs(delta_to) > Decimal("0.00001"):
                raise ValueError("Thumbnail-to-tray depth bounds need review")
            observed_image = stats[file["file"]]
            image_frames.append({"id": stable_id(axis_id, log["log_id"], ordinal),
                                 "source_log_id": log["log_id"], "image_ordinal": ordinal,
                                 "core_interval_id": tray["id"], "source_tray_label": tray["source_label"],
                                 "depth_from_m": a, "depth_to_m": b, "source_path": file["file"],
                                 "width_px": observed_image["width_px"], "height_px": observed_image["height_px"],
                                 "image_kind": "tray_thumbnail", "pixel_depth_mapping": None,
                                 "depth_from_difference_m": float(delta_from), "depth_to_difference_m": float(delta_to),
                                 "depth_comparison_tolerance_m": 0.00001,
                                 "association_basis": "dataset ID, complete ordinal sequence and both tray depth bounds within declared comparison tolerance"})
    base = baseline_for(hole)
    if len(trays) != base["thumbnail_count"] or len(sections) != base["section_count"]:
        raise ValueError("Interval count differs from the reviewed baseline")
    buffer = io.BytesIO()
    np.savez_compressed(buffer, sample_no=samples, md_m=depth, tray_index=tray_index,
                        section_index=section_index, tray_sample_no=tray_sample,
                        section_sample_no=section_sample, section_distance_mm=distance)
    atomic_bytes(folder / "4_sample_axis.npz", buffer.getvalue())
    write_json(folder / "4_core_intervals.json", trays + sections)
    write_json(folder / "4_image_frames.json", image_frames)
    bindings = [{"source_log_id": e["log_id"], "axis_id": axis_id, "status": "verified",
                 "basis": "all point-depth coordinates equal in source row order"} for e in dense]
    bindings.extend({"source_log_id": e["log_id"], "axis_id": axis_id, "status": "verified",
                     "basis": "continuous explicit sampleNo and prof_min cross-check"}
                    for e in meta["raw_metadata"]["profilometer_manifest.json"])
    # Spectral byte shape alone is deliberately not an axis binding.
    write_json(folder / "4_axis_bindings.json", bindings)
    report = report_base(inv, 4, folder / "3_metadata_report.json")
    report.update({"status": "passed_with_issues" if issues else "passed", "axis_id": axis_id,
                   "sample_count": n, "dense_logs_aligned": len(dense), "depth_anchor_log_id": anchor["log_id"],
                   "anchor_choice_basis": "lexically first source ID after all dense depth vectors verified equal",
                   "unique_depth_count": int(np.unique(depth).size), "duplicate_depth_samples_preserved": int(n - np.unique(depth).size),
                   "observed_depth_min_m": float(depth.min()), "observed_depth_max_m": float(depth.max()),
                   "tray_count": len(trays), "section_count": len(sections), "image_frames_bound": len(image_frames),
                   "position_evidence": position_evidence, "profile_alignment_checks": profile_checks,
                   "spectral_sample_binding_status": "unconfirmed_matrix_order", "issues": issues,
                   "artifacts": {name: sha_file(folder / name) for name in (
                       "4_sample_axis.npz", "4_core_intervals.json", "4_image_frames.json", "4_axis_bindings.json")}})
    write_json(folder / "4_alignment_report.json", report)
    write_text(folder / "4_alignment_report.md", f"# {hole} 样本与位置对齐\n\n"
               f"{n:,} 个样本，{len(dense)} 条密集标量通过逐坐标哈希对照；"
               f"保留 {report['duplicate_depth_samples_preserved']:,} 个重复孔深样本。\n\n"
               f"已验证 {len(trays)} 个托盘、{len(sections)} 个行段、{len(image_frames)} 张图片关联及轮廓对应关系。\n\n"
               "光谱矩阵顺序未确认，未生成已验证的样本光谱关联。未生成真实三维轨迹或像素孔深标定。\n")
    print(f"  {n:,} samples; {len(dense)} dense logs; {len(image_frames)} images aligned", flush=True)


if __name__ == "__main__":
    fail_main(lambda: run_holes("step 4 align samples", align_hole))

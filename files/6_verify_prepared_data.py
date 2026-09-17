"""Step 6: cross-hole reconciliation and representative depth-range reads."""
from collections import Counter
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from _etl_common import (
    ALLOWED, BASELINE, OUTPUT, arguments, fail_main, inside, pipeline_id, read_json,
    require_report, scalar_rows, sha_file, snapshot, write_json, write_text,
)


def verify_hole(hole):
    folder, inv = snapshot(hole)
    report_files = ["1_inventory.json", "2_integrity.json", "3_metadata_report.json",
                    "4_alignment_report.json", "5_storage_report.json"]
    reports = [require_report(folder, name) for name in report_files]
    for i, report in enumerate(reports):
        if report["pipeline_id"] != inv["pipeline_id"] or report["source_snapshot_id"] != inv["source_snapshot_id"]:
            raise ValueError("Step reports mix versions")
        if i and report["previous_report_sha256"] != sha_file(folder / report_files[i - 1]):
            raise ValueError("Step report dependency hash mismatch")
    integrity, metadata_report, alignment, storage = reports[1:]
    if sha_file(folder / "3_normalized_metadata.json") != metadata_report["normalized_metadata_sha256"]:
        raise ValueError("Normalized metadata checksum mismatch")
    meta = read_json(folder / "3_normalized_metadata.json")
    for name, expected in alignment["artifacts"].items():
        if sha_file(inside(folder, folder / name)) != expected:
            raise ValueError(f"Alignment artifact checksum mismatch: {name}")
    for asset in storage["assets"]:
        path = inside(folder, folder / asset["relative_path"])
        if sha_file(path) != asset["sha256"] or path.stat().st_size != asset["byte_size"]:
            raise ValueError(f"Canonical asset checksum mismatch: {asset['relative_path']}")
        parquet = pq.ParquetFile(path)
        if parquet.metadata.num_rows != asset["row_count"]:
            raise ValueError("Parquet metadata row count mismatch")
        parquet.close()
    raw_manifest = {f["path"]: f["expected_sha256"] for f in inv["files"]}
    validated = {f["path"]: f["sha256"] for f in integrity["files"]}
    if raw_manifest != validated:
        raise ValueError("Source files have not all passed their manifest hashes")
    # Exercise the exact storage-index pattern a future depth-range API will use.
    log = next(e for e in meta["logs"] if e["source_log_name"] == "Min1 sTSAS")
    asset = next(a for a in storage["assets"] if a.get("source_log_id") == log["source_log_id"])
    with np.load(folder / "4_sample_axis.npz", allow_pickle=False) as data:
        depth = data["md_m"]
    center = float(depth[len(depth) // 2])
    lo, hi = center - 0.25, center + 0.25
    groups = [g["row_group"] for g in asset["row_groups"] if g["coordinate_max"] >= lo and g["coordinate_min"] <= hi]
    range_groups = groups.copy()
    parquet = pq.ParquetFile(folder / asset["relative_path"])
    table = parquet.read_row_groups(groups)
    table = table.filter(pc.and_(pc.greater_equal(table["depth_m"], lo), pc.less_equal(table["depth_m"], hi)))
    actual = [(r["source_row"], r["value_raw"]) for r in table.select(["source_row", "value_raw"]).to_pylist()]
    expected = [(i, row[2]) for i, row in scalar_rows(hole, log["source_manifest"]) if lo <= float(row[0]) <= hi]
    if actual != expected:
        raise ValueError("Indexed depth-range query differs from original CSV records")
    repeated_indices = np.flatnonzero(np.diff(depth) == 0)
    duplicate_check = None
    if repeated_indices.size:
        value = float(depth[int(repeated_indices[0])])
        groups = [g["row_group"] for g in asset["row_groups"] if g["coordinate_min"] <= value <= g["coordinate_max"]]
        duplicate_rows = parquet.read_row_groups(groups, columns=["sample_no", "depth_m"])
        duplicate_rows = duplicate_rows.filter(pc.equal(duplicate_rows["depth_m"], value))
        actual_ids = duplicate_rows["sample_no"].to_pylist()
        expected_ids = np.flatnonzero(depth == value).tolist()
        if actual_ids != expected_ids:
            raise ValueError("Exact depth query lost duplicate-depth samples")
        duplicate_check = {"depth_m": value, "returned_sample_count": len(actual_ids), "sample_ids": actual_ids}
    parquet.close()
    issue_counts = Counter(i["code"] for report in reports for i in report.get("issues", []))
    normalized_log_count = len(meta["logs"])
    expected_log_count = sum(len(meta["raw_metadata"][name]) for name in (
        "logs_scalar.json", "logs_spectral.json", "logs_profilometer.json", "logs_image.json"))
    if normalized_log_count != expected_log_count:
        raise ValueError("Source log metadata was lost")
    return {"hole_id": hole, "directory": folder.relative_to(OUTPUT).as_posix(),
            "source_snapshot_id": inv["source_snapshot_id"], "pipeline_id": inv["pipeline_id"],
            "status": "passed_with_known_limitations", "source_file_count": inv["file_count"],
            "source_bytes": inv["source_bytes"], "sample_count": alignment["sample_count"],
            "scalar_log_count": integrity["counts"]["scalar_logs"],
            "scalar_data_rows": storage["scalar_data_rows"], "metadata_log_count": normalized_log_count,
            "spectral_array_count": integrity["counts"]["spectral_arrays"],
            "thumbnail_count": integrity["counts"]["thumbnails"],
            "profile_count": integrity["counts"]["profiles"],
            "canonical_file_count": storage["canonical_file_count"], "canonical_bytes": storage["canonical_bytes"],
            "duplicate_depth_samples_preserved": alignment["duplicate_depth_samples_preserved"],
            "depth_range_query": {"source_log_id": log["source_log_id"], "from_m": lo, "to_m": hi,
                                  "row_groups_read": range_groups, "matched_rows": len(actual), "matches_original_csv": True},
            "duplicate_depth_query": duplicate_check, "issue_counts": dict(issue_counts),
            "report_sha256": {name: sha_file(folder / name) for name in report_files}}


def main():
    args = arguments("step 6 verify prepared data")
    holes = []
    for hole in args.holes:
        print(f"{hole}: verify report chain, asset hashes and indexed queries", flush=True)
        holes.append(verify_hole(hole))
    keys = ["source_file_count", "source_bytes", "sample_count", "scalar_log_count", "scalar_data_rows",
            "metadata_log_count", "spectral_array_count", "thumbnail_count", "profile_count",
            "canonical_file_count", "canonical_bytes", "duplicate_depth_samples_preserved"]
    totals = {key: sum(h[key] for h in holes) for key in keys}
    all_five = set(args.holes) == set(ALLOWED)
    if all_five:
        expected = read_json(BASELINE)["totals"]
        for observed, name in (("source_bytes", "source_bytes"), ("sample_count", "sample_count"),
                               ("scalar_log_count", "scalar_log_count"), ("scalar_data_rows", "scalar_data_row_count_from_earlier_audit"),
                               ("spectral_array_count", "spectral_array_count"), ("thumbnail_count", "thumbnail_count")):
            if totals[observed] != expected[name]:
                raise ValueError(f"Five-hole baseline reconciliation failed: {observed}")
    result = {"status": "passed_with_known_limitations", "active_holes": args.holes, "all_five_verified": all_five,
              "pipeline_id": pipeline_id(), "verification_code_sha256": sha_file(Path(__file__)),
              "totals": totals, "holes": holes,
              "limitations": ["F32 sample/wavelength order unconfirmed; no per-sample spectra published",
                              "Physical profile calibration and borehole trajectory unavailable",
                              "Source domain references remain unresolved",
                              "No confidence score computed", "Database import and cloud upload not started"],
              "source_check_scope": "Full raw hashes checked in step 2; selective raw rereads hashed in steps 4/5/6; source size and mtime checked at step entry"}
    label = "all_five" if all_five else "_".join(args.holes)
    root = OUTPUT / "verification" / pipeline_id()[:16]
    write_json(root / f"6_{label}.json", result)
    lines = ["# 五孔处理验证结果" if all_five else "# 处理验证结果", "", "状态：已通过已实现处理范围内的校验，仍有明确的能力限制。", "",
             "| 钻孔 | 样本 | 标量行 | Parquet 文件 | Parquet 字节 | 保留的重复孔深样本 |", "|---|---:|---:|---:|---:|---:|"]
    lines += [f"| {h['hole_id']} | {h['sample_count']:,} | {h['scalar_data_rows']:,} | {h['canonical_file_count']} | {h['canonical_bytes']:,} | {h['duplicate_depth_samples_preserved']:,} |" for h in holes]
    lines += ["", f"合计 {totals['source_file_count']} 个来源文件、{totals['scalar_log_count']} 条标量日志、{totals['scalar_data_rows']:,} 行标量数据。",
              "", "规范载荷每批值与空值掩码已经过回读校验，本步骤另行核对文件哈希、版本依赖及按孔深范围读取。重复孔深查询保留多个样本。", "",
              "仍未实现：已确认排列顺序的逐样本光谱、真实三维轨迹、标定后的岩心表面、可信度评分、数据库导入和云端上传。"]
    write_text(root / f"6_{label}.md", "\n".join(lines) + "\n")
    if all_five:
        write_json(OUTPUT / "latest_verified.json", {
            "pipeline_id": result["pipeline_id"],
            "summary_json": (root / f"6_{label}.json").relative_to(OUTPUT).as_posix(),
            "summary_markdown": (root / f"6_{label}.md").relative_to(OUTPUT).as_posix(),
            "summary_sha256": sha_file(root / f"6_{label}.json"),
            "purpose": "verified local prepared-data pointer; not a database/cloud publication",
        })
    print(f"Verified {len(holes)} holes: {totals['scalar_data_rows']:,} scalar rows, {totals['canonical_file_count']} Parquet files", flush=True)
    print(str(root / f"6_{label}.md"), flush=True)


if __name__ == "__main__":
    fail_main(main)

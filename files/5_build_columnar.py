"""Step 5: create loss-preserving Parquet payloads and verify every output batch."""
import csv
import hashlib
import os
import struct
import time

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from _etl_common import (
    HashedLines, environment, fail_main, issue, parse_json, read_json, report_base, require_report,
    run_holes, scalar_rows, sha_file, snapshot, source_path, write_json, write_text,
)

BATCH_ROWS = 4096


def logical_digest(table, source_records=None):
    """Hash typed values independently of Parquet compression or dictionary encoding."""
    h = hashlib.sha256()
    h.update(struct.pack("<q", len(source_records) if source_records is not None else table.num_rows))
    for field, column in zip(table.schema, table.columns):
        # Materialize Python values before hashing. Avoid Arrow fill_null kernels:
        # this Windows runtime reproduced heap corruption in fill_null on mixed
        # numeric/all-null batches. No scientific values are imputed here.
        values = ([row.get(field.name) for row in source_records]
                  if source_records is not None else column.to_pylist())
        h.update(field.name.encode("utf-8"))
        h.update(bytes(value is None for value in values))
        if pa.types.is_fixed_size_list(field.type):
            h.update(struct.pack("<q", field.type.list_size))
            h.update(np.asarray(values, dtype="<f8").tobytes())
        elif pa.types.is_integer(field.type) or pa.types.is_floating(field.type):
            kind = "<i8" if pa.types.is_integer(field.type) else "<f8"
            h.update(np.asarray([0 if v is None else v for v in values], dtype=kind).tobytes())
        elif pa.types.is_string(field.type):
            for value in values:
                if value is None:
                    h.update(struct.pack("<i", -1))
                else:
                    data = value.encode("utf-8")
                    h.update(struct.pack("<i", len(data)))
                    h.update(data)
        else:
            raise ValueError(f"Unsupported verification type: {field.type}")
    return h.hexdigest()


def write_verified_parquet(path, schema, records, coordinate_name=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    expected, groups, batch, total = [], [], [], 0
    try:
        with pq.ParquetWriter(temporary, schema, compression="zstd", use_dictionary=True) as writer:
            def flush():
                nonlocal total
                table = pa.Table.from_pylist(batch, schema=schema)
                expected.append(logical_digest(table, batch))
                coordinates = [r[coordinate_name] for r in batch if r.get(coordinate_name) is not None] if coordinate_name else []
                groups.append({"row_group": len(groups), "source_row_from": total,
                               "source_row_to_exclusive": total + len(batch), "row_count": len(batch),
                               "coordinate_min": min(coordinates) if coordinates else None,
                               "coordinate_max": max(coordinates) if coordinates else None})
                writer.write_table(table, row_group_size=BATCH_ROWS)
                total += len(batch)
                batch.clear()
            for record in records:
                batch.append(record)
                if len(batch) == BATCH_ROWS:
                    flush()
            if batch:
                flush()
        parquet = pq.ParquetFile(temporary)
        try:
            actual = [logical_digest(pa.Table.from_batches([b], schema=schema))
                      for b in parquet.iter_batches(batch_size=BATCH_ROWS)]
            rows_read, groups_read = parquet.metadata.num_rows, parquet.metadata.num_row_groups
        finally:
            parquet.close(force=True)
        if actual != expected or rows_read != total:
            raise ValueError(f"Typed round-trip verification failed: {path.name}")
        if groups_read != len(groups):
            raise ValueError("Parquet row-group index mismatch")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()
    return {"row_count": total, "row_groups": groups, "sha256": sha_file(path),
            "byte_size": path.stat().st_size, "logical_group_sha256": expected,
            "round_trip_verification": "all typed values and null masks in every batch"}


def scalar_records(hole, manifest, interval, numeric):
    for index, row in scalar_rows(hole, manifest):
        token = row[2]
        missing = token in ("", "null")
        a, b = float(row[0]), float(row[1])
        if interval:
            if not a.is_integer() or not b.is_integer():
                raise ValueError("Noninteger sample-index interval")
        elif a != b:
            raise ValueError("Expected a point scalar")
        yield {"source_row": index, "sample_no": None if interval else index,
               "depth_m": None if interval else a,
               "sample_no_from": int(a) if interval else None,
               "sample_no_to": int(b) if interval else None,
               "value_numeric": float(token) if numeric and not missing else None,
               "value_text": token if not numeric and not missing else None, "value_raw": token}


def profile_records(hole, manifest):
    with source_path(hole, manifest["file"]).open("rb") as stream:
        lines = HashedLines(stream)
        for index, line in enumerate(lines):
            record = parse_json(line)
            if record["sampleNo"] != index:
                raise ValueError("Profile sample sequence changed")
            yield {"sample_no": record["sampleNo"], "values": record["floatprofdata"]}
        if lines.sha256.hexdigest() != manifest["sha256"]:
            raise ValueError("Profile source hash changed")


def build_hole(hole):
    folder, inv = snapshot(hole)
    integrity = require_report(folder, "2_integrity.json")
    metadata_report = require_report(folder, "3_metadata_report.json")
    alignment = require_report(folder, "4_alignment_report.json")
    if sha_file(folder / "3_normalized_metadata.json") != metadata_report["normalized_metadata_sha256"]:
        raise ValueError("Normalized metadata was modified")
    for name, expected in alignment["artifacts"].items():
        if sha_file(folder / name) != expected:
            raise ValueError(f"Alignment artifact was modified: {name}")
    meta = read_json(folder / "3_normalized_metadata.json")
    canonical = folder / "5_canonical"
    assets, last_progress, scalar_rows_written = [], time.monotonic(), 0
    for index, log in enumerate(meta["logs"]):
        if log["log_kind"] not in ("scalar", "profile"):
            continue
        manifest = log["source_manifest"]
        header = {"source_path": manifest["file"], "source_sha256": manifest["sha256"],
                  "source_log_id": log["source_log_id"], "dataset_revision_id": meta["dataset_revision"]["id"],
                  "axis_id": alignment["axis_id"], "origin": log["origin"],
                  "unit": log.get("unit"), "batch_rows": BATCH_ROWS}
        if log["log_kind"] == "scalar":
            interval = log["coordinate_kind"] == "sample_index_closed_interval"
            header["coordinate_kind"] = log["coordinate_kind"]
            if interval:
                header["interval_bounds"] = "inclusive sample indices"
            schema = pa.schema([
                ("source_row", pa.int64()), ("sample_no", pa.int64()), ("depth_m", pa.float64()),
                ("sample_no_from", pa.int64()), ("sample_no_to", pa.int64()),
                ("value_numeric", pa.float64()), ("value_text", pa.string()), ("value_raw", pa.string()),
            ])
            records = scalar_records(hole, manifest, interval, log["observed_value_kind"] == "numeric")
            relative = f"scalars/{log['source_log_id']}.parquet"
            coordinate = "sample_no_from" if interval else "depth_m"
        else:
            header.update({"physical_unit": None, "physical_calibration_status": "unconfirmed",
                           "observed_channel_count": log["observed_channel_count"]})
            schema = pa.schema([("sample_no", pa.int64()),
                                ("values", pa.list_(pa.float64(), log["observed_channel_count"]))])
            records = profile_records(hole, manifest)
            relative, coordinate = f"profiles/{log['source_log_id']}.parquet", "sample_no"
        from _etl_common import json_bytes
        schema = schema.with_metadata({b"etl4": json_bytes(header)})
        result = write_verified_parquet(canonical / relative, schema, records, coordinate)
        if log["log_kind"] == "scalar":
            scalar_rows_written += result["row_count"]
        assets.append({"kind": log["log_kind"], "source_log_id": log["source_log_id"],
                       "relative_path": "5_canonical/" + relative, **result, "provenance": header})
        if time.monotonic() - last_progress >= 10:
            print(f"  {hole}: wrote and verified {len(assets)} log files", flush=True)
            last_progress = time.monotonic()
    if scalar_rows_written != integrity["counts"]["scalar_data_rows"]:
        raise ValueError("Canonical scalar row count differs from source integrity report")
    with np.load(folder / "4_sample_axis.npz", allow_pickle=False) as axis:
        schema = pa.schema([(k, pa.float64() if k in ("md_m", "section_distance_mm") else pa.int64()) for k in axis.files])
        # Load each NPZ member once; indexing an NpzFile repeatedly would decompress per row.
        arrays = {k: axis[k] for k in axis.files}
        def axis_records():
            for index in range(alignment["sample_count"]):
                row = {k: values[index].item() for k, values in arrays.items()}
                row["section_distance_mm"] = None if not np.isfinite(row["section_distance_mm"]) else row["section_distance_mm"]
                yield row
        result = write_verified_parquet(canonical / "sample_axis.parquet", schema, axis_records(), "md_m")
        assets.append({"kind": "sample_axis", "relative_path": "5_canonical/sample_axis.parquet", **result})
    raw_assets = [{"source_relative_path": f["path"], "sha256": f["expected_sha256"],
                   "byte_size": f["size_bytes"], "kind": f["kind"],
                   "proposed_object_key": f"raw/{hole}/{inv['source_snapshot_id']}/{f['path']}",
                   "upload_status": "not_uploaded"} for f in inv["files"]]
    issues = [issue("SPECTRAL_SAMPLE_BINDING_PENDING", hole,
                    "Original F32 assets preserved in the raw asset index; no matrix order assumed and no per-sample spectrum published.")]
    report = report_base(inv, 5, folder / "4_alignment_report.json")
    report.update({"status": "passed_with_issues", "environment": environment(),
                   "scalar_data_rows": scalar_rows_written,
                   "canonical_file_count": len(assets), "canonical_bytes": sum(a["byte_size"] for a in assets),
                   "assets": assets, "raw_assets": raw_assets, "issues": issues,
                   "database_import_status": "not_started", "cloud_upload_status": "not_started",
                   "ready_capabilities": ["metadata", "scalar_ranges", "sample_axis", "tray_thumbnails", "profile_arrays_with_unknown_physical_units"],
                   "unavailable_capabilities": ["verified_per_sample_spectra", "true_3d_trajectory", "calibrated_core_surface"]})
    write_json(folder / "5_storage_report.json", report)
    write_text(folder / "5_storage_report.md", f"# {hole} 规范载荷\n\n"
               f"生成并全量回读验证 {len(assets)} 个 Parquet 文件，{report['canonical_bytes']:,} 字节；"
               f"标量 {scalar_rows_written:,} 行全部保留。\n\n"
               "数值、类别、空值掩码、原始标量文本和样本顺序完成逐批校验。"
               "源 F32、图片及元数据保留原件引用与哈希，没有重复下载或云端上传。\n\n"
               "光谱矩阵顺序仍未确认；没有发布未经确认的样本光谱，也未部署数据库。\n")
    print(f"  {len(assets)} Parquet files; {report['canonical_bytes']:,} bytes; all batches verified", flush=True)


if __name__ == "__main__":
    fail_main(lambda: run_holes("step 5 build columnar payloads", build_hole))

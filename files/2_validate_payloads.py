"""Step 2: hash and structurally validate raw payloads in streaming passes."""
import csv
import hashlib
import io
import math
import struct
import time
from collections import Counter

import numpy as np
from PIL import Image

from _etl_common import (
    HashedLines, baseline_for, fail_main, issue, parse_json, report_base,
    run_holes, sha_file, snapshot, source_json, source_path, write_json, write_text,
)


def validate_scalar(path, source_type):
    rows = nulls = equal = decreases = duplicate_adjacent = non_finite = 0
    low = high = previous = value_low = value_high = None
    start_hash, end_hash = hashlib.sha256(), hashlib.sha256()
    categories, null_tokens = Counter(), Counter()
    numeric = str(source_type) in ("2", "6")
    with path.open("rb") as stream:
        lines = HashedLines(stream)
        reader = csv.reader(lines, strict=True)
        header = next(reader)
        if len(header) != 3 or header[:2] != ["StartDepth", "EndDepth"]:
            raise ValueError(f"Unexpected scalar header: {header}")
        for row in reader:
            if len(row) != 3:
                raise ValueError(f"Expected 3 fields at data row {rows}")
            a, b = float(row[0]), float(row[1])
            if not math.isfinite(a) or not math.isfinite(b) or a > b:
                raise ValueError(f"Invalid coordinates at data row {rows}")
            start_hash.update(struct.pack("<d", a))
            end_hash.update(struct.pack("<d", b))
            equal += a == b
            if previous is not None:
                decreases += a < previous
                duplicate_adjacent += a == previous
            previous, low, high = a, a if low is None else min(low, a), b if high is None else max(high, b)
            token = row[2]
            if token in ("", "null"):
                nulls += 1
                null_tokens[token] += 1
            elif numeric:
                value = float(token)
                if math.isfinite(value):
                    value_low = value if value_low is None else min(value_low, value)
                    value_high = value if value_high is None else max(value_high, value)
                else:
                    non_finite += 1
            else:
                categories[token] += 1
            rows += 1
    return {"sha256": lines.sha256.hexdigest(), "columns": header, "row_count": rows,
            "observed_value_kind": "numeric" if numeric else "text",
            "null_count": nulls, "null_tokens": dict(null_tokens),
            "non_finite_value_count": non_finite,
            "all_coordinates_are_points": equal == rows,
            "coordinate_min": low, "coordinate_max": high,
            "decreasing_start_count": decreases, "adjacent_equal_start_count": duplicate_adjacent,
            "start_vector_sha256": start_hash.hexdigest(), "end_vector_sha256": end_hash.hexdigest(),
            "value_min": value_low, "value_max": value_high,
            "category_count": len(categories), "category_counts": dict(categories)}


def validate_spectrum(path, entry, log):
    if entry["dtype"] != "float32" or entry["byte_order"] != "little":
        raise ValueError("Unsupported spectrum dtype/byte order; explicit support required")
    n, bands = int(entry["sample_count"]), int(entry["band_count"])
    if path.stat().st_size != n * bands * 4 or len(log["wavelengths"]) != bands:
        raise ValueError("Spectral shape/byte/wavelength counts disagree")
    waves = np.asarray(log["wavelengths"], dtype=np.float64)
    if not np.isfinite(waves).all() or not (np.diff(waves) > 0).all():
        raise ValueError("Wavelength axis is not finite and strictly increasing")
    h, low, high, non_finite, below, above = hashlib.sha256(), None, None, 0, 0, 0
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            h.update(chunk)
            values = np.frombuffer(chunk, dtype="<f4")
            good = np.isfinite(values)
            non_finite += int((~good).sum())
            finite = values[good]
            if finite.size:
                lo, hi = float(finite.min()), float(finite.max())
                low, high = lo if low is None else min(lo, low), hi if high is None else max(hi, high)
                below += int((finite < 0).sum())
                above += int((finite > 1).sum())
    return {"sha256": h.hexdigest(), "sample_count": n, "wavelength_count": bands,
            "dtype": "float32", "byte_order": "little", "value_min": low, "value_max": high,
            "non_finite_value_count": non_finite, "values_below_zero": below, "values_above_one": above,
            "wavelength_min": float(waves[0]), "wavelength_max": float(waves[-1]),
            "matrix_order_status": "unconfirmed", "per_sample_spectrum_publication_allowed": False}


def validate_profile(path, entry):
    rows, channels, sample_errors = 0, Counter(), 0
    low = high = None
    non_finite = zero_count = 0
    profile_min = []
    with path.open("rb") as stream:
        lines = HashedLines(stream)
        for line in lines:
            record = parse_json(line)
            if set(record) != {"sampleNo", "floatprofdata"}:
                raise ValueError("Unexpected profile record fields; preserve and review before parsing")
            if type(record["sampleNo"]) is not int or record["sampleNo"] != rows:
                sample_errors += 1
            values = np.asarray(record["floatprofdata"], dtype=np.float64)
            if values.ndim != 1 or not values.size:
                raise ValueError("Profile must be a nonempty one-dimensional numeric array")
            channels[len(values)] += 1
            finite = values[np.isfinite(values)]
            non_finite += len(values) - len(finite)
            if finite.size:
                lo, hi = float(finite.min()), float(finite.max())
                low, high = lo if low is None else min(low, lo), hi if high is None else max(high, hi)
            zero_count += int((values == 0).sum())
            positive = values[values > 0]
            profile_min.append(float(positive.min()) if positive.size else 0.0)
            rows += 1
    if rows != entry["sample_count"] or rows != entry["records"] or sample_errors:
        raise ValueError("Profile record count or sample numbering is inconsistent")
    if len(channels) != 1 or non_finite:
        raise ValueError("Profile shape varies or contains non-finite values")
    return {"sha256": lines.sha256.hexdigest(), "row_count": rows,
            "declared_channel_count": entry["channel_count"],
            "observed_channel_count": next(iter(channels)), "channel_counts": dict(channels),
            "sample_number_errors": sample_errors, "non_finite_value_count": non_finite,
            "zero_value_count": zero_count, "value_min": low, "value_max": high}, profile_min


def validate_hole(hole):
    folder, inv = snapshot(hole)
    scalar_meta = {x["log_id"]: x for x in source_json(hole, "logs_scalar.json")}
    spectral_meta = {x["log_id"]: x for x in source_json(hole, "logs_spectral.json")}
    results, issues, errors = [], [], []
    last_progress = time.monotonic()
    for index, file in enumerate(inv["files"]):
        path = source_path(hole, file["path"])
        result = {"path": file["path"], "kind": file["kind"], "size_bytes": file["size_bytes"]}
        ref = file["manifest_reference"]
        entry = ref["entry"] if ref else None
        try:
            if file["kind"] == "scalar":
                result.update(validate_scalar(path, scalar_meta[entry["log_id"]]["log_type"]))
            elif file["kind"] == "spectral":
                result.update(validate_spectrum(path, entry, spectral_meta[entry["log_id"]]))
                issues.append(issue("SPECTRAL_MATRIX_ORDER_UNCONFIRMED", entry["log_id"],
                                    "Shape and bytes pass, but sample/wavelength ordering is not source-declared."))
            elif file["kind"] == "profile":
                profile_result, profile_min = validate_profile(path, entry)
                result.update(profile_result)
                result["positive_min_cache"] = f"2_profile_min_{entry['log_id']}.json"
                write_json(folder / result["positive_min_cache"], profile_min)
                result["positive_min_cache_sha256"] = sha_file(folder / result["positive_min_cache"])
                if result["declared_channel_count"] != result["observed_channel_count"]:
                    issues.append(issue("PROFILE_CHANNEL_COUNT_CONFLICT", entry["log_id"],
                                        "Preserve both declaration and full-file observation.", evidence={
                                            "declared": result["declared_channel_count"],
                                            "observed": result["observed_channel_count"]}))
            else:
                data = path.read_bytes()
                result["sha256"] = hashlib.sha256(data).hexdigest()
                if path.suffix.lower() == ".jpg":
                    with Image.open(io.BytesIO(data)) as img:
                        img.verify()
                    with Image.open(io.BytesIO(data)) as img:
                        img.load()
                        result.update({"width_px": img.width, "height_px": img.height,
                                       "image_format": img.format, "image_mode": img.mode})
                elif path.suffix == ".json":
                    parse_json(data.decode("utf-8-sig"))
                else:
                    data.decode("utf-8-sig")
            if result["sha256"] != file["expected_sha256"]:
                raise ValueError("SHA256 differs from the source manifest/inventory")
            if result.get("non_finite_value_count", 0):
                raise ValueError("Non-finite data needs an explicit handling rule")
            result["status"] = "passed"
        except Exception as exc:
            result.update({"status": "failed", "error": f"{type(exc).__name__}: {exc}"})
            errors.append(issue("PAYLOAD_INTEGRITY_FAILURE", file["path"], str(exc), "error"))
        results.append(result)
        if time.monotonic() - last_progress >= 10:
            print(f"  {hole}: checked {index + 1}/{len(inv['files'])} files", flush=True)
            last_progress = time.monotonic()
    base = baseline_for(hole)
    counts = {"scalar_logs": sum(r["kind"] == "scalar" for r in results),
              "scalar_data_rows": sum(r.get("row_count", 0) for r in results if r["kind"] == "scalar"),
              "spectral_arrays": sum(r["kind"] == "spectral" for r in results),
              "profiles": sum(r["kind"] == "profile" for r in results),
              "thumbnails": sum("width_px" in r for r in results),
              "files_checked": len(results), "file_errors": len(errors)}
    for key, baseline_key in (("scalar_logs", "scalar_log_count"), ("spectral_arrays", "spectral_array_count"),
                              ("thumbnails", "thumbnail_count")):
        if counts[key] != base[baseline_key]:
            errors.append(issue("BASELINE_COUNT_MISMATCH", key, f"Observed {counts[key]}", "error"))
    report = report_base(inv, 2, folder / "1_inventory.json")
    report.update({"status": "failed" if errors else "passed_with_issues" if issues else "passed",
                   "counts": counts, "issues": issues + errors, "files": results,
                   "validation_scope": "physical integrity and structure, not independently validated mineral accuracy"})
    write_json(folder / "2_integrity.json", report)
    write_text(folder / "2_integrity.md", f"# {hole} 文件完整性检查\n\n状态：{report['status']}。\n\n"
               f"检查 {counts['files_checked']} 个文件；标量 {counts['scalar_data_rows']:,} 行；错误 {len(errors)} 项。\n\n"
               + "\n".join(f"- {i['code']}：{i['scope']}。{i['detail']}" for i in report["issues"]) + "\n")
    print(f"  {report['status']}; {counts['scalar_data_rows']:,} scalar rows; {len(errors)} errors", flush=True)
    if errors:
        raise ValueError("Integrity failures recorded; dependent steps must not run")


if __name__ == "__main__":
    fail_main(lambda: run_holes("step 2 integrity", validate_hole))

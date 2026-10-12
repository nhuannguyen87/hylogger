"""Suggest adapters from selected source evidence; this is not processing acceptance."""

import csv
import io
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re

import numpy as np
from dataset_inputs import selection_plan


TEXT_LOGS = {
    "8aa08235-f728-419f-b578-ca9e146fe5e", "4d1f0ec0-76a2-4a81-88b1-fa6b12bbd45",
    "af32891f-c043-41d8-8c08-5648fba52e8", "357bc113-7070-4b2b-b4a3-04679c9a885",
    "e2abbc70-f418-4482-8caf-45503ff2895", "da0ae1d6-0450-4dbc-989d-da1cea577c3",
}
REVIEWED_RESETS = {"6a23f66a-00ff-44ae-9d3d-662a1c2a653": [6030],
                   "340f4c72-49fb-43cd-8a30-c6efbedeff6": [27800]}


def document(folder, name):
    return json.loads((folder / name).read_text(encoding="utf-8-sig"))


def checked_rows(folder, entry):
    path = (folder / entry["file"]).resolve()
    if not path.is_relative_to(folder) or path == folder:
        raise ValueError("Source path escapes the hole folder")
    data = path.read_bytes()
    if len(data) != entry["nbytes"] or hashlib.sha256(data).hexdigest() != entry["sha256"]:
        raise ValueError("Source file differs from manifest: " + entry["file"])
    rows = list(csv.reader(io.StringIO(data.decode("utf-8-sig")), strict=True))
    if not rows or rows[0][:2] != ["StartDepth", "EndDepth"] or any(len(row) != 3 for row in rows):
        raise ValueError("Invalid three-column CSV: " + entry["file"])
    return rows[1:]


def expand(rows, n):
    index = np.full(n, -1, dtype=np.int32)
    labels = np.empty(n, dtype=object)
    starts, ends, next_start = [], [], 0
    for ordinal, (a, b, label) in enumerate(rows):
        if not float(a).is_integer() or not float(b).is_integer():
            raise ValueError("Noninteger source interval")
        a, b = int(float(a)), int(float(b))
        if a != next_start or b < a or b >= n:
            raise ValueError("Invalid source interval coverage")
        index[a:b+1], labels[a:b+1] = ordinal, label
        starts.append(a)
        ends.append(b)
        next_start = b + 1
    if next_start != n:
        raise ValueError("Incomplete source intervals")
    return index, labels, np.asarray(starts), np.asarray(ends)


def inspect(folder, failure=None):
    datasets = document(folder, "datasets.json")
    result = {"hole": folder.name, "dataset_ids": [d["dataset_id"] for d in datasets],
              "suggested_cases": [], "evidence": {}, "unresolved": []}
    cases, evidence = result["suggested_cases"], result["evidence"]
    plan = selection_plan(folder.name, datasets)
    if plan is not None:
        result["route"] = "selected_inputs"
        cases.append(plan["case"])
        evidence[plan["case"]] = plan
        return result
    if not re.fullmatch(r"[A-Za-z0-9_-]+", folder.name) or (folder / "source_identity.json").exists():
        cases.append("01")
        evidence["01"] = "Directory syntax or source_identity.json is rejected by the baseline entry."
    if len(datasets) != 1:
        result["route"] = "multi"
        manifest = document(folder, "scalar_manifest.json")
        by_id = {entry["log_id"]: entry for entry in manifest}
        counts = []
        for dataset in datasets:
            trays = checked_rows(folder, by_id[dataset["tray_id"]])
            end = float(trays[-1][1])
            if not end.is_integer():
                raise ValueError("Noninteger final sample index")
            counts.append(int(end) + 1)
        message = (failure or {}).get("error", "")
        if len(counts) != len(set(counts)) or "SpectralLogCollection" in message or "Profile membership ambiguous" in message:
            cases.append("11")
            evidence["11"] = {"sample_counts": counts, "requires_official_membership": True,
                               "note": "Special processing must establish unique full-value profile membership."}
        else:
            result["unresolved"].append("Multi-dataset failure needs a source-membership diagnosis")
        result["suggested_cases"] = sorted(set(cases))
        return result
    result["route"] = "single_dataset"
    dataset = datasets[0]
    manifest = document(folder, "scalar_manifest.json")
    by_id = {entry["log_id"]: entry for entry in manifest}
    trays = checked_rows(folder, by_id[dataset["tray_id"]])
    sections = checked_rows(folder, by_id[dataset["section_id"]])
    n = int(float(trays[-1][1])) + 1
    if n <= 0 or not float(trays[-1][1]).is_integer():
        raise ValueError("Invalid final tray sample index")
    ti, _, ts, te = expand(trays, n)
    si, global_labels, ss, se = expand(sections, n)
    if np.any(ti[ss] != ti[se]):
        raise ValueError("Section crosses tray boundary")
    local = np.empty(n)
    counts = {}
    for a, b in zip(ss, se):
        parent = int(ti[a])
        counts[parent] = counts.get(parent, 0) + 1
        local[a:b+1] = counts[parent]
    for name, expected in (("TraySamp", np.arange(n)-ts[ti]+1), ("SecSamp", np.arange(n)-ss[si]+1)):
        entries = [e for e in manifest if e["log_name"] == name]
        if not entries:
            raise ValueError("Missing position log: " + name)
        for entry in entries:
            values = np.asarray([float(row[2]) for row in checked_rows(folder, entry)])
            if not np.array_equal(values, expected):
                raise ValueError("Position evidence mismatch: " + name)
    for entry in manifest:
        if entry["log_name"] == "virtual_section":
            global_labels = global_labels.astype(np.float64)
            rows = checked_rows(folder, entry)
            values = np.asarray([float("nan") if row[2] in ("", "null") else float(row[2]) for row in rows])
            if np.array_equal(values, global_labels):
                continue
            if np.array_equal(values, local):
                cases.append("02")
                evidence["02"] = {"samples_compared": n, "tray_local_mismatches": 0}
            else:
                present = np.isfinite(values)
                if len(values) == n and present.any() and np.isnan(values[~present]).all() and np.array_equal(values[present], global_labels[present]):
                    cases.append("05")
                    evidence["05"] = {"section_null_count": int((~present).sum()), "nonnull_mismatches": 0}
                else:
                    result["unresolved"].append("Unrecognized virtual_section numbering")
        if entry["log_id"] in TEXT_LOGS:
            try:
                checked_rows(folder, entry)
            except (csv.Error, ValueError) as error:
                # Hash errors are not text parsing exceptions.
                if "differs from manifest" in str(error) or "escapes" in str(error):
                    raise
                cases.append("08")
                evidence.setdefault("08", []).append({"log_id": entry["log_id"], "baseline_error": str(error)})
        if entry["log_name"] == "prof_min":
            rows = checked_rows(folder, entry)
            values = np.asarray([float("nan") if r[2] in ("", "null") else float(r[2]) for r in rows])
            if len(values) != n or np.isinf(values).any() or not np.isfinite(values).any():
                result["unresolved"].append("Invalid profile reference axis")
            elif np.isnan(values).any():
                cases.append("05")
                evidence.setdefault("05", {})["profile_null_count"] = int(np.isnan(values).sum())
    for name, role in (("Tray", "tray_id"), ("Section", "section_id")):
        aliases = [entry for entry in manifest if entry["log_name"] == name]
        if len(aliases) > 1:
            for entry in aliases:
                checked_rows(folder, entry)
            if all(e["sha256"] == by_id[dataset[role]]["sha256"] for e in aliases):
                cases.append("03")
                evidence.setdefault("03", {})[name] = len(aliases)
            else:
                result["unresolved"].append("Different interval logs share the same name")
    image = document(folder, "images_manifest.json")
    logs = [e for e in image["logs"] if e["log_name"] == "Tray Thumbnail Images"]
    if len(logs) > 1:
        declared = document(folder, "tray_depths.json")
        signatures = []
        for log in logs:
            files = sorted((f for f in image["files"] if f["log_id"] == log["log_id"]), key=lambda f: int(f["sample_no"]))
            depths = {int(row["sample_no"]): row for row in declared[log["log_id"]]}
            if (len(depths) != len(declared[log["log_id"]]) or set(depths) != set(range(len(trays)))
                    or [int(f["sample_no"]) for f in files] != list(range(len(trays)))):
                raise ValueError("Incomplete or duplicate image ordinals")
            signature = []
            for file in files:
                path = (folder / file["file"]).resolve()
                if not path.is_relative_to(folder) or file["dataset_id"] != dataset["dataset_id"]:
                    raise ValueError("Invalid image path or dataset association")
                with path.open("rb") as stream:
                    checksum = hashlib.file_digest(stream, "sha256").hexdigest()
                if checksum != file["sha256"] or path.stat().st_size != file["nbytes"]:
                    raise ValueError("Image content differs from manifest")
                depth_row = depths[int(file["sample_no"])]
                signature.append((file["dataset_id"], int(file["sample_no"]), file["depth_from"], file["depth_to"],
                                  depth_row["start_value"], depth_row["end_value"], checksum))
            signatures.append(signature)
        if any(row != signatures[0] for row in signatures[1:]):
            result["unresolved"].append("Thumbnail logs differ in content or associations")
        else:
            cases.append("04")
            evidence["04"] = {"identical_logs": len(logs), "images_per_log": len(trays)}
    anchor = next(e for e in manifest if e["log_name"] == "TraySamp")
    depth = np.asarray([float(row[0]) for row in checked_rows(folder, anchor)])
    float_cases = 0
    for item in image["files"]:
        if item["log_id"] not in {x["log_id"] for x in logs}:
            continue
        ordinal = int(item["sample_no"])
        pairs = [(float(item["depth_from"]), depth[ts[ordinal]]), (float(item["depth_to"]), depth[te[ordinal]])]
        if all(abs(Decimal(str(a))-Decimal(str(b))) <= Decimal("0.00001") for a, b in pairs):
            continue
        if all(np.isfinite(np.float32(a)) and np.float32(a).tobytes() == np.float32(b).tobytes() for a, b in pairs):
            float_cases += 1
        else:
            result["unresolved"].append("Image bounds differ beyond exact float32 equivalence")
    if float_cases:
        cases.append("06")
        evidence["06"] = {"float32_equivalent_image_bounds": float_cases}
    resets = (np.flatnonzero(np.diff(depth) < 0) + 1).tolist()
    if resets:
        if (resets == REVIEWED_RESETS.get(dataset["dataset_id"])
                and set(resets) <= set(ts.tolist()) and set(resets) <= set(ss.tolist())):
            cases.append("07")
            evidence["07"] = {"reset_indices": resets}
        else:
            result["unresolved"].append("Unreviewed depth reset")
    for entry in document(folder, "profilometer_manifest.json"):
        if entry.get("status") == "unavailable":
            if (entry.get("sha256") or entry.get("nbytes") != 0 or entry.get("records") != 0
                    or (entry.get("file") and (folder / entry["file"]).exists())):
                result["unresolved"].append("Contradictory unavailable profile evidence")
            else:
                cases.append("10")
                evidence["10"] = "Explicitly unavailable with no contradictory payload evidence."
    message = (failure or {}).get("error", "")
    if "Profile positive minima" in message or "Profile references differ" in message:
        # Read complete profiles only for the corresponding failure.
        references = [e for e in manifest if e["log_name"] == "prof_min"]
        matches = []
        for profile in document(folder, "profilometer_manifest.json"):
            if profile.get("status") == "unavailable":
                continue
            path = (folder / profile["file"]).resolve()
            if not path.is_relative_to(folder):
                raise ValueError("Profile path leaves the hole directory")
            minima = []
            checksum = hashlib.sha256()
            with path.open("rb") as stream:
                for index, line in enumerate(stream):
                    checksum.update(line)
                    row = json.loads(line)
                    values = row["floatprofdata"]
                    if row["sampleNo"] != index or len(values) != profile["channel_count"] or not np.isfinite(values).all():
                        raise ValueError("Invalid profile axis or channels")
                    minima.append(min((x for x in values if x > 0), default=0.0))
            if len(minima) != n or checksum.hexdigest() != profile["sha256"]:
                raise ValueError("Profile source content mismatch")
            for reference in references:
                values = np.asarray([float("nan") if r[2] in ("", "null") else float(r[2]) for r in checked_rows(folder, reference)])
                finite = np.isfinite(values)
                if len(values) != n or not finite.any() or not np.isnan(values[~finite]).all():
                    raise ValueError("Invalid profile reference values")
                left, right = values[finite].astype(np.float32), np.asarray(minima)[finite].astype(np.float32)
                matches.append(np.isfinite(left).all() and np.isfinite(right).all() and np.array_equal(left.view(np.uint32), right.view(np.uint32)))
        if matches and all(matches):
            cases.append("06")
            evidence.setdefault("06", {})["profile_reference_float32_match"] = True
        else:
            result["unresolved"].append("Profile reference is not equivalent at float32 precision")
    for entry in document(folder, "logs_spectral.json"):
        waves = entry["wavelengths"]
        known = waves and entry["wavelength_units"] == "nm" and (min(waves), max(waves)) in ((380, 2500), (6000, 14500))
        if not known:
            cases.append("09")
            evidence.setdefault("09", []).append(entry["log_id"])
    result["suggested_cases"] = sorted(set(cases))
    return result


def main():
    # The old entry also requires failures, never an unrestricted raw scan.
    from diagnose import main as diagnose_main
    diagnose_main()


if __name__ == "__main__":
    main()

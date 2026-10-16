"""Process the failed holes assigned to one special case by a diagnosis report."""

import argparse
import ast
import hashlib
from pathlib import Path
import shutil
import tempfile

from _support import load_adapter, replace_once, read_json, source_metadata, write_json, save_processing_reports
from check_baseline import run_hole

HOME = Path(__file__).resolve().parent
BASE = HOME.parent / "etl4"
CASES = {p.name[:2]: p for p in sorted(HOME.iterdir()) if p.is_dir() and (p / "apply_fix.py").is_file()}


def code_hashes(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.suffix in (".py", ".sql")}


def build_copy(destination, cases, dataset_ids, split_metadata=False):
    before = code_hashes(BASE)
    # Copy code and references without caches, raw data or previous outputs.
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="special_code_", dir=destination.parent) as temporary:
        staging = Path(temporary) / "etl4"
        shutil.copytree(BASE, staging, ignore=shutil.ignore_patterns("__pycache__", ".venv", "refactor_work", "*.pyc"))
        for case in cases:
            load_adapter(case).apply(staging)
        version = "etl4-preparation-special-v1-" + "-".join(case.name[:2] for case in cases)
        if split_metadata:
            # Apply the split policy only to this external processing copy.
            filename = "processing/metadata_normalization.py"
            replace_once(staging, filename,
                         '"coordinate_basis": "project_owner_confirmed borehole.json x/y",',
                         '"coordinate_basis": b["coordinate_basis"],')
            replace_once(staging, filename,
                         '"orientation_missing_reason": orientation_missing_reason,',
                         '"orientation_missing_reason": "not_provided_in_selected_collar",')
            replace_once(staging, filename,
                         '"orientation_missing_reason_source": orientation_missing_reason_source,',
                         '"orientation_missing_reason_source": "split_optional_fields_policy",')
            version += "-split-metadata-v2"
        rules = {did: version for did in sorted(set(dataset_ids))}
        replace_once(staging, "processing/_etl_common.py", "PROCESSING_RULE_VERSIONS = {}",
                     "PROCESSING_RULE_VERSIONS = " + repr(rules))
        for path in staging.rglob("*.py"):
            ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        if before != code_hashes(BASE):
            raise ValueError("The baseline changed while building the processing copy")
        after = code_hashes(staging)
        if destination.exists():
            raise FileExistsError("Use a new output directory; existing code is never overwritten")
        staging.rename(destination)
    return {"cases": [c.name for c in cases], "processing_rule_version": version,
            "dataset_rules": rules, "baseline_sha256": before, "adapted_sha256": after,
            "changed_files": [p for p in before if before[p] != after[p]]}


def select_group(diagnosis, case, holes=None):
    if diagnosis.get("schema_version") != 2 or diagnosis.get("status") != "complete":
        raise ValueError("A completed failure diagnosis is required")
    if case not in CASES:
        raise ValueError("Unknown processing case")
    assigned = diagnosis["groups"].get(CASES[case].name, [])
    selected = assigned if holes is None else holes
    if not selected or len(selected) != len(set(selected)) or not set(selected) <= set(assigned):
        raise ValueError("Select only holes assigned to this processing folder")
    rows = {row["hole"]: row for row in diagnosis["holes"]}
    if len(rows) != len(diagnosis["holes"]):
        raise ValueError("Duplicate holes in diagnosis")
    result = []
    for name in selected:
        row = rows[name]
        if (row["status"] != "routed" or row["assigned_case"] != case or row["unresolved"]
                or row["failure"]["status"] != "failed" or not row["cases"] or not set(row["cases"]) <= CASES.keys()):
            raise ValueError("The hole is not an eligible, diagnosed failure")
        result.append(row)
    return result


def process_group(diagnosis_file, raw, work, case, holes=None, evidence_dir=None, until=6):
    diagnosis = read_json(diagnosis_file)
    rows = select_group(diagnosis, case, holes)
    raw, work = raw.resolve(), work.resolve()
    if work.is_relative_to(raw) or work.is_relative_to(HOME.parent):
        raise ValueError("Use an output directory outside raw data and the handoff repository")
    work.mkdir(parents=True, exist_ok=False)
    report = {"schema_version": 2, "status": "running", "raw_dir": str(raw), "assigned_case": case,
              "diagnosis_sha256": hashlib.sha256(diagnosis_file.read_bytes()).hexdigest(),
              "normal_etl_modified": False, "results": []}
    for index, row in enumerate(rows):
        name = row["hole"]
        folder = (raw / name).resolve()
        code = work / f"hole_{index:04d}/code/etl4"
        data = work / f"hole_{index:04d}/data"
        applied = []
        print(f"{index+1}/{len(rows)} {name}: cases {','.join(row['cases'])}", flush=True)
        try:
            if folder.parent != raw or source_metadata(folder) != row["metadata_sha256"]:
                raise ValueError("Source metadata changed since diagnosis; diagnose this hole again")
            dataset_ids = [d["dataset_id"] for d in read_json(folder / "datasets.json")]
            if dataset_ids != row["dataset_ids"]:
                raise ValueError("Dataset membership differs from diagnosis")
            package_file = folder / "package_manifest.json"
            selection = read_json(package_file).get("special_selection", {}) if package_file.exists() else {}
            proof = build_copy(code, [CASES[c] for c in sorted(row["cases"])], dataset_ids,
                               split_metadata=selection.get("case") == "13")
            write_json(code.parent / "processing_rules.json", proof)
            applied = row["cases"]
            route = "single" if len(dataset_ids) == 1 else "multi"
            cache = evidence_dir
            if route == "multi" and cache is None:
                original = Path(diagnosis["failure_report"]).parent
                subdir = row["failure"].get("work_subdirectory", "")
                candidate = (original / subdir / "database/multi_work/evidence").resolve()
                if candidate.is_relative_to(original.resolve()) and candidate.is_dir():
                    cache = candidate
            result = run_hole(code, raw, name, data, until, route=route, evidence_dir=cache)
        except (OSError, ValueError, KeyError, TypeError) as error:
            result = {"hole": name, "status": "failed", "failed_step": "special_setup",
                      "error": f"{type(error).__name__}: {error}"}
        result.update({"applied_cases": applied, "code_subdirectory": code.relative_to(work).as_posix(),
                       "work_subdirectory": data.relative_to(work).as_posix()})
        if result["status"] == "passed":
            result["next_step"] = 33 if result["route"] == "multi" else result["validated_through"] + 1
        report["results"].append(result)
        save_processing_reports(work, report, "special_processing_result.json")
    report["status"] = "complete"
    report["failed_count"] = sum(r["status"] == "failed" for r in report["results"])
    save_processing_reports(work, report, "special_processing_result.json")
    return report


def main(argv=None, *, forced_case=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--diagnosis", type=Path, required=True)
    parser.add_argument("--raw-dir", type=Path, help="Override a relocated raw directory; metadata must still match")
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--holes", nargs="+", help="Optional subset of this folder's assigned holes")
    if forced_case is None:
        parser.add_argument("--group", choices=sorted(CASES), required=True)
    parser.add_argument("--evidence-dir", type=Path, help="Existing official membership evidence for the multi route")
    parser.add_argument("--until", type=int, choices=range(1, 7), default=6)
    args = parser.parse_args(argv)
    diagnosis = read_json(args.diagnosis)
    raw = args.raw_dir or Path(diagnosis["raw_dir"])
    case = forced_case[:2] if forced_case else args.group
    report = process_group(args.diagnosis, raw, args.work_dir, case, args.holes, args.evidence_dir, args.until)
    print(f"Completed {len(report['results'])} holes; {report['failed_count']} still failed", flush=True)
    if report["failed_count"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

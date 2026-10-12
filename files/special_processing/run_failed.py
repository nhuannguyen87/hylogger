"""Diagnose failed inputs, prepare replacements and automatically retry supported cases."""

import argparse
from pathlib import Path

from _support import failure_rows, read_json, save_processing_reports, write_json
from check_baseline import BASE, run_hole
from dataset_inputs import FOLDERS, process_inputs
from diagnose import diagnose_report
from prepare import build_copy, process_group


def run_prepared(raw, name, work, first_failure=None):
    """Run normal preparation once, then apply only newly diagnosed rules."""
    work.mkdir(parents=True, exist_ok=False)
    result = first_failure
    applied = set((first_failure or {}).get("applied_cases", []))
    attempts = []
    if result is None:
        print("Normal preparation: " + name, flush=True)
        code = BASE
        package = read_json(raw / name / "package_manifest.json")
        if package.get("special_selection", {}).get("case") == "13":
            code = work / "code/etl4"
            ids = [d["dataset_id"] for d in read_json(raw / name / "datasets.json")]
            proof = build_copy(code, [], ids, split_metadata=True)
            write_json(work / "code/processing_rules.json", proof)
        result = run_hole(code, raw, name, work / "normal", 6)
        result.update(code_directory=str(code), work_directory=str(work / "normal"))
        attempts.append({"status": result["status"], "steps": result["steps"], "cases": []})
    # Each retry must add a supported rule; repeat failures stop without a timer.
    while result["status"] == "failed":
        attempt = work / f"retry_{len(attempts):02d}"
        attempt.mkdir()
        failure = {**result, "hole": name, "applied_cases": sorted(applied)}
        save_processing_reports(attempt, {"status": "complete", "raw_dir": str(raw),
                                "results": [failure]}, "preparation_result.json")
        diagnosis = diagnose_report(attempt / "failed_holes.json", raw, attempt / "diagnosis")
        row = diagnosis["holes"][0]
        result["retry_report"] = str(attempt / "failed_holes.json")
        if row["status"] != "routed" or set(row["cases"]) & FOLDERS.keys():
            result["unresolved"] = row["unresolved"] or ["Input selection requires a separate source diagnosis"]
            break
        combined = applied | set(row["cases"])
        if combined == applied:
            result["unresolved"] = ["No new supported rule; manual diagnosis required"]
            break
        # Preserve previously proven rules if the next error reveals another case.
        row["cases"] = sorted(combined)
        write_json(attempt / "diagnosis/diagnosis.json", diagnosis)
        print("Automatic retry: " + name + " cases " + ",".join(row["cases"]), flush=True)
        report = process_group(attempt / "diagnosis/diagnosis.json", raw,
                               attempt / "processing", row["assigned_case"], [name])
        result = report["results"][0]
        result["code_directory"] = str(attempt / "processing" / result["code_subdirectory"])
        result["work_directory"] = str(attempt / "processing" / result["work_subdirectory"])
        applied = combined
        attempts.append({"status": result["status"], "steps": result.get("steps", []), "cases": sorted(applied)})
    result.update(raw_dir=str(raw), attempts=attempts)
    if result["status"] == "passed":
        result["next_step"] = 33 if result.get("route") == "multi" else 7
    return result


def run_failed(failure_file, work, raw=None, cache=None, collars=None):
    source = read_json(failure_file)
    failures = failure_rows(source)
    raw = (raw or Path(source["raw_dir"])).resolve()
    work = work.resolve()
    if work.is_relative_to(raw) or work.is_relative_to(BASE.parent):
        raise ValueError("Use a fresh work directory outside raw data and the handoff repository")
    work.mkdir(parents=True, exist_ok=False)
    diagnosis = diagnose_report(failure_file, raw, work / "diagnosis")
    summary = {"schema_version": 1, "status": "running", "source_raw_dir": str(raw),
               "failed_source_count": len(failures), "normal_etl_modified": False, "results": []}
    jobs = []
    for case, folder in FOLDERS.items():
        if folder not in diagnosis["groups"]:
            continue
        selection = process_inputs(work / "diagnosis/diagnosis.json", work / folder,
                                   case, cache, collars, raw=raw)
        selected_raw = Path(selection["prepared_raw_dir"])
        for row in selection["results"]:
            for output in row.get("outputs", []):
                jobs.append((selected_raw, output["hole"], row["hole"], output["dataset_id"], None))
            for output in row.get("pending_outputs", []):
                summary["results"].append({"source_hole": row["hole"], "hole": output["hole"],
                                           "dataset_id": output["dataset_id"], "status": "failed",
                                           "error": output["reason"], "failed_step": "input_selection"})
            if row["status"] == "excluded" or (row["status"] == "failed" and not row.get("pending_outputs")):
                summary["results"].append({**row, "source_hole": row["hole"]})
    for row in diagnosis["holes"]:
        if row["status"] == "needs_review":
            summary["results"].append({"source_hole": row["hole"], "hole": row["hole"],
                                       "status": "needs_review", "unresolved": row["unresolved"]})
        elif row["assigned_case"] not in FOLDERS:
            jobs.append((raw, row["hole"], row["hole"], None, row["failure"]))
    write_json(work / "automation_result.json", summary)
    for index, (input_raw, name, original, did, failure) in enumerate(jobs):
        print(f"{index + 1}/{len(jobs)} {name}", flush=True)
        try:
            result = run_prepared(input_raw, name, work / f"processed_{index:02d}", failure)
        except (OSError, ValueError, KeyError, TypeError) as error:
            result = {"hole": name, "status": "failed", "failed_step": "automatic_preparation",
                      "error": f"{type(error).__name__}: {error}"}
        result.update(source_hole=original, dataset_id=did)
        summary["results"].append(result)
        write_json(work / "automation_result.json", summary)
    summary["status"] = "complete"
    summary["passed_count"] = sum(r["status"] == "passed" for r in summary["results"])
    summary["pending_count"] = sum(r["status"] in ("failed", "needs_review") for r in summary["results"])
    write_json(work / "automation_result.json", summary)
    lines = ["# Automatic preparation", "", "| Source | Output | Result | Detail |", "|---|---|---|---|"]
    for row in summary["results"]:
        detail = f"Continue at step {row['next_step']}" if row["status"] == "passed" else (
            "; ".join(row.get("unresolved", [])) or row.get("error", "Excluded by project scope"))
        lines.append(f"| {row['source_hole']} | {row['hole']} | {row['status']} | {detail.replace(chr(10), ' ').replace('|', '/')} |")
    (work / "automation_result.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--failures", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--raw-dir", type=Path)
    parser.add_argument("--cache-dir", type=Path)
    parser.add_argument("--collars", type=Path, help="Optional overrides; dataset coordinates are used by default")
    args = parser.parse_args()
    report = run_failed(args.failures, args.work_dir, args.raw_dir, args.cache_dir,
                         read_json(args.collars) if args.collars else None)
    print(f"Passed {report['passed_count']}; pending {report['pending_count']}", flush=True)
    if report["pending_count"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

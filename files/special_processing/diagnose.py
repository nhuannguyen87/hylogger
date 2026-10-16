"""Diagnose only failed holes and assign each to one special-processing folder."""

import argparse
import csv
import hashlib
from pathlib import Path

from _support import failure_rows, read_json, source_metadata, write_json
from inspect_cases import inspect, TEXT_LOGS

HOME = Path(__file__).resolve().parent
CASE_FOLDERS = {p.name[:2]: p.name for p in HOME.iterdir()
                if (p / "apply_fix.py").is_file() or (p / "prepare_inputs.py").is_file()}


def diagnose_hole(raw, failure):
    name = failure["hole"]
    folder = (raw / name).resolve()
    if folder.parent != raw:
        raise ValueError("Failure report contains a path instead of a hole name")
    before = {}
    try:
        before = source_metadata(folder)
        row = inspect(folder, failure)
        if before != source_metadata(folder):
            row["unresolved"].append("Source metadata changed during diagnosis")
    except (OSError, ValueError, KeyError, TypeError, IndexError, StopIteration, csv.Error) as error:
        row = {"hole": name, "suggested_cases": [], "evidence": {},
               "unresolved": [f"{type(error).__name__}: {error}"]}
    cases = row.pop("suggested_cases")
    unresolved = row["unresolved"]
    # Do not hide unknown integrity failures behind a known special case.
    if failure.get("payload_errors"):
        manifest = read_json(folder / "scalar_manifest.json") if "08" in cases else []
        literal_files = {e["file"] for e in manifest if e["log_id"] in TEXT_LOGS}
        for error in failure["payload_errors"]:
            if error["path"] not in literal_files or "08" not in cases:
                unresolved.append("Unresolved payload failure: " + error["path"] + ": " + error["error"])
    message = failure.get("error", "")
    blocked = ["ModuleNotFoundError", "PermissionError", "No space left", "Membership response content changed"]
    # Input handlers establish membership from the full cached dataset response.
    if not set(cases).intersection({"12", "13"}):
        blocked.append("Missing source membership evidence")
    if any(token in message for token in blocked):
        unresolved.append("Resolve the environment or missing evidence first: " + message.strip().splitlines()[-1])
    if not cases:
        unresolved.append("No supported special rule explains this failure")
    if failure.get("applied_cases") and set(cases) <= set(failure["applied_cases"]):
        unresolved.append("The same special rules already failed; manual diagnosis is required")
    primary = next((case for case in ("13", "12", "11") if case in cases),
                   next((case for case in cases if case != "01"), "01"))
    row.update({"cases": cases, "failure": failure, "metadata_sha256": before,
                "status": "needs_review" if unresolved else "routed",
                "assigned_case": None if unresolved else primary})
    return row


def diagnose_report(failure_file, raw, output):
    report = read_json(failure_file)
    failures = failure_rows(report)
    raw, output = raw.resolve(), output.resolve()
    if output.is_relative_to(raw) or output.is_relative_to(HOME.parent / "etl4"):
        raise ValueError("Diagnosis output must be outside raw data and the normal ETL")
    output.mkdir(parents=True, exist_ok=False)
    diagnosis = {"schema_version": 2, "status": "running", "raw_dir": str(raw),
                 "failure_report": str(failure_file.resolve()),
                 "failure_report_sha256": hashlib.sha256(failure_file.read_bytes()).hexdigest(),
                 "failed_count": len(failures), "excluded_nonfailed_count": len(report["results"])-len(failures),
                 "holes": [], "groups": {}}
    for index, failure in enumerate(failures, 1):
        row = diagnose_hole(raw, failure)
        diagnosis["holes"].append(row)
        print(f"{index}/{len(failures)} {row['hole']}: {row['status']} {','.join(row['cases'])}", flush=True)
        write_json(output / "diagnosis.json", diagnosis)
    for row in diagnosis["holes"]:
        if row["status"] == "routed":
            name = CASE_FOLDERS[row["assigned_case"]]
            diagnosis["groups"].setdefault(name, []).append(row["hole"])
    diagnosis["status"] = "complete"
    write_json(output / "diagnosis.json", diagnosis)
    for group, names in diagnosis["groups"].items():
        (output / (group + ".txt")).write_text("\n".join(names) + "\n", encoding="utf-8")
    pending = [row["hole"] for row in diagnosis["holes"] if row["status"] == "needs_review"]
    (output / "needs_review.txt").write_text("\n".join(pending) + ("\n" if pending else ""), encoding="utf-8")
    lines = ["# Failure diagnosis", "", "| Hole | Status | Assigned folder | All cases |", "|---|---|---|---|"]
    for row in diagnosis["holes"]:
        folder = CASE_FOLDERS.get(row["assigned_case"], "needs_review")
        lines.append(f"| {row['hole']} | {row['status']} | {folder} | {', '.join(row['cases'])} |")
    lines += ["", "## Needs review", ""]
    lines.extend(f"- {row['hole']}: {'; '.join(row['unresolved'])}" for row in diagnosis["holes"] if row["unresolved"])
    (output / "diagnosis.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return diagnosis


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--failures", type=Path, required=True, help="failed_holes.json or a completed processing report")
    parser.add_argument("--raw-dir", type=Path, help="Required only for older reports without raw_dir")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    source = read_json(args.failures)
    raw = args.raw_dir or (Path(source["raw_dir"]) if source.get("raw_dir") else None)
    if raw is None:
        parser.error("This older report needs --raw-dir")
    diagnosis = diagnose_report(args.failures, raw, args.output_dir)
    print(f"Diagnosed {diagnosis['failed_count']} failures; {len(diagnosis['groups'])} processing groups", flush=True)


if __name__ == "__main__":
    main()

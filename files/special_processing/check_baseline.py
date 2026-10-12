"""Run the selected normal pipeline and record the first failure per hole."""

import argparse
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import time
import shutil

from _support import read_json, save_processing_reports


STEPS = [
    "1_inventory.py", "2_validate_payloads.py", "3_normalize_metadata.py",
    "4_align_samples.py", "5_build_columnar.py", "6_verify_prepared_data.py",
]
BASE = Path(__file__).resolve().parents[1] / "etl4"


def run_hole(code, raw, hole, work, until, *, route="single", evidence_dir=None):
    work.mkdir(parents=True, exist_ok=False)
    result = {"hole": hole, "route": route, "steps": [], "status": "passed", "validated_through": 0}
    if route == "multi" and evidence_dir is not None:
        shutil.copytree(evidence_dir, work / "database/multi_work/evidence")
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1"}
    steps = [("processing", step) for step in STEPS[:until]] if route == "single" else [
        ("database", "31_audit_multi_dataset_sources.py"), ("database", "32_prepare_multi_dataset_payloads.py")]
    for subfolder, step in steps:
        command = [sys.executable, "-B", str(code / subfolder / step),
                   "--raw-dir", str(raw), "--work-dir", str(work), "--holes", hole]
        started = time.monotonic()
        with (work / (step + ".log")).open("w", encoding="utf-8") as output:
            process = subprocess.run(command, stdout=output, stderr=subprocess.STDOUT, env=env)
        entry = {"step": step, "exit_code": process.returncode,
                 "seconds": round(time.monotonic() - started, 3)}
        result["steps"].append(entry)
        if process.returncode:
            result["status"] = "failed"
            result["failed_step"] = step
            result["error"] = (work / (step + ".log")).read_text(encoding="utf-8")[-2500:]
            result["payload_errors"] = []
            for path in (work / "processing/outputs").rglob("2_integrity.json"):
                result["payload_errors"].extend({"path": item["path"], "error": item.get("error", "")}
                    for item in read_json(path).get("files", []) if item.get("status") == "failed")
            break
        result["validated_through"] = int(step.split("_", 1)[0])
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-dir", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--holes", nargs="+")
    parser.add_argument("--until", type=int, choices=range(1, 7), default=6)
    parser.add_argument("--route", choices=("auto", "single", "multi"), default="auto")
    parser.add_argument("--evidence-dir", type=Path, help="Existing official evidence for the explicit multi route")
    args = parser.parse_args()
    raw, work = args.raw_dir.resolve(), args.work_dir.resolve()
    if work == raw or work.is_relative_to(raw) or work == BASE or work.is_relative_to(BASE):
        parser.error("Use a new work directory outside raw data and baseline code")
    # Use a fresh directory to avoid stale results.
    work.mkdir(parents=True, exist_ok=False)
    names = args.holes or sorted(p.name for p in raw.iterdir() if p.is_dir())
    if len(names) != len(set(names)):
        parser.error("Duplicate hole names")
    report = {"schema_version": 2, "status": "running", "raw_dir": str(raw),
              "code_sha256": {str(p.relative_to(BASE)): hashlib.sha256(p.read_bytes()).hexdigest()
                              for p in sorted(BASE.rglob("*.py"))},
              "requested_until": args.until, "results": []}
    for index, hole in enumerate(names):
        folder = (raw / hole).resolve()
        if folder.parent != raw:
            raise ValueError("Hole must be one raw directory component")
        try:
            datasets = read_json(folder / "datasets.json")
            if not datasets:
                raise ValueError("Source contains no datasets")
            route = ("single" if len(datasets) == 1 else "multi") if args.route == "auto" else args.route
            if (len(datasets) == 1) != (route == "single"):
                item = {"hole": hole, "status": "requires_other_route", "dataset_count": len(datasets),
                        "note": "Select the matching normal route; this is not a special-case failure."}
            else:
                item = run_hole(BASE, raw, hole, work / f"hole_{index:04d}", args.until,
                                route=route, evidence_dir=args.evidence_dir)
        except (OSError, ValueError, KeyError, TypeError) as error:
            item = {"hole": hole, "status": "failed", "failed_step": "input_metadata",
                    "route": args.route, "error": f"{type(error).__name__}: {error}"}
        item["work_subdirectory"] = f"hole_{index:04d}"
        report["results"].append(item)
        save_processing_reports(work, report, "baseline_results.json")
        print(f"{index + 1}/{len(names)} {hole}: {item['status']}", flush=True)
    report["status"] = "complete"
    save_processing_reports(work, report, "baseline_results.json")


if __name__ == "__main__":
    main()

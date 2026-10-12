"""Small helpers for building a separate processing copy."""

import importlib.util
import hashlib
import json
import os
from pathlib import Path


def replace_once(code_dir, filename, before, after, count=1):
    path = code_dir / filename
    content = path.read_text(encoding="utf-8")
    if content.count(before) != count:
        raise ValueError(f"Adapter does not match this code snapshot: {filename}")
    changed = content.replace(before, after)
    compile(changed, filename, "exec")
    path.write_text(changed, encoding="utf-8")


def load_adapter(folder):
    spec = importlib.util.spec_from_file_location(folder.name, folder / "apply_fix.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def case_main(case):
    # A case entry accepts only failed holes assigned by diagnosis.
    from prepare import main
    main(forced_case=case)


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def source_metadata(folder):
    # Record diagnosis inputs; processing still rechecks payload contents.
    result = {}
    for path in sorted(folder.glob("*.json")):
        if path.resolve().parent != folder.resolve():
            raise ValueError("Source metadata link leaves the hole directory")
        result[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def failure_rows(report):
    if report.get("status") == "running":
        raise ValueError("Finish the current batch before diagnosing its failures")
    rows = report.get("results")
    if not isinstance(rows, list):
        raise ValueError("Expected a processing report with a results list")
    failures = [row for row in rows if row.get("status") == "failed"]
    names = [row["hole"] for row in failures]
    if len(names) != len(set(names)):
        raise ValueError("Failure report contains duplicate hole records")
    return failures


def save_processing_reports(work, report, filename):
    write_json(work / filename, report)
    write_json(work / "failed_holes.json", {
        "schema_version": 2, "raw_dir": report["raw_dir"],
        "status": report["status"], "source_report": filename,
        "results": [row for row in report["results"] if row["status"] == "failed"],
    })

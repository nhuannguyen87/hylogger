"""Exercise real pipeline steps on small, explicitly synthetic copies of a normal hole."""

import argparse
import copy
import csv
import hashlib
import json
from pathlib import Path
import shutil
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from prepare import BASE, CASES, build_copy
from check_baseline import run_hole


def read(folder, name):
    return json.loads((folder / name).read_text(encoding="utf-8-sig"))


def write(folder, name, data):
    (folder / name).write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def refresh(folder, entry, content=None):
    path = folder / entry["file"]
    if content is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    entry["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    entry["nbytes"] = path.stat().st_size


def duplicate_intervals(folder, different=False):
    dataset = read(folder, "datasets.json")[0]
    manifest, logs = read(folder, "scalar_manifest.json"), read(folder, "logs_scalar.json")
    for role in ("tray_id", "section_id"):
        source = next(x for x in manifest if x["log_id"] == dataset[role])
        alias = copy.deepcopy(source)
        alias["log_id"] = str(uuid.uuid5(uuid.NAMESPACE_URL, "fixture/" + role))
        alias["file"] = "scalars/fixture_" + role + ".csv"
        data = (folder / source["file"]).read_bytes()
        if different and role == "tray_id":
            rows = data.decode("utf-8-sig").splitlines()
            fields = rows[1].split(",")
            fields[2] = "99999"
            rows[1] = ",".join(fields)
            data = ("\n".join(rows) + "\n").encode("utf-8")
        refresh(folder, alias, data)
        manifest.append(alias)
        metadata = copy.deepcopy(next(x for x in logs if x["log_id"] == dataset[role]))
        metadata["log_id"] = alias["log_id"]
        logs.append(metadata)
    write(folder, "scalar_manifest.json", manifest)
    write(folder, "logs_scalar.json", logs)


def duplicate_images(folder, different=False, foreign=False):
    manifest = read(folder, "images_manifest.json")
    source = next(x for x in manifest["logs"] if x["log_name"] == "Tray Thumbnail Images")
    alias_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "fixture/image"))
    alias = {**source, "log_id": alias_id}
    manifest["logs"].append(alias)
    for item in list(manifest["files"]):
        if item["log_id"] != source["log_id"]:
            continue
        target = {**item, "log_id": alias_id, "file": "images/fixture_" + Path(item["file"]).name}
        if foreign:
            target["dataset_id"] = "different-dataset"
        data = (folder / item["file"]).read_bytes()
        # Alter fixture bytes while keeping the image readable.
        refresh(folder, target, data + (b"\n" if different else b""))
        manifest["files"].append(target)
    write(folder, "images_manifest.json", manifest)
    for name in ("logs_image.json", "image_logs_by_dataset.json"):
        logs = read(folder, name)
        entry = copy.deepcopy(next(x for x in logs if x["log_id"] == source["log_id"]))
        entry["log_id"] = alias_id
        logs.append(entry)
        write(folder, name, logs)
    depths = read(folder, "tray_depths.json")
    depths[alias_id] = copy.deepcopy(depths[source["log_id"]])
    write(folder, "tray_depths.json", depths)


def null_section(folder, bad=False):
    manifest = read(folder, "scalar_manifest.json")
    entry = next(x for x in manifest if x["log_name"] == "virtual_section")
    path = folder / entry["file"]
    rows = list(csv.reader(path.read_text(encoding="utf-8-sig").splitlines()))
    rows[1][2] = "null"
    if bad:
        rows[2][2] = "99999"
    with path.open("w", encoding="utf-8", newline="") as stream:
        csv.writer(stream).writerows(rows)
    refresh(folder, entry)
    write(folder, "scalar_manifest.json", manifest)


def unavailable_profile(folder, contradictory=False):
    manifest = read(folder, "profilometer_manifest.json")
    for entry in manifest:
        if not contradictory:
            (folder / entry["file"]).unlink()
        entry.update(status="unavailable", records=0, nbytes=0, sha256=None)
    write(folder, "profilometer_manifest.json", manifest)


def profile_precision(folder, different=False):
    profiles = read(folder, "profilometer_manifest.json")
    profile = profiles[0]
    path = folder / profile["file"]
    lines = path.read_text(encoding="utf-8").splitlines()
    first = json.loads(lines[0])
    first["floatprofdata"] = [1234100.25] + [0.0] * (len(first["floatprofdata"])-1)
    lines[0] = json.dumps(first)
    refresh(folder, profile, ("\n".join(lines) + "\n").encode("utf-8"))
    write(folder, "profilometer_manifest.json", profiles)
    manifest = read(folder, "scalar_manifest.json")
    entry = next(e for e in manifest if e["log_name"] == "prof_min")
    path = folder / entry["file"]
    rows = list(csv.reader(path.read_text(encoding="utf-8-sig").splitlines()))
    rows[1][2] = "1234101.2" if different else "1234100.2"
    with path.open("w", encoding="utf-8", newline="") as stream:
        csv.writer(stream).writerows(rows)
    refresh(folder, entry)
    write(folder, "scalar_manifest.json", manifest)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--normal-hole", type=Path, required=True, help="Small normal single-dataset source, e.g. raw/BB1")
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--only", nargs="+", help="Run selected fixture scenarios")
    args = parser.parse_args()
    source, work = args.normal_hole.resolve(), args.work_dir.resolve()
    if work == source or work.is_relative_to(source):
        raise ValueError("Test outputs must be outside the raw hole")
    work.mkdir(parents=True, exist_ok=False)
    did = read(source, "datasets.json")[0]["dataset_id"]
    report = {"fixture_basis": source.name, "synthetic": True, "results": []}
    scenarios = [
        ("interval_alias", "03", duplicate_intervals, True, 6),
        ("interval_difference", "03", lambda p: duplicate_intervals(p, True), False, 3),
        ("image_alias", "04", duplicate_images, True, 6),
        ("image_difference", "04", lambda p: duplicate_images(p, True), False, 4),
        ("image_wrong_dataset", "04", lambda p: duplicate_images(p, foreign=True), False, 4),
        ("nullable_section", "05", null_section, True, 6),
        ("wrong_nonnull_section", "05", lambda p: null_section(p, True), False, 4),
        ("unavailable_profile", "10", unavailable_profile, True, 6),
        ("contradictory_unavailable", "10", lambda p: unavailable_profile(p, True), False, 1),
        ("profile_float32", "06", profile_precision, True, 6),
        ("profile_different", "06", lambda p: profile_precision(p, True), False, 4),
    ]
    if args.only:
        if not set(args.only) <= {row[0] for row in scenarios}:
            parser.error("Unknown fixture scenario")
        scenarios = [row for row in scenarios if row[0] in args.only]
    for name, case, mutate, accepted, until in scenarios:
        location = work / name
        folder = location / "raw" / source.name
        shutil.copytree(source, folder, ignore=shutil.ignore_patterns("source_identity.json"))
        mutate(folder)
        package = read(folder, "package_manifest.json")
        package["content_fingerprint"] = hashlib.sha256(("synthetic/" + name).encode()).hexdigest()
        write(folder, "package_manifest.json", package)
        code = location / "code" / "etl4"
        build_copy(code, [CASES[case]], [did])
        baseline = run_hole(BASE, folder.parent, folder.name, location / "baseline", 6)
        adapted = run_hole(code, folder.parent, folder.name, location / "adapted", until)
        success = baseline["status"] == "failed" and (adapted["status"] == "passed") == accepted
        if not accepted:
            success = success and adapted.get("failed_step", "").startswith(str(until) + "_")
        report["results"].append({"case": name, "expected_accepted": accepted, "baseline": baseline,
                                  "adapted": adapted, "test_passed": success})
        (work / "test_results.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"{name}: {'PASS' if success else 'FAIL'}", flush=True)
    if not all(x["test_passed"] for x in report["results"]):
        raise SystemExit(1)


if __name__ == "__main__":
    main()

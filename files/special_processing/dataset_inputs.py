"""Prepare core-only inputs or split reviewed boreholes outside the normal ETL."""

import argparse
import copy
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import tempfile
from urllib.parse import parse_qs, unquote, urlparse
import xml.etree.ElementTree as ET

from _support import read_json, source_metadata, write_json

HOME = Path(__file__).resolve().parent
FOLDERS = {"12": "12_core_only", "13": "13_split_boreholes"}
METADATA = (
    "borehole.json", "datasets.json", "logs_scalar.json", "scalar_manifest.json",
    "logs_spectral.json", "spectral_manifest.json", "logs_profilometer.json",
    "profilometer_manifest.json", "logs_image.json", "image_logs_by_dataset.json",
    "images_manifest.json", "tray_depths.json", "package_manifest.json",
)
# These source identities were reviewed separately; other multi-dataset holes need review.
SPLITS = read_json(HOME / "13_split_boreholes/reviewed_splits.json")


def xml_root(data):
    if b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
        raise ValueError("XML entity declarations are not supported")
    return ET.fromstring(data)


def xml_fields(dataset):
    text = dataset.get("description", "")
    if not text:
        return {}
    fields = {}
    for node in xml_root(text.encode("utf-8")):
        name = node.tag.rsplit("}", 1)[-1]
        if name in fields:
            raise ValueError("Repeated dataset metadata field: " + name)
        fields[name] = (node.text or "").strip()
    return fields


def safe_name(name):
    if (not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_ -]+", name)
            or name != name.strip() or name.upper() in {"CON", "PRN", "AUX", "NUL"}
            or re.fullmatch(r"(?:COM|LPT)[1-9]", name, re.I)):
        raise ValueError("Unsafe output borehole name: " + str(name))
    return name


def selection_plan(hole, datasets):
    """Classify reviewed identities and explicit cuttings labels, without reading payloads."""
    original = unquote(hole)
    ids = [d["dataset_id"] for d in datasets]
    if not ids or len(ids) != len(set(ids)):
        raise ValueError("Missing or duplicate dataset IDs")
    if original in SPLITS:
        reviewed = SPLITS[original]
        if set(ids) != set(reviewed):
            raise ValueError("The reviewed split has changed dataset membership")
        outputs = []
        for dataset in datasets:
            project, label = reviewed[dataset["dataset_id"]]
            if xml_fields(dataset).get("Project", "") != project:
                raise ValueError("Source project differs from the reviewed split")
            outputs.append({"dataset_id": dataset["dataset_id"],
                            "hole": safe_name(original + "-" + label), "project": project})
        return {"case": "13", "outputs": outputs, "excluded": []}
    cuttings = [d for d in datasets if re.search(
        r"(?:^|[\s_()-])cuttings\d*(?:$|[\s_()-])", d.get("dataset_name", ""), re.I)]
    if not cuttings:
        return None
    excluded_ids = {d["dataset_id"] for d in cuttings}
    retained = [d for d in datasets if d["dataset_id"] not in excluded_ids]
    # A bare hole name is the core label in the reviewed NVCL packages.
    if len(retained) > 1 or any(d.get("dataset_name", "").casefold() != original.casefold() for d in retained):
        raise ValueError("The remaining sample type or dataset grouping needs review")
    return {"case": "12", "outputs": [
        {"dataset_id": d["dataset_id"], "hole": safe_name(original),
         "project": xml_fields(d).get("Project", "")} for d in retained],
        "excluded": [{"dataset_id": d["dataset_id"], "dataset_name": d["dataset_name"],
                      "reason": "out_of_scope_cuttings"} for d in cuttings]}


def source_collection(cache, hole, datasets):
    """Use the downloader's complete official response for log ownership, including profiles."""
    paths = []
    for path in (cache / hole).glob("*.txt"):
        url = urlparse(unquote(path.name[:-4]))
        if (url.hostname == "geossdi.dmp.wa.gov.au" and url.path.endswith("/getDatasetCollection.html")
                and parse_qs(url.query).get("holeidentifier") == [unquote(hole)]):
            paths.append(path)
    if len(paths) != 1:
        raise ValueError("Provide one cached official getDatasetCollection response for " + hole)
    body = paths[0].read_bytes()
    root = xml_root(body)
    if root.tag != "DatasetCollection":
        raise ValueError("Expected the official DatasetCollection XML response")
    nodes = {}
    for node in root.findall("Dataset"):
        did = node.findtext("DatasetID")
        if not did or did in nodes:
            raise ValueError("Duplicate or empty dataset in the official response")
        nodes[did] = node
    if set(nodes) != {d["dataset_id"] for d in datasets}:
        raise ValueError("Cached collection differs from the raw dataset membership")
    membership = {}
    owners = {kind: {} for kind in ("scalar", "spectral", "profile")}
    for dataset in datasets:
        did = dataset["dataset_id"]
        node = nodes[did]
        for local, remote in (("dataset_name", "DatasetName"), ("borehole_uri", "boreholeURI"),
                              ("tray_id", "trayID"), ("section_id", "sectionID"),
                              ("domain_id", "domainID"), ("description", "description")):
            if dataset.get(local, "") != (node.findtext(remote) or ""):
                raise ValueError("Cached dataset metadata differs: " + did + ": " + local)
        membership[did] = {}
        for kind, tag, id_tag in (("scalar", "Logs/Log", "LogID"),
                                 ("spectral", "SpectralLogs/SpectralLog", "logID"),
                                 ("profile", "ProfilometerLogs/ProfLog", "logID"),
                                 ("image", "ImageLogs/Log", "LogID")):
            ids = [entry.findtext(id_tag) for entry in node.findall(tag)]
            if any(not x for x in ids) or len(ids) != len(set(ids)):
                raise ValueError("Invalid official log IDs for " + did + ": " + kind)
            membership[did][kind] = set(ids)
            if kind in owners:
                for lid in ids:
                    if lid in owners[kind]:
                        raise ValueError("A source log belongs to more than one dataset: " + lid)
                    owners[kind][lid] = did
    return membership, body


def split_collar(output, dataset, collars=None):
    """Use dataset coordinates under the owner's shared-coordinate-system decision."""
    did = dataset["dataset_id"]
    fields = xml_fields(dataset)
    record = (collars or {}).get(did)
    # The shared hole record may mix attributes from separate sites.
    borehole = {key: None for key in (
        "operator", "driller", "boreholeMaterialCustodian", "drillingMethod",
        "boreholeLength_m", "drillStartDate", "drillEndDate", "elevation_m", "elevation_srs",
    )}
    source = "dataset.description Longitude/Latitude"
    if record is not None:
        if not isinstance(record, dict) or not isinstance(record.get("borehole"), dict) or not record.get("source"):
            raise ValueError("Invalid optional collar override for " + output["hole"])
        borehole.update(copy.deepcopy(record["borehole"]))
        source = record["source"]
    else:
        try:
            borehole.update(x=float(fields["Longitude"]), y=float(fields["Latitude"]))
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("Missing or invalid dataset coordinates: " + output["hole"]) from error
    x, y = borehole.get("x"), borehole.get("y")
    if (isinstance(x, bool) or isinstance(y, bool) or not isinstance(x, (int, float))
            or not isinstance(y, (int, float)) or not math.isfinite(x) or not math.isfinite(y)
            or not (-180 <= x <= 180 and -90 <= y <= 90)):
        raise ValueError("Invalid dataset coordinate: " + output["hole"])
    # Optional overrides must still describe this site's coordinates.
    for key, value in (("Longitude", x), ("Latitude", y)):
        if fields.get(key) and abs(float(fields[key]) - value) > 0.001:
            raise ValueError("Collar override is inconsistent with this dataset: " + output["hole"])
    borehole.update(nvcl_id=output["hole"], name=output["hole"], project=output["project"] or None)
    borehole["source_dataset_id"] = did
    borehole["coordinate_source"] = source
    borehole["source_coordinate_datum"] = fields.get("Datum") or None
    borehole["horizontal_crs"] = "EPSG:4326"
    borehole["coordinate_basis"] = "project_owner_assumed_shared_coordinate_system; source values unchanged"
    return borehole


def projected_metadata(raw, output, membership, plan, collars, collection_sha):
    metadata = copy.deepcopy(raw)
    did = output["dataset_id"]
    dataset = next(d for d in raw["datasets.json"] if d["dataset_id"] == did)
    ids = membership[did]
    metadata["datasets.json"] = [copy.deepcopy(dataset)]
    for kind, names in {
        "scalar": ("logs_scalar.json", "scalar_manifest.json"),
        "spectral": ("logs_spectral.json", "spectral_manifest.json"),
        "profile": ("logs_profilometer.json", "profilometer_manifest.json"),
        "image": ("logs_image.json", "image_logs_by_dataset.json"),
    }.items():
        for name in names:
            metadata[name] = [e for e in metadata[name] if e["log_id"] in ids[kind]]
            local_ids = [e["log_id"] for e in metadata[name]]
            if set(local_ids) != ids[kind] or len(local_ids) != len(set(local_ids)):
                raise ValueError("Incomplete or duplicate selected log metadata: " + name)
        for entry in metadata[names[1]]:
            if entry.get("dataset_id", did) != did:
                raise ValueError("Local log has a conflicting dataset association")
    scalar_ids = {e["log_id"] for e in metadata["scalar_manifest.json"]}
    if not {dataset["tray_id"], dataset["section_id"]} <= scalar_ids:
        raise ValueError("Selected dataset is missing its tray or section log")
    if not metadata["spectral_manifest.json"]:
        raise ValueError("Selected dataset has no spectral payload")
    metadata["images_manifest.json"] = {}
    for key, entries in raw["images_manifest.json"].items():
        if not isinstance(entries, list):
            raise ValueError("Unexpected image manifest structure")
        selected = [e for e in entries if e["dataset_id"] == did]
        if any(e["log_id"] not in ids["image"] for e in selected):
            raise ValueError("Image association contradicts the official dataset collection")
        metadata["images_manifest.json"][key] = selected
    metadata["tray_depths.json"] = {k: v for k, v in raw["tray_depths.json"].items() if k in ids["image"]}
    if plan["case"] == "13":
        metadata["borehole.json"] = split_collar(output, dataset, collars)
    elif str(metadata["borehole.json"]["nvcl_id"]).strip() != output["hole"]:
        raise ValueError("Core input identity differs from the source borehole")
    package = metadata["package_manifest.json"]
    package.update(hole_id=output["hole"], local_directory=output["hole"],
                   status="complete", warnings=[], errors=[])
    package["special_selection"] = {
        "rule_version": "dataset-input-selection-v2" if plan["case"] == "13" else "dataset-input-selection-v1",
        "case": plan["case"],
        "original_hole_id": raw["borehole.json"]["nvcl_id"], "dataset_id": did,
        "original_borehole": raw["borehole.json"], "source_project": output["project"],
        "official_collection_sha256": collection_sha, "excluded_datasets": plan["excluded"],
    }
    package.pop("content_fingerprint", None)
    package["content_fingerprint"] = hashlib.sha256(json.dumps(
        metadata, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode()).hexdigest()
    return metadata


def payload_entries(metadata):
    entries = []
    for name in ("scalar_manifest.json", "spectral_manifest.json", "profilometer_manifest.json"):
        entries.extend(e for e in metadata[name] if e.get("file"))
    entries.extend(e for e in metadata["images_manifest.json"]["files"] if e.get("file"))
    paths = [e["file"] for e in entries]
    if len(paths) != len(set(paths)):
        raise ValueError("Duplicate selected payload paths")
    return entries


def copy_payload(source, destination, entry):
    relative = entry["file"]
    path = (source / relative).resolve()
    target = (destination / relative).resolve()
    if (Path(relative).is_absolute() or ".." in Path(relative).parts
            or not path.is_relative_to(source.resolve()) or not target.is_relative_to(destination.resolve())
            or path == source.resolve() or target == destination.resolve()):
        raise ValueError("Payload path escapes the package")
    if not path.is_file() or path.stat().st_size != entry["nbytes"]:
        raise ValueError("Missing payload or size mismatch: " + relative)
    target.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as incoming, target.open("xb") as outgoing:
        for chunk in iter(lambda: incoming.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
            outgoing.write(chunk)
    if digest.hexdigest() != entry["sha256"] or size != entry["nbytes"]:
        raise ValueError("Payload content differs from its manifest: " + relative)


def prepare_hole(source, raw_output, provenance, row, cache, collars):
    before = source_metadata(source)
    if before != row["metadata_sha256"]:
        raise ValueError("Source metadata changed since diagnosis")
    raw = {name: read_json(source / name) for name in METADATA}
    plan = selection_plan(source.name, raw["datasets.json"])
    if plan is None or plan["case"] != row["assigned_case"]:
        raise ValueError("Selection policy differs from diagnosis")
    members, body = source_collection(cache, source.name, raw["datasets.json"])
    checksum = hashlib.sha256(body).hexdigest()
    names = [out["hole"].casefold() for out in plan["outputs"]]
    if len(names) != len(set(names)) or any((raw_output / out["hole"]).exists() for out in plan["outputs"]):
        raise ValueError("Output borehole name collision; existing inputs are never overwritten")
    provenance.mkdir(parents=True, exist_ok=False)
    for name in before:
        shutil.copyfile(source / name, provenance / name)
    (provenance / "official_collection.xml").write_bytes(body)
    result = {"hole": source.name, "status": "running", "case": plan["case"],
              "outputs": [], "pending_outputs": [], "excluded": plan["excluded"],
              "copied_payload_bytes": 0, "source_metadata_sha256": before,
              "official_collection_sha256": checksum}
    # Publish each complete output independently; another dataset may still fail.
    for output in plan["outputs"]:
        print("Preparing " + output["hole"], flush=True)
        try:
            metadata = projected_metadata(raw, output, members, plan, collars, checksum)
            entries = payload_entries(metadata)
            required = sum(e["nbytes"] for e in entries)
            if shutil.disk_usage(raw_output).free < required + 16 * 1024 * 1024:
                raise ValueError("Insufficient space for independent selected payload copies")
            with tempfile.TemporaryDirectory(prefix="selection_", dir=raw_output.parent) as temporary:
                folder = Path(temporary) / output["hole"]
                folder.mkdir()
                for entry in entries:
                    copy_payload(source, folder, entry)
                for name, value in metadata.items():
                    write_json(folder / name, value)
                (folder / "download-report.txt").write_text(
                    "Selected input package. See package_manifest.json for source provenance.\n", encoding="utf-8")
                if source_metadata(source) != before:
                    raise ValueError("Source metadata changed while preparing inputs")
                folder.rename(raw_output / output["hole"])
            result["outputs"].append(output)
            result["copied_payload_bytes"] += required
        except (OSError, ValueError, KeyError, TypeError) as error:
            result["pending_outputs"].append({**output, "reason": f"{type(error).__name__}: {error}"})
        write_json(provenance / "selection.json", result)
    if result["pending_outputs"]:
        result["status"] = "partial" if result["outputs"] else "failed"
    else:
        result["status"] = "prepared" if result["outputs"] else "excluded"
    write_json(provenance / "selection.json", result)
    return result


def process_inputs(diagnosis_file, work, case, cache, collars=None, holes=None, raw=None):
    if collars is not None and not isinstance(collars, dict):
        raise ValueError("Collars must be a JSON object keyed by source dataset ID")
    diagnosis = read_json(diagnosis_file)
    if diagnosis.get("schema_version") != 2 or diagnosis.get("status") != "complete":
        raise ValueError("A completed failure diagnosis is required")
    assigned = diagnosis.get("groups", {}).get(FOLDERS[case], [])
    selected = assigned if holes is None else holes
    if not selected or len(selected) != len(set(selected)) or not set(selected) <= set(assigned):
        raise ValueError("Select only failed holes assigned to this input handler")
    rows = {r["hole"]: r for r in diagnosis["holes"]}
    if len(rows) != len(diagnosis["holes"]):
        raise ValueError("Duplicate holes in diagnosis")
    raw = (raw or Path(diagnosis["raw_dir"])).resolve()
    work = work.resolve()
    if work.is_relative_to(raw) or work.is_relative_to(HOME.parent):
        raise ValueError("Use a new work directory outside raw data and this repository")
    for name in selected:
        row = rows[name]
        if ((raw / name).resolve().parent != raw or row.get("status") != "routed"
                or row.get("assigned_case") != case or row.get("unresolved")
                or row.get("failure", {}).get("status") != "failed"):
            raise ValueError("Only eligible diagnosed failures can be prepared")
    work.mkdir(parents=True, exist_ok=False)
    raw_output = work / "prepared_raw"
    raw_output.mkdir()
    report = {"schema_version": 2, "status": "running", "raw_dir": str(raw),
              "prepared_raw_dir": str(raw_output), "normal_etl_modified": False,
              "diagnosis_sha256": hashlib.sha256(diagnosis_file.read_bytes()).hexdigest(), "results": []}
    for name in selected:
        try:
            provider = read_json(raw / name / "package_manifest.json")["provider"]
            source_cache = cache if cache is not None else raw.parent / "cache" / provider
            result = prepare_hole(raw / name, raw_output, work / "provenance" / name,
                                  rows[name], source_cache, collars or {})
        except (OSError, ValueError, KeyError, TypeError, ET.ParseError) as error:
            result = {"hole": name, "status": "failed", "failed_step": "prepare_selected_inputs",
                      "error": f"{type(error).__name__}: {error}"}
        report["results"].append(result)
        write_json(work / "input_selection_result.json", report)
        print(name + ": " + result["status"], flush=True)
    report["status"] = "complete"
    report["failed_count"] = sum(r["status"] in ("failed", "partial") for r in report["results"])
    report["next_step"] = "Run check_baseline.py on prepared_raw; diagnose any remaining preparation failures"
    write_json(work / "input_selection_result.json", report)
    return report


def main(case):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--diagnosis", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--cache-dir", type=Path, help="Defaults to the sibling cache/<provider> directory")
    parser.add_argument("--raw-dir", type=Path)
    parser.add_argument("--holes", nargs="+")
    if case == "13":
        parser.add_argument("--collars", type=Path, help="Optional source-specific collar overrides; normally unnecessary")
    args = parser.parse_args()
    collars = read_json(args.collars) if case == "13" and args.collars else {}
    report = process_inputs(args.diagnosis, args.work_dir, case, args.cache_dir,
                            collars, args.holes, args.raw_dir)
    if report["failed_count"]:
        raise SystemExit(1)

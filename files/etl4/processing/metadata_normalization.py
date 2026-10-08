"""Normalize metadata and source identifiers while preserving original documents.
整理元数据和来源标识，同时保存原始文档。
"""

import math
import re
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from datetime import datetime
from processing._etl_common import (
    METADATA,
    issue,
    report_base,
    require_report,
    snapshot,
    source_json,
    stable_id,
    write_json,
    write_text,
)

FAMILIES = {
    "sTSAV": ("TSA", "VNIR", "s"),
    "uTSAV": ("TSA", "VNIR", "u"),
    "sTSAS": ("TSA", "SWIR", "s"),
    "uTSAS": ("TSA", "SWIR", "u"),
    "sjCLST": ("jCLST", "TIR", "s"),
    "ujCLST": ("jCLST", "TIR", "u"),
    "sTSAT": ("TSA", "TIR", "s"),
    "uTSAT": ("TSA", "TIR", "u"),
}
METRICS = {
    "Min": ("mineral_name", "Mineral name", "text", None),
    "Grp": ("mineral_group", "Mineral group", "text", None),
    "Wt": ("mineral_weight", "Model component weight", "numeric", "1"),
    "Error": ("fit_error", "Fit error", "numeric", None),
    "SNR": ("snr", "Source SNR metric", "numeric", None),
    "NIL_Stat": ("nil_stat", "Source NIL_Stat metric", "numeric", None),
    "Unbound_Water": ("unbound_water", "Source Unbound_Water metric", "numeric", None),
    "Bound_Water": ("bound_water", "Source Bound_Water metric", "numeric", None),
    "AspRat": ("asp_rat", "Source AspRat metric", "numeric", None),
    "TNorm": ("t_norm", "Source TNorm metric", "numeric", None),
}


def text_value(value):
    return None if value is None or value in ("", "unknown") else value


def number(value):
    value = text_value(value)
    if value is None:
        return None
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("Non-finite metadata number")
    return result


def source_bool(value):
    if value in (None, ""):
        return None
    if value is True or value == "true":
        return True
    if value is False or value == "false":
        return False
    raise ValueError(f"Unknown source boolean token: {value}")


def source_date(value):
    value = text_value(value)
    if value is None:
        return None
    datetime.fromisoformat(value)
    return value


def parse_xml(text):
    if not text:
        return {}
    if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
        raise ValueError("XML entity/DOCTYPE declarations require explicit handling")
    root = ET.fromstring(text)
    result = {}
    for child in root:
        key = child.tag.rsplit("}", 1)[-1]
        if key in result:
            raise ValueError(f"Repeated XML metadata node: {key}")
        result[key] = child.text or ""
    return result


def classify_name(name):
    match = re.fullmatch(r"(.+)\s+(sTSAV|uTSAV|sTSAS|uTSAS|sjCLST|ujCLST|sTSAT|uTSAT)", name)
    if not match:
        return None
    prefix, family = match.groups()
    rank_match = re.fullmatch(r"(Min|Grp|Wt)(\d+)", prefix)
    if rank_match:
        token, rank = rank_match.group(1), int(rank_match.group(2))
    else:
        token, rank = prefix, None
    metric = METRICS.get(token)
    return {
        "source_set_code": family,
        "source_metric_token": token,
        "component_rank": rank,
        "metric": metric,
    }


def normalize_hole(
    hole,
    context=None,
    *,
    source_metadata=None,
    dataset_selection=None,
    orientation_missing_reason="withheld_for_confidentiality",
    orientation_missing_reason_source="project_owner_statement",
):
    """Parse selected datasets and record complete provenance and missing-data reasons on the first write.
    按选定 dataset 解析，并在首次写入时保存完整来源及缺失原因。
    """
    folder, inv = snapshot(hole, context)
    integrity = require_report(folder, "2_integrity.json")
    observed = {x["path"]: x for x in integrity["files"]}
    raw = {name: source_json(hole, name, context) for name in METADATA}
    b, d, package = raw["borehole.json"], raw["datasets.json"][0], raw["package_manifest.json"]
    xml = parse_xml(d.get("description", ""))
    rev = stable_id(hole, inv["source_snapshot_id"], inv["pipeline_id"])
    borehole_id = stable_id(package["provider"], b["nvcl_id"])
    dataset_id = stable_id(package["provider"], d["dataset_id"])
    axis_id = stable_id(rev, "dense_sample_axis")
    x, y = number(b["x"]), number(b["y"])
    if x is None or y is None or not (-180 <= x <= 180 and -90 <= y <= 90):
        raise ValueError("Invalid WGS84 collar coordinate")
    issues = []
    normalized_borehole = {
        "id": borehole_id,
        "source_hole_id": b["nvcl_id"],
        "source_name": b["name"],
        "provider_code": package["provider"],
        "operator_name": text_value(b.get("operator")),
        "driller_name": text_value(b.get("driller")),
        "custodian_name": text_value(b.get("boreholeMaterialCustodian")),
        "project_name": text_value(b.get("project")),
        "drilling_method": text_value(b.get("drillingMethod")),
        "longitude": x,
        "latitude": y,
        "horizontal_crs": "EPSG:4326",
        "coordinate_basis": "project_owner_confirmed borehole.json x/y",
        "collar_geometry": {"type": "Point", "coordinates": [x, y]},
        "reported_length_m": number(b.get("boreholeLength_m")),
        "elevation_m": number(b.get("elevation_m")),
        "vertical_crs": text_value(b.get("elevation_srs")),
        "azimuth_deg": None,
        "inclination_deg": None,
        "inclination_convention": None,
        "trajectory_status": "unavailable",
        "orientation_missing_reason": orientation_missing_reason,
        "orientation_missing_reason_source": orientation_missing_reason_source,
        "drill_start_date": source_date(b.get("drillStartDate")),
        "drill_end_date": source_date(b.get("drillEndDate")),
        "actual_drill_date_precision": "unconfirmed",
        "source_identifier": b.get("identifier"),
        "metadata_revision_id": rev,
    }
    normalized_dataset = {
        "id": dataset_id,
        "borehole_id": borehole_id,
        "source_dataset_id": d["dataset_id"],
        "source_dataset_name": d["dataset_name"],
        "source_borehole_uri": d["borehole_uri"],
        "instrument_name": text_value(xml.get("InstrumentName")),
        "owner_name": text_value(xml.get("Owner")),
        "source_project_name": text_value(xml.get("Project")),
    }
    revision = {
        "id": rev,
        "dataset_id": dataset_id,
        "source_created_at": source_date(d.get("created_date")),
        "source_modified_at": source_date(d.get("modified_date")),
        "source_download_started_at": source_date(package.get("started_at")),
        "source_download_finished_at": source_date(package.get("finished_at")),
        "source_snapshot_id": inv["source_snapshot_id"],
        "pipeline_id": inv["pipeline_id"],
        "publication_status": "not_published",
        "metadata_xml_values": xml,
    }
    manifests = {
        "scalar": {x["log_id"]: x for x in raw["scalar_manifest.json"]},
        "spectral": {x["log_id"]: x for x in raw["spectral_manifest.json"]},
        "profile": {x["log_id"]: x for x in raw["profilometer_manifest.json"]},
        "image": {x["log_id"]: x for x in raw["images_manifest.json"]["logs"]},
    }
    image_files = defaultdict(list)
    for file in raw["images_manifest.json"]["files"]:
        image_files[file["log_id"]].append(file)
    logs, sets, streams, definitions = [], {}, {}, {}
    for kind, metadata_name in (
        ("scalar", "logs_scalar.json"),
        ("spectral", "logs_spectral.json"),
        ("profile", "logs_profilometer.json"),
        ("image", "logs_image.json"),
    ):
        source_logs = raw[metadata_name]
        if len({x["log_id"] for x in source_logs}) != len(source_logs):
            raise ValueError(f"Duplicate source log ID in {metadata_name}")
        if set(manifests[kind]) != {x["log_id"] for x in source_logs}:
            raise ValueError(f"Metadata/manifest log ID sets disagree: {kind}")
        for entry in source_logs:
            lid, name = entry["log_id"], entry["log_name"]
            manifest = manifests[kind][lid]
            paths = (
                [f["file"] for f in image_files[lid]]
                if kind == "image"
                else ([manifest["file"]] if manifest.get("file") else [])
            )
            log = {
                "id": stable_id(rev, kind, lid),
                "dataset_revision_id": rev,
                "source_log_id": lid,
                "source_log_name": name,
                "log_kind": kind,
                "source_log_type": entry.get("log_type"),
                "source_algorithm_id": entry.get("algorithm_id"),
                "source_is_public": source_bool(entry.get("is_public")),
                "source_mask_log_id": entry.get("mask_log_id") or None,
                "source_created_at": source_date(entry.get("created_date")),
                "source_modified_at": source_date(entry.get("modified_date")),
                "axis_id": None,
                "axis_binding_status": "pending_step_4" if kind != "image" else "not_a_dense_axis",
                "source_status": manifest.get("status"),
                "availability_status": "payload_present" if paths else "metadata_only",
                "omission_reason": (
                    "thumbnail_only_policy"
                    if kind == "image" and name in ("Tray Images", "Imagery") and not paths
                    else ("metadata_record_without_payload" if not paths else None)
                ),
                "payload_paths": paths,
                "metric_code": None,
                "metric_key": None,
                "component_rank": None,
                "interpretation_set_id": None,
                "spectral_stream_id": None,
                "definition_status": "unconfirmed",
                "unit": None,
                "source_metadata_file": metadata_name,
                "source_metadata": entry,
                "source_manifest": manifest,
            }
            classification = classify_name(name) if kind == "scalar" else None
            if classification:
                family = classification["source_set_code"]
                algorithm, region, variant = FAMILIES[family]
                set_id = stable_id(rev, "logical_interpretation_set", family)
                sets[family] = {
                    "id": set_id,
                    "dataset_revision_id": rev,
                    "source_set_code": family,
                    "algorithm_family": algorithm,
                    "output_region": region,
                    "variant_code": variant,
                    "algorithm_version": None,
                    "parameters": None,
                    "actual_mineral_library": None,
                    "grouping_basis": "source log name family; not a source-declared processing run",
                    "input_association_status": "unconfirmed",
                }
                log.update(
                    {
                        "interpretation_set_id": set_id,
                        "source_set_code": family,
                        "component_rank": classification["component_rank"],
                        "origin": "upstream_interpretation",
                    }
                )
                metric = classification["metric"]
                if metric:
                    code, display, value_kind, unit = metric
                    log.update(
                        {
                            "metric_code": code,
                            "metric_key": code,
                            "unit": unit,
                            "definition_status": "role_identified_exact_formula_unconfirmed",
                        }
                    )
                    definitions[code] = {
                        "metric_key": code,
                        "display_name": display,
                        "value_type": value_kind,
                        "unit": unit,
                        "definition_status": log["definition_status"],
                        "is_probability": False if code == "mineral_weight" else None,
                    }
            if kind == "scalar":
                stats = observed[paths[0]]
                log["observed_value_kind"] = stats["observed_value_kind"]
                log["observed_row_count"] = stats["row_count"]
                log["null_tokens"] = stats["null_tokens"]
                log["coordinate_kind"] = (
                    "sample_index_closed_interval"
                    if lid in (d["tray_id"], d["section_id"])
                    else "point_depth_m"
                )
                if log["metric_key"] is None:
                    key = "source_log:" + lid
                    log.update({"metric_key": key, "origin": "upstream_scalar"})
                    if name == "SecDist (mm)":
                        log.update(
                            {"unit": "mm", "definition_status": "unit_explicit_in_source_name"}
                        )
                    definitions[key] = {
                        "metric_key": key,
                        "display_name": name,
                        "value_type": stats["observed_value_kind"],
                        "unit": log["unit"],
                        "definition_status": log["definition_status"],
                        "source_log_id": lid,
                    }
            elif kind == "spectral":
                waves = entry["wavelengths"]
                region = (
                    "VSWIR"
                    if entry["wavelength_units"] == "nm"
                    and min(waves) == 380
                    and max(waves) == 2500
                    else (
                        "TIR"
                        if entry["wavelength_units"] == "nm"
                        and min(waves) == 6000
                        and max(waves) == 14500
                        else None
                    )
                )
                sid = stable_id(rev, "spectral_stream", region, str(waves))
                streams[sid] = {
                    "id": sid,
                    "dataset_revision_id": rev,
                    "region_code": region,
                    "wavelength_unit": entry["wavelength_units"],
                    "wavelength_count": len(waves),
                    "wavelengths": waves,
                    "classification_basis": "observed source wavelength axis",
                }
                log.update(
                    {
                        "spectral_stream_id": sid,
                        "source_script_raw": entry.get("script_raw"),
                        "source_script": entry.get("script"),
                        "array_layout_status": "unconfirmed",
                        "per_sample_publication_allowed": False,
                        "origin": "upstream_spectral",
                    }
                )
            elif kind == "profile":
                stats = observed[paths[0]]
                log.update(
                    {
                        "declared_channel_count": manifest["channel_count"],
                        "observed_channel_count": stats["observed_channel_count"],
                        "physical_unit": None,
                        "lateral_coordinates": None,
                        "physical_geometry_status": "uncalibrated",
                        "origin": "upstream_profile",
                    }
                )
            logs.append(log)
    for name, count in Counter(x["source_log_name"] for x in logs).items():
        if count > 1:
            issues.append(
                issue("DUPLICATE_LOG_NAME_PRESERVED", name, f"Preserved {count} source IDs", "info")
            )
    references = []
    source_ids = {x["source_log_id"] for x in logs if x["log_kind"] == "scalar"}
    for role in ("tray_id", "section_id", "domain_id"):
        target = d.get(role)
        resolved = target in source_ids
        references.append(
            {
                "role": role,
                "source_target_id": target,
                "status": "resolved" if resolved else "unresolved",
            }
        )
        if not resolved:
            issues.append(
                issue(
                    "UNRESOLVED_SOURCE_REFERENCE",
                    role,
                    "No name-based replacement was made.",
                    evidence=target,
                )
            )
    xml_x, xml_y = number(xml.get("Longitude")), number(xml.get("Latitude"))
    if (xml_x, xml_y) != (x, y):
        issues.append(
            issue(
                "COORDINATE_DECLARATIONS_DIFFER",
                "dataset XML",
                "Canonical collar uses owner-confirmed borehole.json x/y.",
                evidence={"borehole_xy": [x, y], "xml_lon_lat": [xml_x, xml_y]},
            )
        )
    data = {
        "borehole": normalized_borehole,
        "dataset": normalized_dataset,
        "dataset_revision": revision,
        "proposed_axis_id": axis_id,
        "logs": logs,
        "spectral_streams": list(streams.values()),
        "interpretation_sets": list(sets.values()),
        "interpretation_inputs": [],
        "metric_definitions": list(definitions.values()),
        "source_references": references,
        "raw_metadata": source_metadata if source_metadata is not None else raw,
    }
    if dataset_selection is not None:
        data["selected_dataset_context"] = dataset_selection
    write_json(folder / "3_normalized_metadata.json", data)
    report = report_base(inv, 3, folder / "2_integrity.json")
    report.update(
        {
            "status": "passed_with_issues" if issues else "passed",
            "issues": issues,
            "counts": {
                "logs": len(logs),
                "interpretation_sets": len(sets),
                "spectral_streams": len(streams),
                "metric_definitions": len(definitions),
                "source_metadata_documents_preserved": len(raw),
            },
        }
    )
    from processing._etl_common import sha_file

    report["normalized_metadata_sha256"] = sha_file(folder / "3_normalized_metadata.json")
    write_json(folder / "3_metadata_report.json", report)
    write_text(
        folder / "3_metadata_report.md",
        f"# {hole} Metadata normalization\n\n"
        f"Preserved {len(raw)} metadata documents, {len(logs)} logs, and {len(sets)} interpretation sets.\n\n"
        "Confidentiality-related omissions, thumbnail policies, and unconfirmed domain definitions retain explicit statuses; no confidence scores were generated.\n\n"
        + "\n".join(f"- {x['code']}: {x['scope']}. {x['detail']}" for x in issues)
        + "\n",
    )
    print(
        f"  {len(logs)} logs; {len(sets)} interpretation sets; {len(issues)} recorded issues",
        flush=True,
    )

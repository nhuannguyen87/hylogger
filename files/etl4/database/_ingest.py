"""Map prepared data into the database while preserving source values and relationships.

将准备结果映射到数据库，保持来源值和关联关系。
"""

import mimetypes
import time
import pyarrow.parquet as pq
import uuid
from _db_common import (
    ETL,
    LOCK_KEY,
    assert_schema,
    connection,
    importer_hash,
    insert,
    pick,
    schema_hash,
    uid,
)
from _db_files import digest, inside, read, sha
from _prepared_inputs import prepared_document, prepared_holes
from pathlib import Path
from psycopg import sql
from psycopg.types.json import Jsonb

MAPPING_VERSION = 1
MAPPING_MIGRATIONS = (
    "001_initial.sql",
    "002_nullable_metric_semantics.sql",
    "003_metric_source_identity.sql",
)

from _file_checks import fresh_file_checks
from _settings import path_reference, raw_asset_path, local_location


def input_digest(h):
    return digest(
        {
            "source_snapshot_id": h["metadata"]["dataset_revision"]["source_snapshot_id"],
            "pipeline_id": h["metadata"]["dataset_revision"]["pipeline_id"],
            "mapping_version": MAPPING_VERSION,
            "content": prepared_content(h),
        }
    )


def prepared_content(h):
    """Compare data content separately; run reports do not define import identity.

    数据内容单独比较，运行报告不参与导入身份。
    """
    folder = h["folder"]
    paths = [
        "3_normalized_metadata.json",
        "4_sample_axis.parquet",
        "4_core_intervals.json",
        "4_image_frames.json",
    ]
    paths.extend(a["relative_path"] for a in h["storage"]["assets"])
    return {name: sha(folder / name) for name in sorted(set(paths)) if (folder / name).is_file()}


def add_asset(
    conn,
    revision,
    representation,
    logical_path,
    kind,
    size,
    checksum,
    object_key,
    semantic,
    provenance,
):
    aid = uid("asset", revision, representation, logical_path)
    media = mimetypes.guess_type(logical_path)[0] or {
        "parquet": "application/vnd.apache.parquet",
        "f32": "application/octet-stream",
        "jsonl": "application/x-ndjson",
    }.get(Path(logical_path).suffix[1:], "application/octet-stream")
    insert(
        conn,
        "asset",
        {
            "id": aid,
            "dataset_revision_id": revision,
            "asset_kind": kind,
            "representation": representation,
            "logical_path": logical_path,
            "sha256": checksum,
            "byte_size": size,
            "media_type": media,
            "payload_status": "present",
            "semantic_status": semantic,
            "provenance": provenance,
        },
    )
    insert(
        conn,
        "asset_location",
        {
            "id": uid("location", aid, "local"),
            "asset_id": aid,
            "backend": "local",
            **local_location(object_key),
            "access_status": "verified_local",
            "verified_sha256": checksum,
        },
    )
    return aid


def import_source_assets(conn, h):
    """Register source files, original documents, and their log relationships.

    登记来源文件、原始文档和日志对应关系。
    """
    m = h["metadata"]
    storage = h["storage"]
    r = m["dataset_revision"]
    rev = r["id"]
    hole = h["summary"]["hole_id"]
    raw_asset_ids = {}
    for a in storage["raw_assets"]:
        rel = a["source_relative_path"]
        kind = a["kind"]
        semantic = "matrix_order_unconfirmed" if rel.endswith(".f32") else "source_preserved"
        if rel in h.get("verified_spectral_paths", []):
            semantic = "verified_sample_major_float32"
        aid = add_asset(
            conn,
            rev,
            "raw",
            rel,
            kind,
            a["byte_size"],
            a["sha256"],
            path_reference(raw_asset_path(hole, a)),
            semantic,
            {
                "source_snapshot_id": r["source_snapshot_id"],
                "proposed_object_key": a["proposed_object_key"],
            },
        )
        raw_asset_ids[rel] = aid
        if rel in m["raw_metadata"]:
            insert(
                conn,
                "source_document",
                {
                    "id": uid("document", rev, rel),
                    "dataset_revision_id": rev,
                    "asset_id": aid,
                    "document_name": rel,
                    "content_json": Jsonb(m["raw_metadata"][rel]),
                },
            )
        elif Path(rel).suffix.lower() in (".txt", ".xml"):
            p = raw_asset_path(hole, a)
            if sha(p) != a["sha256"]:
                raise ValueError("Text source changed after preflight")
            insert(
                conn,
                "source_document",
                {
                    "id": uid("document", rev, rel),
                    "dataset_revision_id": rev,
                    "asset_id": aid,
                    "document_name": rel,
                    "content_text": p.read_text(encoding="utf-8-sig"),
                },
            )
    for log in m["logs"]:
        for rel in log["payload_paths"]:
            if rel not in raw_asset_ids:
                raise ValueError("Log payload path is not in verified asset inventory")
            insert(
                conn,
                "log_asset",
                {
                    "log_id": log["id"],
                    "asset_id": raw_asset_ids[rel],
                    "dataset_revision_id": rev,
                    "role": "raw_source",
                },
            )
    return raw_asset_ids


def import_canonical_assets(conn, h, logs):
    """Register Parquet files and row-group indexes without changing scientific values.

    登记 Parquet 及其行组索引，不修改科学数值。
    """
    m = h["metadata"]
    storage = h["storage"]
    r = m["dataset_revision"]
    rev = r["id"]
    folder = h["folder"]
    axis = h["alignment"]["axis_id"]
    for a in storage["assets"]:
        source_log = logs.get(a.get("source_log_id"))
        log_id = source_log["id"] if source_log else None
        semantic = "uncalibrated_profile" if a["kind"] == "profile" else "verified_typed_values"
        aid = add_asset(
            conn,
            rev,
            "canonical",
            a["relative_path"],
            a["kind"],
            a["byte_size"],
            a["sha256"],
            path_reference(folder / a["relative_path"]),
            semantic,
            a.get("provenance", {"axis_id": axis, "origin": "derived_sample_axis"}),
        )
        if log_id:
            insert(
                conn,
                "log_asset",
                {
                    "log_id": log_id,
                    "asset_id": aid,
                    "dataset_revision_id": rev,
                    "role": "canonical",
                },
            )
        coordinate = source_log.get("coordinate_kind") if source_log else "point_depth_m"
        if a["kind"] == "profile":
            coordinate = "sample_index"
        for group, logical_hash in zip(a["row_groups"], a["logical_group_sha256"], strict=True):
            insert(
                conn,
                "data_chunk",
                {
                    "asset_id": aid,
                    "dataset_revision_id": rev,
                    "axis_id": axis,
                    "log_id": log_id,
                    **group,
                    "coordinate_kind": coordinate,
                    "logical_sha256": logical_hash,
                },
            )


def import_hole(conn, h, fail_after_samples=False, *, content_digest=None):
    m = h["metadata"]
    alignment = h["alignment"]
    storage = h["storage"]
    folder = h["folder"]
    hole = h["summary"]["hole_id"]
    b = m["borehole"]
    d = m["dataset"]
    r = m["dataset_revision"]
    rev = r["id"]
    axis = alignment["axis_id"]
    fingerprint = input_digest(h) if content_digest is None else content_digest
    # 原始文件也在每次导入边界核验，不能只信任早先的报告。 / Check source files at every import boundary instead of trusting earlier reports alone.
    for asset in storage["raw_assets"]:
        path = raw_asset_path(hole, asset)
        if path.stat().st_size != asset["byte_size"] or sha(path) != asset["sha256"]:
            raise ValueError("Raw input differs from its verified manifest")
    existing = conn.execute(
        "SELECT input_digest FROM core.dataset_revision WHERE id=%s", (rev,)
    ).fetchone()
    if existing:
        if existing["input_digest"] != fingerprint:
            raise ValueError("Same revision identity has different content or schema mapping")
        return {"hole_id": hole, "dataset_revision_id": rev, "action": "already_present"}
    if not conn.execute("SELECT 1 FROM core.borehole WHERE id=%s", (b["id"],)).fetchone():
        insert(
            conn, "borehole", pick(b, "id", "provider_code", "source_hole_id", "source_identifier")
        )
    br = pick(
        b,
        "source_name",
        "provider_code",
        "custodian_name",
        "driller_name",
        "operator_name",
        "project_name",
        "drilling_method",
        "reported_length_m",
        "drill_start_date",
        "drill_end_date",
        "actual_drill_date_precision",
        "coordinate_basis",
        "horizontal_crs",
        "vertical_crs",
        "elevation_m",
        "azimuth_deg",
        "inclination_deg",
        "inclination_convention",
        "orientation_missing_reason",
        "orientation_missing_reason_source",
        "trajectory_status",
    )
    br.update(id=b["metadata_revision_id"], borehole_id=b["id"], normalized_metadata=b)
    # EWKT 同时保留已确认的坐标系和二维坐标。 / EWKT preserves both the confirmed coordinate system and the two-dimensional coordinates.
    if b["longitude"] is not None and b["latitude"] is not None:
        br["collar_geom"] = f"SRID=4326;POINT({b['longitude']} {b['latitude']})"
    if not conn.execute("SELECT 1 FROM core.borehole_revision WHERE id=%s", (br["id"],)).fetchone():
        insert(conn, "borehole_revision", br)
    if not conn.execute("SELECT 1 FROM core.dataset WHERE id=%s", (d["id"],)).fetchone():
        insert(conn, "dataset", pick(d, "id", "borehole_id", "source_dataset_id"))
    dr = pick(
        r,
        "id",
        "dataset_id",
        "source_snapshot_id",
        "pipeline_id",
        "source_created_at",
        "source_modified_at",
        "source_download_started_at",
        "source_download_finished_at",
    )
    dr.update(
        pick(
            d,
            "source_dataset_name",
            "source_borehole_uri",
            "source_project_name",
            "instrument_name",
            "owner_name",
        )
    )
    dr.update(
        borehole_id=b["id"],
        borehole_revision_id=br["id"],
        input_digest=fingerprint,
        mapping_version=MAPPING_VERSION,
        normalized_metadata=r,
    )
    insert(conn, "dataset_revision", dr)
    insert(
        conn,
        "sample_axis",
        {
            "id": axis,
            "dataset_revision_id": rev,
            "sample_count": alignment["sample_count"],
            "depth_min_m": alignment["observed_depth_min_m"],
            "depth_max_m": alignment["observed_depth_max_m"],
            "alignment_evidence": alignment,
        },
    )
    intervals = prepared_document(h, "4_core_intervals.json")
    for i in sorted(intervals, key=lambda x: (x["interval_kind"] != "tray", x["ordinal"])):
        insert(conn, "core_interval", i)
    trays = {i["ordinal"]: i["id"] for i in intervals if i["interval_kind"] == "tray"}
    sections = {i["ordinal"]: i["id"] for i in intervals if i["interval_kind"] == "section"}
    columns = [
        "axis_id",
        "sample_no",
        "md_m",
        "tray_interval_id",
        "section_interval_id",
        "tray_sample_no",
        "section_sample_no",
        "section_distance_mm",
    ]
    with pq.ParquetFile(folder / "5_canonical/sample_axis.parquet") as pf:
        with conn.cursor().copy(
            sql.SQL("COPY core.scan_sample ({}) FROM STDIN").format(
                sql.SQL(",").join(map(sql.Identifier, columns))
            )
        ) as copy:
            for batch in pf.iter_batches(batch_size=4096):
                for row in batch.to_pylist():
                    copy.write_row(
                        (
                            axis,
                            row["sample_no"],
                            row["md_m"],
                            trays[row["tray_index"]],
                            sections[row["section_index"]],
                            row["tray_sample_no"],
                            row["section_sample_no"],
                            row["section_distance_mm"],
                        )
                    )
    if fail_after_samples:
        raise ValueError("Injected interruption after sample COPY")
    for stream in m["spectral_streams"]:
        insert(conn, "spectral_stream", stream)
    for result_set in m["interpretation_sets"]:
        insert(conn, "interpretation_set", result_set)
    if m["interpretation_inputs"]:
        raise ValueError("New interpretation input schema requires explicit mapping review")
    for metric in m["metric_definitions"]:
        insert(conn, "metric_definition", {"dataset_revision_id": rev, **metric})
    logs = {x["source_log_id"]: x for x in m["logs"]}
    if len(logs) != len(m["logs"]):
        raise ValueError("Source log IDs overlap across kinds; require scoped mapping")
    bindings = {x["source_log_id"]: x for x in prepared_document(h, "4_axis_bindings.json")}
    log_fields = [
        "id",
        "dataset_revision_id",
        "source_log_id",
        "source_log_name",
        "log_kind",
        "source_log_type",
        "source_algorithm_id",
        "source_mask_log_id",
        "source_is_public",
        "source_created_at",
        "source_modified_at",
        "source_status",
        "metric_key",
        "metric_code",
        "component_rank",
        "interpretation_set_id",
        "spectral_stream_id",
        "coordinate_kind",
        "observed_row_count",
        "observed_value_kind",
        "unit",
        "origin",
        "availability_status",
        "omission_reason",
        "definition_status",
        "array_layout_status",
        "declared_channel_count",
        "observed_channel_count",
        "physical_geometry_status",
        "null_tokens",
        "source_metadata_file",
    ]
    for log in m["logs"]:
        record = pick(log, *log_fields)
        record.update(
            normalized_metadata=log,
            per_sample_publication_allowed=bool(log.get("per_sample_publication_allowed", False)),
        )
        insert(conn, "scan_log", record)
        binding = bindings.get(log["source_log_id"])
        if binding:
            status = binding["status"]
            axis_id = binding["axis_id"]
            basis = binding["basis"]
        else:
            axis_id = None
            status = "unconfirmed" if log["log_kind"] == "spectral" else "not_a_dense_axis"
            basis = (
                "F32 ordering unconfirmed"
                if log["log_kind"] == "spectral"
                else "No verified dense binding; use explicit image/interval association where available"
            )
        insert(
            conn,
            "log_axis_binding",
            {
                "log_id": log["id"],
                "dataset_revision_id": rev,
                "axis_id": axis_id,
                "status": status,
                "basis": basis,
            },
        )
    raw_asset_ids = import_source_assets(conn, h)
    import_canonical_assets(conn, h, logs)
    for rel, checksum in h["prepared_hashes"].items():
        if rel.startswith("5_canonical/"):
            continue
        p = folder / rel
        add_asset(
            conn,
            rev,
            "evidence",
            rel,
            "processing_evidence",
            p.stat().st_size,
            checksum,
            path_reference(p),
            "verified_processing_evidence",
            {"pipeline_id": r["pipeline_id"]},
        )
    for frame in prepared_document(h, "4_image_frames.json"):
        f = {k: v for k, v in frame.items() if k not in ("source_log_id", "source_path")}
        f.update(
            dataset_revision_id=rev,
            axis_id=axis,
            log_id=logs[frame["source_log_id"]]["id"],
            asset_id=raw_asset_ids[frame["source_path"]],
        )
        insert(conn, "image_frame", f)
    for ref in m["source_references"]:
        target = logs.get(ref["source_target_id"])
        if ref["status"] == "resolved" and target is None:
            raise ValueError("Resolved source reference lacks an exact target ID")
        insert(
            conn,
            "source_reference",
            {
                "id": uid("reference", rev, ref["role"], ref["source_target_id"]),
                "dataset_revision_id": rev,
                **ref,
                "target_log_id": target["id"] if ref["status"] == "resolved" else None,
                "evidence": {"resolution": "exact source ID only"},
            },
        )
    for name in (
        "2_integrity.json",
        "3_metadata_report.json",
        "4_alignment_report.json",
        "5_storage_report.json",
    ):
        rep = read(folder / name)
        for i, issue in enumerate(rep.get("issues", [])):
            insert(
                conn,
                "quality_issue",
                {
                    "id": uid("issue", rev, name, i),
                    "dataset_revision_id": rev,
                    "processing_step": rep["step"],
                    **issue,
                    "evidence": (
                        Jsonb(issue["evidence"]) if issue.get("evidence") is not None else None
                    ),
                    "impact": "Preserved source limitation; see code and scope; no invented correction",
                },
            )
    return {
        "hole_id": hole,
        "dataset_revision_id": rev,
        "action": "inserted",
        "sample_count": alignment["sample_count"],
    }


@fresh_file_checks
def run_import(holes=None, dbname=None, failure_hole=None):
    selected = prepared_holes(holes)
    run_id = str(uuid.uuid4())
    result = []
    started = time.perf_counter()
    with connection("ingest", dbname=dbname) as conn:
        assert_schema(conn)
        locked = conn.execute(
            "SELECT pg_try_advisory_lock(%s) AS acquired", (LOCK_KEY,)
        ).fetchone()["acquired"]
        if not locked:
            raise RuntimeError("Another importer/validator is active; retry after it completes")
        insert(
            conn,
            "ingest_run",
            {
                "id": run_id,
                "input_digest": digest([input_digest(h) for h in selected]),
                "code_sha256": importer_hash(),
                "schema_sha256": schema_hash(),
                "holes": Jsonb([h["summary"]["hole_id"] for h in selected]),
                "status": "running",
            },
        )
        try:
            for h in selected:
                print(f"Import {h['summary']['hole_id']}", flush=True)
                with conn.transaction():
                    result.append(
                        import_hole(
                            conn, h, fail_after_samples=h["summary"]["hole_id"] == failure_hole
                        )
                    )
            conn.execute(
                "UPDATE core.ingest_run SET status='staged',finished_at=now(),detail=%s WHERE id=%s",
                (Jsonb({"holes": result}), run_id),
            )
        except Exception as exc:
            conn.execute(
                "UPDATE core.ingest_run SET status='failed',finished_at=now(),detail=%s WHERE id=%s",
                (Jsonb({"error_type": type(exc).__name__, "detail": str(exc), "completed_holes": result}), run_id),
            )
            raise
        finally:
            conn.execute("SELECT pg_advisory_unlock(%s)", (LOCK_KEY,))
    return {
        "status": "staged",
        "run_id": run_id,
        "holes": result,
        "elapsed_seconds": time.perf_counter() - started,
    }

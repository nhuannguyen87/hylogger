"""Check metadata, samples, and assets for one dataset without publishing or checking APIs.
核对单个 dataset 的元数据、样本和资产；不负责发布或接口检查。
"""

import pyarrow.parquet as pq
from psycopg import sql

from _db_files import encoded, sha
from _ingest import input_digest
from _prepared_inputs import prepared_document
from _settings import local_asset_path


def require(condition, message):
    if not condition:
        raise ValueError(message)


def verify_hole(conn, h, verify_assets=True, *, content_digest=None, media_asset_count=0):
    m = h["metadata"]
    rev = m["dataset_revision"]["id"]
    axis = h["alignment"]["axis_id"]
    folder = h["folder"]
    row = conn.execute("SELECT * FROM core.dataset_revision WHERE id=%s", (rev,)).fetchone()
    require(row is not None, "Expected revision not imported")
    fingerprint = input_digest(h) if content_digest is None else content_digest
    require(row["input_digest"] == fingerprint, "Import identity mismatch")
    require(
        row["normalized_metadata"] == m["dataset_revision"], "Dataset revision metadata differs"
    )
    br = conn.execute(
        "SELECT normalized_metadata FROM core.borehole_revision WHERE id=%s",
        (m["borehole"]["metadata_revision_id"],),
    ).fetchone()
    require(br["normalized_metadata"] == m["borehole"], "Borehole metadata lost")
    logs = conn.execute(
        "SELECT * FROM core.scan_log WHERE dataset_revision_id=%s", (rev,)
    ).fetchall()
    require(len(logs) == len(m["logs"]), "Metadata log count differs")
    indexed = {str(x["id"]): x for x in logs}
    for log in m["logs"]:
        require(indexed[log["id"]]["normalized_metadata"] == log, "Normalized log metadata differs")
    for name, raw in m["raw_metadata"].items():
        doc = conn.execute(
            "SELECT content_json FROM core.source_document WHERE dataset_revision_id=%s AND document_name=%s",
            (rev, name),
        ).fetchone()
        require(doc is not None and doc["content_json"] == raw, "Raw metadata document differs")
    for table, key in [
        ("spectral_stream", "spectral_streams"),
        ("interpretation_set", "interpretation_sets"),
        ("metric_definition", "metric_definitions"),
    ]:
        actual = conn.execute(
            sql.SQL("SELECT * FROM core.{} WHERE dataset_revision_id=%s").format(
                sql.Identifier(table)
            ),
            (rev,),
        ).fetchall()
        required = m[key]
        identity = "metric_key" if table == "metric_definition" else "id"
        bykey = {str(x[identity]): x for x in actual}
        require(len(actual) == len(required), f"{table} row count differs")
        for expected in required:
            got = bykey[expected[identity]]
            require(
                encoded({k: got[k] for k in expected}) == encoded(expected),
                f"{table} typed fields differ",
            )
    intervals = prepared_document(h, "4_core_intervals.json")
    byordinal = {(i["interval_kind"], i["ordinal"]): i["id"] for i in intervals}
    for expected in intervals:
        got = conn.execute(
            "SELECT * FROM core.core_interval WHERE id=%s", (expected["id"],)
        ).fetchone()
        require(encoded(got) == encoded(expected), "Core interval differs")
    for expected in prepared_document(h, "4_axis_bindings.json"):
        got = conn.execute(
            """SELECT b.* FROM core.log_axis_binding b JOIN core.scan_log l ON l.id=b.log_id
           WHERE l.dataset_revision_id=%s AND l.source_log_id=%s""",
            (rev, expected["source_log_id"]),
        ).fetchone()
        require(
            str(got["axis_id"]) == expected["axis_id"]
            and got["status"] == expected["status"]
            and got["basis"] == expected["basis"],
            "Verified binding differs",
        )
    samples_compared = 0
    with conn.transaction():
        with conn.cursor(name="axis_validation") as cur, pq.ParquetFile(
            folder / "5_canonical/sample_axis.parquet"
        ) as pf:
            cur.execute(
                "SELECT * FROM core.scan_sample WHERE axis_id=%s ORDER BY sample_no", (axis,)
            )
            for batch in pf.iter_batches(batch_size=4096):
                rows = batch.to_pylist()
                actual = cur.fetchmany(len(rows))
                require(len(actual) == len(rows), "Sample count lost")
                for source, got in zip(rows, actual):
                    expected = {
                        k: source[k]
                        for k in (
                            "sample_no",
                            "md_m",
                            "tray_sample_no",
                            "section_sample_no",
                            "section_distance_mm",
                        )
                    }
                    expected.update(
                        axis_id=axis,
                        tray_interval_id=byordinal[("tray", source["tray_index"])],
                        section_interval_id=byordinal[("section", source["section_index"])],
                    )
                    require(
                        encoded(got) == encoded(expected),
                        "Sample identity, depth or interval position differs",
                    )
                samples_compared += len(rows)
            require(cur.fetchone() is None, "Unexpected extra samples")
    assets = conn.execute(
        "SELECT * FROM core.asset WHERE dataset_revision_id=%s", (rev,)
    ).fetchall()
    raw = [a for a in assets if a["representation"] == "raw"]
    canonical = [a for a in assets if a["representation"] == "canonical"]
    require(len(raw) == h["summary"]["source_file_count"], "Raw asset inventory incomplete")
    require(
        len(canonical) == h["summary"]["canonical_file_count"] + media_asset_count,
        "Canonical asset inventory incomplete",
    )
    asset_map = {a["logical_path"]: a for a in canonical}
    chunks_compared = 0
    for expected in h["storage"]["assets"]:
        a = asset_map[expected["relative_path"]]
        require(
            a["sha256"] == expected["sha256"] and a["byte_size"] == expected["byte_size"],
            "Canonical identity differs",
        )
        chunks = conn.execute(
            "SELECT * FROM core.data_chunk WHERE asset_id=%s ORDER BY row_group", (a["id"],)
        ).fetchall()
        require(len(chunks) == len(expected["row_groups"]), "Chunk count mismatch")
        for got, wanted, logical in zip(
            chunks, expected["row_groups"], expected["logical_group_sha256"]
        ):
            require(
                {k: got[k] for k in wanted} == wanted and got["logical_sha256"] == logical,
                "Chunk coordinates or logical hash differ",
            )
        chunks_compared += len(chunks)
    checked_bytes = 0
    if verify_assets:
        for asset in assets:
            loc = conn.execute(
                "SELECT * FROM core.asset_location WHERE asset_id=%s AND backend='local'",
                (asset["id"],),
            ).fetchone()
            require(loc is not None, "Missing local location")
            path = local_asset_path(loc)
            require(
                path.is_file()
                and path.stat().st_size == asset["byte_size"]
                and sha(path) == asset["sha256"],
                "Asset file checksum or location differs",
            )
            checked_bytes += asset["byte_size"]
    frames = prepared_document(h, "4_image_frames.json")
    for expected in frames:
        got = conn.execute(
            "SELECT * FROM core.image_frame WHERE id=%s", (expected["id"],)
        ).fetchone()
        keys = [k for k in expected if k not in ("source_log_id", "source_path")]
        require(
            encoded({k: got[k] for k in keys}) == encoded({k: expected[k] for k in keys}),
            "Image association differs",
        )
    invalid = conn.execute(
        """SELECT count(*) AS n FROM core.scan_sample s
       JOIN core.core_interval t ON t.id=s.tray_interval_id JOIN core.core_interval c ON c.id=s.section_interval_id
       WHERE s.axis_id=%s AND (t.interval_kind<>'tray' OR c.interval_kind<>'section' OR c.parent_interval_id<>t.id
       OR s.sample_no NOT BETWEEN t.sample_no_from AND t.sample_no_to OR s.sample_no NOT BETWEEN c.sample_no_from AND c.sample_no_to
       OR s.tray_sample_no<>s.sample_no-t.sample_no_from+1 OR s.section_sample_no<>s.sample_no-c.sample_no_from+1)""",
        (axis,),
    ).fetchone()["n"]
    require(invalid == 0, "Sample position consistency failed")
    return {
        "hole_id": h["summary"]["hole_id"],
        "dataset_revision_id": rev,
        "samples_compared": samples_compared,
        "logs_compared": len(logs),
        "chunks_compared": chunks_compared,
        "images_compared": len(frames),
        "assets_hashed": len(assets) if verify_assets else 0,
        "asset_bytes_hashed": checked_bytes,
        "input_digest": fingerprint,
        "status": "passed",
    }

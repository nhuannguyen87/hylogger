"""Verify database backup and restore independently of legacy audit tools or HTTP services.

独立验证数据库备份和恢复，不依赖旧审查入口或 HTTP 服务。
"""

import hashlib
import os
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from psycopg import sql

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _db_common import CODE, LOCK_KEY, PG_BIN, assert_schema, config, connection, schema_hash
from _db_files import read, sha, write
from _run_reports import command, main_guard, report
from _settings import configure, settings

def require(ok, message):
    if not ok:
        raise ValueError(message)


RELATION_CHECKS = {
    "axis_count_and_extent": """SELECT count(*) AS n FROM (SELECT a.id FROM core.sample_axis a LEFT JOIN core.scan_sample s ON s.axis_id=a.id
        GROUP BY a.id HAVING a.sample_count<>count(s.*) OR min(s.sample_no)<>0 OR max(s.sample_no)<>a.sample_count-1
        OR a.depth_min_m<>min(s.md_m) OR a.depth_max_m<>max(s.md_m)) q""",
    "sample_interval_membership": """SELECT count(*) AS n FROM core.scan_sample s JOIN core.core_interval t ON t.id=s.tray_interval_id
        JOIN core.core_interval c ON c.id=s.section_interval_id WHERE t.interval_kind<>'tray' OR c.interval_kind<>'section'
        OR c.parent_interval_id<>t.id OR s.sample_no NOT BETWEEN t.sample_no_from AND t.sample_no_to
        OR s.sample_no NOT BETWEEN c.sample_no_from AND c.sample_no_to OR s.tray_sample_no<>s.sample_no-t.sample_no_from+1
        OR s.section_sample_no<>s.sample_no-c.sample_no_from+1""",
    "section_parent_and_extent": """SELECT count(*) AS n FROM core.core_interval c JOIN core.core_interval p ON p.id=c.parent_interval_id
        WHERE p.interval_kind<>'tray' OR c.sample_no_from<p.sample_no_from OR c.sample_no_to>p.sample_no_to""",
    "interval_depth_extent": """SELECT count(*) AS n FROM (SELECT i.id FROM core.core_interval i JOIN core.scan_sample s
        ON s.axis_id=i.axis_id AND s.sample_no BETWEEN i.sample_no_from AND i.sample_no_to GROUP BY i.id
        HAVING min(s.md_m) IS DISTINCT FROM i.observed_depth_min_m OR max(s.md_m) IS DISTINCT FROM i.observed_depth_max_m) q""",
    "chunk_dense_axis_binding": """SELECT count(*) AS n FROM core.data_chunk c JOIN core.scan_log l ON l.id=c.log_id
        LEFT JOIN core.log_axis_binding b ON b.log_id=c.log_id WHERE c.coordinate_kind IN ('point_depth_m','sample_index')
        AND (b.axis_id IS DISTINCT FROM c.axis_id OR b.status IS DISTINCT FROM 'verified')""",
    "chunk_interval_axis_binding": """SELECT count(*) AS n FROM core.data_chunk c JOIN core.scan_log l ON l.id=c.log_id
        WHERE c.coordinate_kind='sample_index_closed_interval' AND NOT EXISTS(SELECT 1 FROM core.core_interval i
        WHERE i.axis_id=c.axis_id AND i.source_log_id=l.source_log_id)""",
    "chunk_asset_log_relation": """SELECT count(*) AS n FROM core.data_chunk c LEFT JOIN core.log_asset a
        ON (a.log_id,a.asset_id)=(c.log_id,c.asset_id) WHERE c.log_id IS NOT NULL AND (a.log_id IS NULL OR a.role<>'canonical')""",
    "chunk_row_sequence": """SELECT count(*) AS n FROM (SELECT *,lag(source_row_to_exclusive) OVER(PARTITION BY asset_id ORDER BY row_group) previous_end,
        row_number() OVER(PARTITION BY asset_id ORDER BY row_group)-1 expected_group FROM core.data_chunk) c
        WHERE row_group<>expected_group OR (row_group=0 AND source_row_from<>0) OR (row_group>0 AND source_row_from<>previous_end)""",
    "chunk_scalar_row_count": """SELECT count(*) AS n FROM (SELECT c.asset_id,l.observed_row_count FROM core.data_chunk c JOIN core.scan_log l ON l.id=c.log_id
        WHERE l.log_kind='scalar' GROUP BY c.asset_id,l.observed_row_count HAVING sum(c.row_count) IS DISTINCT FROM l.observed_row_count) q""",
    "chunk_profile_row_count": """SELECT count(*) AS n FROM (SELECT c.asset_id,a.sample_count FROM core.data_chunk c JOIN core.scan_log l ON l.id=c.log_id
        JOIN core.sample_axis a ON a.id=c.axis_id WHERE l.log_kind='profile' GROUP BY c.asset_id,a.sample_count
        HAVING sum(c.row_count)<>a.sample_count) q""",
    "image_asset_and_tray_relation": """SELECT count(*) AS n FROM core.image_frame i LEFT JOIN core.log_asset a ON (a.log_id,a.asset_id)=(i.log_id,i.asset_id)
        JOIN core.core_interval t ON t.id=i.core_interval_id JOIN core.scan_log l ON l.id=i.log_id
        WHERE a.log_id IS NULL OR t.interval_kind<>'tray' OR i.source_tray_label<>t.source_label OR l.log_kind<>'image' """,
    "location_content_identity": """SELECT count(*) AS n FROM core.asset_location l JOIN core.asset a ON a.id=l.asset_id WHERE l.verified_sha256<>a.sha256""",
    "log_has_explicit_binding_status": """SELECT count(*) AS n FROM core.scan_log l LEFT JOIN core.log_axis_binding b ON b.log_id=l.id WHERE b.log_id IS NULL""",
    "unavailable_logs_have_no_payload": """SELECT count(*) AS n FROM core.scan_log l JOIN core.log_asset a ON a.log_id=l.id WHERE l.availability_status='metadata_only' """,
    "present_logs_have_source_payload": """SELECT count(*) AS n FROM core.scan_log l WHERE l.availability_status='payload_present'
        AND NOT EXISTS(SELECT 1 FROM core.log_asset a WHERE a.log_id=l.id AND a.role='raw_source')""",
    "weight_not_probability": """SELECT count(*) AS n FROM core.scan_log l JOIN core.metric_definition m
        ON (m.dataset_revision_id,m.metric_key)=(l.dataset_revision_id,l.metric_key) WHERE l.metric_code='mineral_weight' AND m.is_probability IS DISTINCT FROM false""",
    "unconfirmed_spectra_not_marked_usable": """SELECT count(*) AS n FROM core.scan_log l
      WHERE l.log_kind='spectral' AND l.per_sample_publication_allowed AND
      (l.array_layout_status IS DISTINCT FROM 'verified_sample_major' OR NOT EXISTS
       (SELECT 1 FROM core.log_axis_binding b WHERE b.log_id=l.id AND b.status='verified' AND b.axis_id IS NOT NULL))""",
    "valid_collar_geometry": """SELECT count(*) AS n FROM core.borehole_revision WHERE collar_geom IS NOT NULL
        AND (ST_IsEmpty(collar_geom) OR NOT ST_IsValid(collar_geom) OR ST_X(collar_geom) NOT BETWEEN -180 AND 180 OR ST_Y(collar_geom) NOT BETWEEN -90 AND 90)""",
    "unvalidated_constraints": "SELECT count(*) AS n FROM pg_constraint WHERE connamespace='core'::regnamespace AND NOT convalidated",
    "invalid_indexes": "SELECT count(*) AS n FROM pg_index i JOIN pg_class c ON c.oid=i.indrelid WHERE c.relnamespace='core'::regnamespace AND (NOT i.indisvalid OR NOT i.indisready)",
    "disabled_triggers": "SELECT count(*) AS n FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid WHERE c.relnamespace='core'::regnamespace AND t.tgenabled<>'O'",
}


def foreign_key_checks(conn):
    keys = conn.execute(
        """SELECT c.conname,src.relname src,dst.relname dst,c.confmatchtype,
        ARRAY(SELECT attname FROM unnest(c.conkey) WITH ORDINALITY k(att,ord) JOIN pg_attribute a ON a.attrelid=c.conrelid AND a.attnum=k.att ORDER BY ord) sc,
        ARRAY(SELECT attname FROM unnest(c.confkey) WITH ORDINALITY k(att,ord) JOIN pg_attribute a ON a.attrelid=c.confrelid AND a.attnum=k.att ORDER BY ord) dc
        FROM pg_constraint c JOIN pg_class src ON src.oid=c.conrelid JOIN pg_class dst ON dst.oid=c.confrelid
        WHERE c.connamespace='core'::regnamespace AND c.contype='f' ORDER BY src.relname,c.conname"""
    ).fetchall()
    for k in keys:
        require(k["confmatchtype"] == "s", "New FK match semantics need review")
        nonnull = sql.SQL(" AND ").join(
            sql.SQL("s.{} IS NOT NULL").format(sql.Identifier(x)) for x in k["sc"]
        )
        joined = sql.SQL(" AND ").join(
            sql.SQL("s.{}=d.{}").format(sql.Identifier(a), sql.Identifier(b))
            for a, b in zip(k["sc"], k["dc"])
        )
        query = sql.SQL(
            "SELECT count(*) n FROM core.{} s WHERE {} AND NOT EXISTS(SELECT 1 FROM core.{} d WHERE {})"
        ).format(sql.Identifier(k["src"]), nonnull, sql.Identifier(k["dst"]), joined)
        require(conn.execute(query).fetchone()["n"] == 0, f'Orphan FK: {k["conname"]}')
    return len(keys)


def schema_signature(conn):
    return {
        "columns": conn.execute(
            """SELECT c.table_name,c.column_name,c.data_type,c.udt_name,c.is_nullable,c.column_default,
            format_type(a.atttypid,a.atttypmod) exact_type,c.is_identity,c.is_generated,c.generation_expression
            FROM information_schema.columns c JOIN pg_namespace n ON n.nspname=c.table_schema
            JOIN pg_class r ON r.relnamespace=n.oid AND r.relname=c.table_name
            JOIN pg_attribute a ON a.attrelid=r.oid AND a.attname=c.column_name
            WHERE c.table_schema='core' ORDER BY c.table_name,c.ordinal_position"""
        ).fetchall(),
        "constraints": conn.execute(
            "SELECT r.relname,c.conname,pg_get_constraintdef(c.oid) definition FROM pg_constraint c JOIN pg_class r ON r.oid=c.conrelid WHERE c.connamespace='core'::regnamespace ORDER BY 1,2"
        ).fetchall(),
        "indexes": conn.execute(
            "SELECT tablename,indexname,indexdef FROM pg_indexes WHERE schemaname='core' ORDER BY 1,2"
        ).fetchall(),
        "triggers": conn.execute(
            "SELECT c.relname,t.tgname,pg_get_triggerdef(t.oid) definition FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid WHERE c.relnamespace='core'::regnamespace AND NOT t.tgisinternal ORDER BY 1,2"
        ).fetchall(),
        "functions": conn.execute(
            "SELECT p.proname,pg_get_functiondef(p.oid) definition FROM pg_proc p WHERE p.pronamespace='core'::regnamespace ORDER BY 1"
        ).fetchall(),
    }


def fingerprints(dbname=None):
    result = {}
    with connection(dbname=dbname) as c:
        tables = c.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname='core' ORDER BY tablename"
        ).fetchall()
        for row in tables:
            t = row["tablename"]
            if t in ("schema_migration", "ingest_run", "revision_validation", "active_release"):
                continue
            # 按文本稳定排序，摘要覆盖每条记录，不依赖内部序号。 / Sort text consistently so the digest covers every record without relying on internal sequence numbers.
            query = sql.SQL(
                'COPY (SELECT row_to_json(t)::text AS record FROM core.{} t ORDER BY row_to_json(t)::text COLLATE "C") TO STDOUT'
            ).format(sql.Identifier(t))
            h = hashlib.sha256()
            with c.cursor().copy(query) as cp:
                for data in cp:
                    h.update(data)
            result[t] = h.hexdigest()
    return result

def create_restore_database():
    """Create an empty database with a random name; stop before restore and deletion if creation fails.

    随机创建空库；创建失败时不会进入后续恢复和删除。
    """
    name = "etl4_restore_" + uuid.uuid4().hex
    require(name != config()["database"], "Cannot restore over the source database")
    with connection(dbname="postgres") as conn:
        conn.execute(
            sql.SQL("CREATE DATABASE {} TEMPLATE template0 ENCODING {}").format(
                sql.Identifier(name), sql.Literal("UTF8")
            )
        )
    return name


def drop_restore_database(name):
    """Accept only restore database names generated by this step, without terminating other sessions.

    只接受本步骤生成的恢复库名称，不终止其他会话。
    """
    require(
        re.fullmatch(r"etl4_restore_[0-9a-f]{32}", name) is not None
        and name != config()["database"],
        "Not an isolated restore database",
    )
    with connection(dbname="postgres") as conn:
        conn.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))


def backup_environment(database_config):
    env = os.environ.copy()
    owner = database_config["roles"]["owner"]
    env.update(
        PGHOST=database_config["host"],
        PGPORT=str(database_config["port"]),
        PGUSER=owner["user"],
        PGPASSWORD=owner["password"],
        PGCLIENTENCODING="UTF8",
    )
    return env


def restore_and_check(backup, env, baseline, structure, release, source_conn):
    """Compare restored records, schema, and relationships; clean up this restore database even on failure.

    恢复后比较记录、结构和关联；失败时也清理本次恢复库。
    """
    trial = create_restore_database()
    try:
        command(
            [
                PG_BIN / "pg_restore.exe",
                "--no-owner",
                "--exit-on-error",
                "--dbname",
                trial,
                backup,
            ],
            env=env,
            timeout=900,
        )
        restored = fingerprints(trial)
        require(restored == baseline, "Restored records differ")
        with connection(dbname=trial) as conn:
            require(schema_signature(conn) == structure, "Restored structure differs")
            assert_schema(conn)
            require(
                conn.execute(
                    "SELECT release_id FROM core.active_release WHERE singleton"
                ).fetchone()["release_id"] == release,
                "Restored release differs",
            )
            violations = {
                name: conn.execute(query).fetchone()["n"]
                for name, query in RELATION_CHECKS.items()
            }
            require(not any(violations.values()), "Restored relationship checks failed")
            foreign_keys = foreign_key_checks(conn)
            restored_bytes = conn.execute(
                "SELECT pg_database_size(current_database()) n"
            ).fetchone()["n"]
        require(fingerprints() == baseline, "Source data changed during backup")
        require(schema_signature(source_conn) == structure, "Source structure changed during backup")
        require(
            source_conn.execute(
                "SELECT release_id FROM core.active_release WHERE singleton"
            ).fetchone()["release_id"] == release,
            "Source release changed during backup",
        )
        return {
            "restored_hashes": restored,
            "restored_structure_matches": True,
            "restored_database_bytes": restored_bytes,
            "foreign_keys_checked": foreign_keys,
            "relation_violations": violations,
            "restored_trial_removed": True,
        }
    finally:
        drop_restore_database(trial)


def write_backup_reports(backup, release, baseline, restored_result, manifest):
    """Update the success report and latest-backup reference only after every check passes.

    全部检查通过后才更新成功报告和最新备份入口。
    """
    code_sha256 = sha(Path(__file__))
    schema_sha256 = schema_hash()
    result = {
        "status": "passed",
        "scope": "database-only; no API, cloud or source-file operations",
        "code_sha256": code_sha256,
        # 保留原报告字段，审查代码现在就在本文件中。 / Keep the original report field; the audit code is now in this file.
        "audit_code_sha256": code_sha256,
        "schema_sha256": schema_sha256,
        "release_id": release,
        "backup_relative_path": backup.relative_to(settings.database_dir).as_posix(),
        "backup_sha256": sha(backup),
        "backup_bytes": backup.stat().st_size,
        "business_table_hashes": baseline,
        **restored_result,
    }
    write(
        backup.with_suffix(".manifest.json"),
        {
            "backup_sha256": result["backup_sha256"],
            "release_id": release,
            "schema_sha256": schema_sha256,
            "migrations": {p.name: sha(p) for p in sorted((CODE / "migrations").glob("*.sql"))},
            "asset_manifest_sha256": sha(settings.reports_dir / "deployment_asset_manifest.json"),
            "asset_manifest": manifest,
            "runtime_credentials_included": False,
            "requires": "Existing roles, PostGIS extension binaries, and separately preserved referenced files",
        },
    )
    report("14_database_only_restore.json", result)
    report(
        "latest_database_backup.json",
        {
            "report": "14_database_only_restore.json",
            "backup_relative_path": result["backup_relative_path"],
            "backup_sha256": result["backup_sha256"],
            "schema_sha256": schema_sha256,
            "release_id": release,
        },
    )
    print(
        f'Database-only restore verified: {len(baseline)} business tables, '
        f'{result["foreign_keys_checked"]} foreign keys, {result["backup_bytes"]:,} backup bytes'
    )


def main():
    database_config = config()
    with connection() as guard:
        if not guard.execute("SELECT pg_try_advisory_lock(%s) ok", (LOCK_KEY,)).fetchone()["ok"]:
            raise RuntimeError("Importer/validator is active; retry after it completes")
        assert_schema(guard)
        release = guard.execute(
            "SELECT release_id FROM core.active_release WHERE singleton"
        ).fetchone()["release_id"]
        structure = schema_signature(guard)
        baseline = fingerprints()
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = settings.database_dir / "backups" / f"etl4_db_review_{release}_{stamp}.dump"
        backup.parent.mkdir(exist_ok=True)
        env = backup_environment(database_config)
        command(
            [
                PG_BIN / "pg_dump.exe",
                "--format=custom",
                "--no-owner",
                "--file",
                backup,
                "--dbname",
                database_config["database"],
            ],
            env=env,
            timeout=900,
        )
        restored_result = restore_and_check(
            backup, env, baseline, structure, release, guard
        )
        manifest = read(settings.reports_dir / "deployment_asset_manifest.json")
        require(manifest["release_id"] == str(release), "Asset manifest has a different release")
        write_backup_reports(backup, release, baseline, restored_result, manifest)


if __name__ == "__main__":
    from _settings import StepParser

    args = StepParser().parse_args()
    configure(args)
    main_guard(main)

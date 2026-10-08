"""Append an independent batch to the cloud database; prepare a cumulative release by default and switch only with explicit finalize input.
把独立批次追加到云端库；默认只准备累计发布，明确 finalize 后才切换。
"""

import base64
from datetime import datetime, timezone
from decimal import Decimal
import gzip
import hashlib
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from _batch_files import DATA_TABLES, KNOWN_TABLES, file_info, load_export, read_json, write_json
from _db_common import CODE, LOCK_KEY, uid
from _db_files import digest, encoded
from _settings import StepParser

FLOW = "etl4-cloud-batch-v1"
REVISION_TABLES = {
    "sample_axis", "spectral_stream", "interpretation_set", "interpretation_input",
    "metric_definition", "scan_log", "log_axis_binding", "asset", "log_asset",
    "data_chunk", "image_frame", "source_document", "source_reference", "quality_issue",
    "dataset_segment", "spectral_array", "spectral_sample_map", "image_region",
    "image_sample_mapping", "image_region_asset",
}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def plain(value):
    return json.loads(encoded(value))


def now():
    return datetime.now(timezone.utc).isoformat()


def cloud_connection(environment_name="ETL4_CLOUD_DSN"):
    """Configure the cloud connection independently without reading local database settings or a default database name.
    云端连接独立配置，不读取本地数据库的配置文件或默认库名。
    """
    require(environment_name.startswith("ETL4_CLOUD_"), 'The cloud connection environment variable must start with ETL4_CLOUD_')
    dsn = os.environ.get(environment_name)
    require(bool(dsn), f"Set {environment_name} first; do not put connection passwords on the command line")
    options = conninfo_to_dict(dsn)
    require(options.get("host") and options.get("dbname"), 'The cloud DSN must explicitly specify host and dbname')
    require(options.get("sslmode", "verify-full") == "verify-full", 'The cloud connection requires sslmode=verify-full')
    return psycopg.connect(dsn, autocommit=True, row_factory=dict_row, connect_timeout=20, sslmode="verify-full")


def cloud_target(conn):
    target = {"host": conn.info.host, "port": int(conn.info.port), "dbname": conn.info.dbname}
    return {**target, "fingerprint": digest(target)}


def upload_evidence(directory, manifest, manifest_sha, receipt_path):
    data = Path(receipt_path).read_bytes()
    receipt = read_json(receipt_path)
    require(receipt.get("format") == "etl4-batch-upload-v1" and receipt.get("status") == "complete",
            'The upload receipt from step 39 is not complete')
    require(receipt.get("batch_id") == manifest["batch_id"] and receipt.get("manifest_sha256") == manifest_sha,
            'The upload receipt does not match this batch export')
    require(receipt.get("bucket") and receipt.get("region") and isinstance(receipt.get("prefix"), str), 'The upload receipt is missing its target')
    prefix = receipt["prefix"]
    require(prefix == prefix.strip("/") and "\\" not in prefix and ":" not in prefix
            and (not prefix or all(part not in ("", ".", "..") for part in prefix.split("/"))), 'Invalid S3 prefix')
    batch_prefix = "/".join(filter(None, (prefix, "batches", manifest["batch_id"], manifest_sha)))
    expected = {item["object_key"]: {"sha256": item["sha256"], "byte_size": item["byte_size"],
                    "object_key": "/".join(filter(None, (prefix, item["object_key"]))), "kind": "asset"}
                for item in manifest["assets"]}
    expected.update({name: {"sha256": value["sha256"], "byte_size": value["byte_size"],
                    "object_key": batch_prefix + "/" + name, "kind": value["kind"]}
                    for name, value in manifest["files"].items()})
    expected["manifest.json"] = {"sha256": manifest_sha, "byte_size": (directory / "manifest.json").stat().st_size,
                                 "object_key": batch_prefix + "/manifest.json", "kind": "manifest"}
    rows = receipt.get("objects", [])
    require(len(rows) == len(expected) == receipt.get("expected_objects") == receipt.get("completed_objects"),
            'The upload receipt does not cover all assets and delivery files')
    by_key = {row.get("source_key"): row for row in rows}
    require(set(by_key) == set(expected) and len(by_key) == len(rows), 'Upload receipt objects are duplicated or have a different scope')
    for key, wanted in expected.items():
        actual = by_key[key]
        require(all(actual.get(name) == value for name, value in wanted.items()), 'An object identity in the upload receipt does not match')
        require(actual.get("checksum_type") == "FULL_OBJECT" and actual.get("checksum_algorithm") in ("SHA256", "CRC32"),
                'The upload receipt is missing full-object checksum evidence')
        try:
            checksum = base64.b64decode(actual["checksum"], validate=True)
        except (ValueError, KeyError):
            raise ValueError('The upload receipt checksum is invalid') from None
        if actual["checksum_algorithm"] == "SHA256":
            require(checksum.hex() == wanted["sha256"], 'The upload receipt SHA256 does not match')
        else:
            require(len(checksum) == 4, 'The upload receipt CRC32 is invalid')
    return receipt, hashlib.sha256(data).hexdigest(), by_key


def inspect_columns(conn):
    tables = {row["table_name"] for row in conn.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema='core' AND table_type='BASE TABLE'"
    ).fetchall()}
    require(tables == KNOWN_TABLES, 'The cloud table scope does not match the batch format')
    rows = conn.execute("""SELECT c.relname table_name,a.attname column_name,
        format_type(a.atttypid,a.atttypmod) data_type,NOT a.attnotnull nullable
        FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='core' AND c.relkind='r' AND a.attnum>0 AND NOT a.attisdropped ORDER BY c.relname,a.attnum""").fetchall()
    keys = conn.execute("""SELECT c.relname table_name,array_agg(a.attname ORDER BY k.ordinality) primary_key
        FROM pg_constraint p JOIN pg_class c ON c.oid=p.conrelid JOIN pg_namespace n ON n.oid=c.relnamespace
        CROSS JOIN LATERAL unnest(p.conkey) WITH ORDINALITY k(attnum,ordinality)
        JOIN pg_attribute a ON a.attrelid=c.oid AND a.attnum=k.attnum
        WHERE n.nspname='core' AND p.contype='p' GROUP BY c.relname""").fetchall()
    result = {name: {"columns": [], "primary_key": []} for name in tables}
    for row in rows:
        result[row["table_name"]]["columns"].append({key: row[key] for key in ("column_name", "data_type", "nullable")})
    for row in keys:
        result[row["table_name"]]["primary_key"] = list(row["primary_key"])
    return result


def existing_readers(conn):
    return [row["grantee"] for row in conn.execute("""SELECT DISTINCT grantee FROM information_schema.role_table_grants
        WHERE table_schema='core' AND table_name='dataset' AND privilege_type='SELECT'
        AND grantee<>'PUBLIC' AND grantee<>current_user ORDER BY grantee""").fetchall()]


def ensure_schema(conn, manifest, apply_migrations=False):
    expected = manifest["schema"]["migrations"]
    code = [{"name": path.name, "sha256": file_info(path, "schema")["sha256"]}
            for path in sorted((CODE / "migrations").glob("*.sql"))]
    require(expected == code, 'Batch migrations are not recognized by the current code; arbitrary SQL from the package will not be executed')
    exists = conn.execute("SELECT to_regclass('core.schema_migration') name").fetchone()["name"]
    applied = plain(conn.execute("SELECT name,sha256 FROM core.schema_migration ORDER BY name").fetchall()) if exists else []
    require(applied == expected[:len(applied)], 'Existing cloud migrations have a different order or digest; the batch cannot be appended directly')
    missing = expected[len(applied):]
    require(not missing or apply_migrations, 'Cloud migrations are missing; explicitly use --apply-migrations to add them')
    if missing:
        readers = existing_readers(conn) if applied else []
        with conn.transaction():
            conn.execute("CREATE EXTENSION IF NOT EXISTS postgis WITH SCHEMA public")
            for item in missing:
                conn.execute((CODE / "migrations" / item["name"]).read_text(encoding="utf-8"))
                conn.execute("INSERT INTO core.schema_migration(name,sha256) VALUES(%s,%s)", (item["name"], item["sha256"]))
            # Existing reader roles retain read access to new display tables and views without gaining write access. / 旧读取角色继续读取新增加的展示表和视图，不获得写入权限。
            for reader in readers:
                conn.execute(sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA core TO {}").format(sql.Identifier(reader)))
    require(plain(inspect_columns(conn)) == manifest["schema"]["tables"], 'Cloud columns, types, or primary keys do not match the exported schema')


def stage_name(table):
    require(table in DATA_TABLES, 'Unsupported business table')
    return "etl4_batch_" + table


def load_staging(conn, directory, manifest):
    """Stage structured rows only in the cloud session; COPY preserves the original numeric text in JSON.
    只在云会话中暂存结构化行；COPY 保留 JSON 内原有的数字文本。
    """
    conn.execute("CREATE TEMP TABLE etl4_batch_json(payload jsonb) ON COMMIT PRESERVE ROWS")
    for table in DATA_TABLES:
        columns = manifest["tables"][table]["columns"]
        expected_columns = [item["column_name"] for item in manifest["schema"]["tables"][table]["columns"]]
        require(columns == expected_columns, 'The table column list does not match the schema')
        target = sql.Identifier(stage_name(table))
        conn.execute(sql.SQL("CREATE TEMP TABLE {} (LIKE core.{} INCLUDING DEFAULTS) ON COMMIT PRESERVE ROWS").format(target, sql.Identifier(table)))
        conn.execute("TRUNCATE pg_temp.etl4_batch_json")
        count = 0
        with conn.cursor().copy("COPY pg_temp.etl4_batch_json(payload) FROM STDIN") as copy_rows:
            with gzip.open(directory / manifest["tables"][table]["path"], "rt", encoding="utf-8") as stream:
                for line in stream:
                    row = json.loads(line, parse_float=Decimal)
                    require(isinstance(row, dict) and set(row) == set(columns), 'The table file has missing or unknown fields')
                    copy_rows.write_row((line.strip(),))
                    count += 1
        require(count == manifest["tables"][table]["rows"], 'The table file row count does not match the manifest')
        values = []
        for name in columns:
            if table == "borehole_revision" and name == "collar_geom":
                values.append(sql.SQL("ST_GeomFromEWKB(decode(j.payload->>'collar_geom','hex'))"))
            else:
                values.append(sql.SQL("r.{}").format(sql.Identifier(name)))
        source = sql.SQL("j.payload - 'collar_geom'") if table == "borehole_revision" else sql.SQL("j.payload")
        conn.execute(sql.SQL("INSERT INTO {} SELECT {} FROM pg_temp.etl4_batch_json j CROSS JOIN LATERAL jsonb_populate_record(NULL::core.{}, {}) r").format(
            target, sql.SQL(",").join(values), sql.Identifier(table), source))
        keys = manifest["tables"][table]["primary_key"]
        require(keys == manifest["schema"]["tables"][table]["primary_key"] and keys, 'The table file primary key does not match')
        conn.execute(sql.SQL("CREATE UNIQUE INDEX ON {} ({})").format(target, sql.SQL(",").join(map(sql.Identifier, keys))))
        # Do not rescan the entire sample table when selecting the current dataset. / 选择当前 dataset 时不重复扫描整张样本表。
        for name in ("dataset_revision_id", "axis_id", "spectral_array_id", "borehole_id"):
            if name in columns and keys[0] != name:
                conn.execute(sql.SQL("CREATE INDEX ON {} ({})").format(target, sql.Identifier(name)))


def table_scope(table, revision, alias="s"):
    """Root every selection in the revisions declared by the package and obtain parent relationships from the staging tables.
    所有选择均以包中声明的版本为根，父记录也从暂存区取关联。
    """
    prefix = alias + "."
    if table in REVISION_TABLES:
        return prefix + "dataset_revision_id=%s::uuid", (revision,)
    if table in ("core_interval", "scan_sample"):
        return prefix + "axis_id IN (SELECT id FROM pg_temp.etl4_batch_sample_axis WHERE dataset_revision_id=%s::uuid)", (revision,)
    if table == "spectral_block":
        return prefix + "spectral_array_id IN (SELECT id FROM pg_temp.etl4_batch_spectral_array WHERE dataset_revision_id=%s::uuid)", (revision,)
    column = {"borehole": "borehole_id", "borehole_revision": "borehole_revision_id", "dataset": "dataset_id", "dataset_revision": "id"}.get(table)
    if column:
        return prefix + f"id IN (SELECT {column} FROM pg_temp.etl4_batch_dataset_revision WHERE id=%s::uuid)", (revision,)
    if table in ("borehole_branch", "survey_station"):
        return prefix + "borehole_id IN (SELECT borehole_id FROM pg_temp.etl4_batch_dataset_revision WHERE id=%s::uuid)", (revision,)
    raise ValueError('No dataset scope is defined for the business table')


def check_staging_scope(conn, manifest):
    actual = plain(conn.execute("SELECT dataset_id,id dataset_revision_id,input_digest FROM pg_temp.etl4_batch_dataset_revision ORDER BY dataset_id").fetchall())
    require(actual == manifest["identity"]["datasets"], 'Revision scope or content identity in the table files differs from the manifest')
    revisions = [item["dataset_revision_id"] for item in actual]
    for table in DATA_TABLES:
        terms = [table_scope(table, revision) for revision in revisions]
        clause = " OR ".join("(" + condition + ")" for condition, _ in terms)
        args = tuple(value for _, parameters in terms for value in parameters)
        query = sql.SQL("SELECT count(*) n FROM pg_temp.{} s WHERE NOT ({})").format(sql.Identifier(stage_name(table)), sql.SQL(clause))
        require(conn.execute(query, args).fetchone()["n"] == 0, f"{table} contains records outside this batch")
    assets = plain(conn.execute("SELECT id asset_id,dataset_revision_id,sha256,byte_size,media_type FROM pg_temp.etl4_batch_asset ORDER BY id").fetchall())
    expected = sorted([{key: item[key] for key in ("asset_id", "dataset_revision_id", "sha256", "byte_size", "media_type")}
                       for item in manifest["assets"]], key=lambda item: item["asset_id"])
    require(assets == expected, 'The asset table does not match the uploaded asset manifest')


def check_source_identity(conn, revision):
    for table, keys in (("borehole", ("provider_code", "source_hole_id")), ("dataset", ("borehole_id", "source_dataset_id"))):
        condition, args = table_scope(table, revision)
        join = sql.SQL(" AND ").join(sql.SQL("t.{}=s.{}").format(sql.Identifier(key), sql.Identifier(key)) for key in keys)
        query = sql.SQL("SELECT 1 FROM core.{} t JOIN pg_temp.{} s ON {} WHERE {} AND t.id<>s.id LIMIT 1").format(
            sql.Identifier(table), sql.Identifier(stage_name(table)), join, sql.SQL(condition))
        require(conn.execute(query, args).fetchone() is None, 'The same source borehole or dataset has different entity UUIDs; resolve the source identifier conflict first')


def insert_table(conn, table, revision, manifest):
    description = manifest["tables"][table]
    keys, columns = description["primary_key"], description["columns"]
    condition, args = table_scope(table, revision)
    join = sql.SQL(" AND ").join(sql.SQL("t.{}=s.{}").format(sql.Identifier(key), sql.Identifier(key)) for key in keys)
    difference = sql.SQL("to_jsonb(t) IS DISTINCT FROM to_jsonb(s)")
    if table == "borehole_revision":
        difference = sql.SQL("(to_jsonb(t)-'collar_geom') IS DISTINCT FROM (to_jsonb(s)-'collar_geom') OR ST_AsEWKB(t.collar_geom) IS DISTINCT FROM ST_AsEWKB(s.collar_geom)")
    conflict = sql.SQL("SELECT 1 FROM core.{} t JOIN pg_temp.{} s ON {} WHERE {} AND ({}) LIMIT 1").format(
        sql.Identifier(table), sql.Identifier(stage_name(table)), join, sql.SQL(condition), difference)
    require(conn.execute(conflict, args).fetchone() is None, f"{table} has different content for the same primary key; the original record was not overwritten")
    ordering = sql.SQL(",").join(sql.SQL("s.{}").format(sql.Identifier(key)) for key in keys)
    if table == "core_interval":
        ordering = sql.SQL("s.axis_id,CASE WHEN s.interval_kind='tray' THEN 0 ELSE 1 END,s.ordinal,s.id")
    prefix = sql.SQL("")
    source = sql.SQL("pg_temp.{} s").format(sql.Identifier(stage_name(table)))
    if table == "borehole_branch":
        prefix = sql.SQL("WITH RECURSIVE ordered AS (SELECT id,0 level FROM pg_temp.etl4_batch_borehole_branch WHERE parent_branch_id IS NULL UNION ALL SELECT b.id,p.level+1 FROM pg_temp.etl4_batch_borehole_branch b JOIN ordered p ON b.parent_branch_id=p.id) ")
        unresolved = conn.execute(sql.SQL("{} SELECT count(*) n FROM pg_temp.etl4_batch_borehole_branch s WHERE {} AND NOT EXISTS(SELECT 1 FROM ordered o WHERE o.id=s.id)").format(prefix, sql.SQL(condition)), args).fetchone()["n"]
        require(unresolved == 0, 'The branch parent chain is incomplete or cyclic')
        source = sql.SQL("pg_temp.etl4_batch_borehole_branch s JOIN ordered o ON o.id=s.id")
        ordering = sql.SQL("o.level,s.id")
    query = sql.SQL("{} INSERT INTO core.{} ({}) SELECT {} FROM {} WHERE {} AND NOT EXISTS(SELECT 1 FROM core.{} t WHERE {}) ORDER BY {}").format(
        prefix, sql.Identifier(table), sql.SQL(",").join(map(sql.Identifier, columns)),
        sql.SQL(",").join(sql.SQL("s.{}").format(sql.Identifier(name)) for name in columns), source,
        sql.SQL(condition), sql.Identifier(table), join, ordering)
    return conn.execute(query, args).rowcount


def local_acceptance_evidence(directory, manifest):
    second = read_json(directory / "evidence/acceptance_2.json")
    wanted = {item["dataset_id"]: {"dataset_revision_id": item["dataset_revision_id"], "input_digest": item["input_digest"]}
              for item in manifest["datasets"]}
    require(second.get("stage") == 2 and second.get("status") == "passed"
            and second.get("dataset_contents") == wanted and second.get("content_sha256") == digest(wanted),
            "The second local acceptance does not correspond to this batch's data")
    covered = set()
    require(second.get("prepared_acceptances"), 'The referenced first local acceptance is missing')
    for item in second["prepared_acceptances"]:
        path = item["report_path"]
        require(path in manifest["files"] and path.startswith("evidence/"), 'The first-stage report is not listed in the delivery manifest')
        report = read_json(directory / path)
        content = report.get("content", {})
        require(report.get("stage") == 1 and report.get("status") == "passed"
                and content.get("package_sha256") == item["package_sha256"]
                and report.get("content_sha256") == item["content_sha256"] == digest(content), 'The first local acceptance does not match')
        ids = set(content.get("datasets", {}))
        require(not covered.intersection(ids), 'The first-stage reports contain duplicate datasets')
        covered.update(ids)
    require(covered == set(wanted), 'The first acceptance covers different datasets')
    rows = read_json(directory / "evidence/revision_validation.json")["rows"]
    validations = {item["dataset_revision_id"]: item for item in rows}
    require(len(rows) == len(validations) and set(validations) == {item["dataset_revision_id"] for item in wanted.values()},
            'The local revision acceptance evidence covers a different scope')
    for item in wanted.values():
        value = validations[item["dataset_revision_id"]]
        require(value["input_digest"] == item["input_digest"] and value["result"].get("status") == "passed",
                'The local revision acceptance has not passed')
    composites = read_json(directory / "composites.json")
    require(digest(composites["definition"]) == manifest["identity"]["composite_definition_sha256"],
            "The display definition does not match this batch's identity")
    return validations, composites


def register_assets(conn, revision, manifest, receipt_objects, root_key):
    selected = [item for item in manifest["assets"] if item["dataset_revision_id"] == revision]
    records = [{"id": uid("cloud-location", item["asset_id"], root_key, receipt_objects[item["object_key"]]["object_key"]),
                "asset_id": item["asset_id"], "backend": "s3", "root_key": root_key,
                "object_key": receipt_objects[item["object_key"]]["object_key"],
                "access_status": "verified_remote", "verified_sha256": item["sha256"]} for item in selected]
    actual = plain(conn.execute("SELECT * FROM core.asset_location WHERE asset_id=ANY(%s::uuid[]) AND backend='s3' AND root_key=%s",
                                 ([item["asset_id"] for item in records], root_key)).fetchall())
    expected = {item["asset_id"]: item for item in records}
    require(len({item["asset_id"] for item in actual}) == len(actual), 'The same asset has duplicate S3 locations; specify the read location first')
    for item in actual:
        require(all(item[key] == expected[item["asset_id"]][key] for key in ("object_key", "access_status", "verified_sha256")),
                'The existing S3 location differs from the upload receipt; use a separate root_key')
    if records:
        conn.execute("""INSERT INTO core.asset_location
            SELECT r.* FROM jsonb_populate_recordset(NULL::core.asset_location,%s) r
            ON CONFLICT(asset_id,backend,root_key,object_key) DO NOTHING""", (Jsonb(records),))


def check_storage_root(conn, storage):
    known = conn.execute("""SELECT DISTINCT detail->'storage' storage FROM core.ingest_run
        WHERE detail->>'flow'=%s AND detail->'storage'->>'root_key'=%s""", (FLOW, storage["root_key"])).fetchall()
    require(all(row["storage"] == storage for row in known), 'This S3 root_key already represents another upload target')
    if not known:
        old = conn.execute("SELECT 1 FROM core.asset_location WHERE backend='s3' AND root_key=%s LIMIT 1", (storage["root_key"],)).fetchone()
        require(old is None, 'This S3 root_key has old locations without a batch target record; use a new root_key')


def record_validation(conn, validation):
    conn.execute("""INSERT INTO core.revision_validation
        SELECT r.* FROM jsonb_populate_record(NULL::core.revision_validation,%s) r
        ON CONFLICT(dataset_revision_id) DO NOTHING""", (Jsonb(validation),))
    known = conn.execute("SELECT input_digest,result FROM core.revision_validation WHERE dataset_revision_id=%s",
                         (validation["dataset_revision_id"],)).fetchone()
    require(known and known["input_digest"] == validation["input_digest"] and known["result"].get("status") == "passed",
            'The existing revision acceptance evidence differs from this batch')


def members_of(conn, release):
    if not release:
        return []
    row = conn.execute("SELECT manifest,manifest_sha256 FROM core.data_release WHERE id=%s", (release,)).fetchone()
    require(row and digest(row["manifest"]) == row["manifest_sha256"], 'The source release manifest is missing or its digest does not match')
    rows = plain(conn.execute("SELECT dataset_id,dataset_revision_id FROM core.release_dataset WHERE release_id=%s ORDER BY dataset_id", (release,)).fetchall())
    require(rows and rows == sorted(row["manifest"]["dataset_revisions"], key=lambda item: item["dataset_id"]), 'Source release members do not match the manifest')
    return rows


def accumulated_members(conn, incoming):
    active = conn.execute("SELECT release_id FROM core.active_release WHERE singleton").fetchone()
    recent = conn.execute("""SELECT detail->>'release_id' release_id FROM core.ingest_run
        WHERE status='staged' AND detail->>'flow'=%s AND detail ? 'release_id'
        ORDER BY finished_at DESC,id DESC LIMIT 1""", (FLOW,)).fetchone()
    sources = []
    for row in (active, recent):
        if row and str(row["release_id"]) not in sources:
            sources.append(str(row["release_id"]))
    members = {}
    for source in sources:
        members.update({item["dataset_id"]: item for item in members_of(conn, source)})
    replacements = []
    for item in incoming:
        previous = members.get(item["dataset_id"])
        if previous and previous["dataset_revision_id"] != item["dataset_revision_id"]:
            replacements.append({"dataset_id": item["dataset_id"], "previous_revision_id": previous["dataset_revision_id"],
                                 "incoming_revision_id": item["dataset_revision_id"],
                                 "basis": "incoming locally accepted revision; old revision retained; scientific equivalence not inferred"})
        members[item["dataset_id"]] = {key: item[key] for key in ("dataset_id", "dataset_revision_id")}
    return [members[key] for key in sorted(members)], sources, replacements


def insert_immutable(conn, table, record):
    """Release records contain few rows; compare every field on reuse without overwriting existing content.
    发布记录只有少量行；重复时逐字段比较，不覆盖旧内容。
    """
    found = conn.execute(sql.SQL("SELECT to_jsonb(t) value FROM core.{} t WHERE id=%s").format(sql.Identifier(table)), (record["id"],)).fetchone()
    if found:
        require(all(found["value"].get(key) == value for key, value in plain(record).items()), f"{table} release record identity conflict")
        return
    columns = list(record)
    values = [Jsonb(value) if isinstance(value, (dict, list)) else value for value in record.values()]
    conn.execute(sql.SQL("INSERT INTO core.{} ({}) VALUES ({})").format(sql.Identifier(table),
                 sql.SQL(",").join(map(sql.Identifier, columns)), sql.SQL(",").join(sql.Placeholder() for _ in columns)), values)


def display_sources(conn, members, inherited, incoming_composites):
    """Reuse accepted segment selections; old single-dataset releases can use the complete sample axis directly.
    复用已验收的取段；旧单 dataset 发布可直接使用完整样本轴。
    """
    revisions = [item["dataset_revision_id"] for item in members]
    datasets = plain(conn.execute("""SELECT d.id dataset_id,d.borehole_id,d.source_dataset_id,b.source_hole_id,
        r.id dataset_revision_id,a.id axis_id,a.sample_count,a.depth_min_m,a.depth_max_m
        FROM core.dataset_revision r JOIN core.dataset d ON d.id=r.dataset_id JOIN core.borehole b ON b.id=d.borehole_id
        JOIN core.sample_axis a ON a.dataset_revision_id=r.id WHERE r.id=ANY(%s::uuid[]) ORDER BY d.borehole_id,d.id""", (revisions,)).fetchall())
    require(len(datasets) == len(members), 'A candidate member does not have exactly one complete sample axis')
    available = []
    for release in inherited:
        composites = plain(conn.execute("SELECT * FROM core.borehole_composite WHERE release_id=%s ORDER BY borehole_id", (release,)).fetchall())
        parts = plain(conn.execute("SELECT * FROM core.borehole_composite_part WHERE release_id=%s ORDER BY borehole_id,sequence_no", (release,)).fetchall())
        available.extend((record, [part for part in parts if part["composite_id"] == record["id"]]) for record in composites)
    available.extend((record, [part for part in incoming_composites["parts"] if part["composite_id"] == record["id"]])
                     for record in incoming_composites["composites"])
    output = []
    for hole in sorted({item["borehole_id"] for item in datasets}):
        group = [item for item in datasets if item["borehole_id"] == hole]
        selected = {item["dataset_revision_id"] for item in group}
        match = next(((record, parts) for record, parts in reversed(available)
                      if record["borehole_id"] == hole and {part["dataset_revision_id"] for part in parts} == selected), None)
        if match:
            record, parts = match
            require({part["dataset_id"] for part in parts} == {item["dataset_id"] for item in group}, 'The display composite has inconsistent dataset ownership')
            parts = sorted(parts, key=lambda item: item["sequence_no"])
            require([part["sequence_no"] for part in parts] == list(range(len(group))), 'The display composite sequence is incomplete')
            output.append({"borehole_id": hole, "source_hole_id": group[0]["source_hole_id"], "source": record, "parts": parts})
        else:
            require(len(group) == 1, 'A borehole with multiple datasets lacks accepted segments covering all members; add a complete-borehole batch')
            item = group[0]
            output.append({"borehole_id": hole, "source_hole_id": item["source_hole_id"], "source": None,
                "parts": [{"borehole_id": hole, "sequence_no": 0, "dataset_id": item["dataset_id"],
                    "dataset_revision_id": item["dataset_revision_id"], "axis_id": item["axis_id"],
                    "selection_from_md_m": item["depth_min_m"], "selection_to_md_m": item["depth_max_m"],
                    "upper_inclusive": True, "sample_no_from": 0, "sample_no_to": item["sample_count"] - 1,
                    "selection_status": "selected"}]})
    return output


def display_definition(groups):
    return [{"borehole_id": group["borehole_id"], "parts": [
        {key: value for key, value in part.items() if key not in ("release_id", "composite_id", "predecessor_relation_id")}
        for part in group["parts"]]} for group in groups]


def install_display(conn, release, groups):
    for group in groups:
        definition = display_definition([group])[0]
        cid = uid("cloud-composite", release, group["borehole_id"])
        evidence = {"basis": "locally accepted source selection; no cloud scientific recalculation",
                    "source_hole_id": group["source_hole_id"]}
        insert_immutable(conn, "borehole_composite", {"id": cid, "release_id": release,
            "borehole_id": group["borehole_id"], "composition_kind": "single_dataset" if len(group["parts"]) == 1 else "confirmed_successor_chain",
            "boundary_rule": "successor_first_actual_sample_md_m", "missing_policy": "preserve_no_fallback",
            "trajectory_kind": "display_composite_not_surveyed_path", "definition_sha256": digest(definition), "evidence": evidence})
        for index, original in enumerate(group["parts"]):
            part = {key: value for key, value in original.items() if key not in ("release_id", "composite_id", "predecessor_relation_id")}
            relation_id = None
            if index:
                semantic = {"borehole_id": group["borehole_id"],
                    "predecessor_revision_id": group["parts"][index - 1]["dataset_revision_id"],
                    "successor_revision_id": part["dataset_revision_id"],
                    "relation_kind": "confirmed_display_successor", "switch_md_m": part["selection_from_md_m"],
                    "switch_basis": "successor_first_actual_sample_md_m"}
                existing = plain(conn.execute("SELECT * FROM core.dataset_succession WHERE predecessor_revision_id=%s AND successor_revision_id=%s",
                    (semantic["predecessor_revision_id"], semantic["successor_revision_id"])).fetchone())
                if existing:
                    require(all(existing[key] == value for key, value in semantic.items()), 'The existing dataset succession has different semantics')
                    relation_id = existing["id"]
                else:
                    relation_id = uid("cloud-succession", digest(semantic))
                    insert_immutable(conn, "dataset_succession", {"id": relation_id, **semantic,
                                     "confirmation_basis": {"source": "accepted batch display definition", "definition": definition}})
            part.update(composite_id=cid, release_id=release, predecessor_relation_id=relation_id)
            existing = plain(conn.execute("SELECT * FROM core.borehole_composite_part WHERE composite_id=%s AND sequence_no=%s", (cid, index)).fetchone())
            if existing:
                require(existing == part, 'The existing release segments differ from this definition')
            else:
                conn.execute("INSERT INTO core.borehole_composite_part SELECT r.* FROM jsonb_populate_record(NULL::core.borehole_composite_part,%s) r", (Jsonb(part),))


def ready_members(conn, members):
    revisions = [item["dataset_revision_id"] for item in members]
    row = conn.execute("""SELECT count(*) n FROM core.dataset_revision r
        LEFT JOIN core.revision_validation v ON v.dataset_revision_id=r.id
        WHERE r.id=ANY(%s::uuid[]) AND v.input_digest=r.input_digest AND v.result->>'status'='passed'""", (revisions,)).fetchone()
    require(row["n"] == len(revisions), 'The candidate release contains revisions that have not passed local acceptance')
    unavailable = conn.execute("""SELECT 1 FROM core.asset a WHERE a.dataset_revision_id=ANY(%s::uuid[])
        AND NOT EXISTS(SELECT 1 FROM core.asset_location l WHERE l.asset_id=a.id AND l.backend='s3'
        AND l.access_status='verified_remote' AND l.verified_sha256=a.sha256) LIMIT 1""", (revisions,)).fetchone()
    require(unavailable is None, 'The candidate release still has assets without registered S3 upload results')


def stage_candidate(conn, manifest, members, inherited, composites):
    ready_members(conn, members)
    groups = display_sources(conn, members, inherited, composites)
    release_manifest = {"dataset_revisions": sorted(members, key=lambda item: item["dataset_id"]),
                        "schema_sha256": digest(manifest["identity"]["schema_migrations"]),
                        "flow": FLOW, "display_sha256": digest(display_definition(groups))}
    release = uid("cloud-release", digest(release_manifest))
    insert_immutable(conn, "data_release", {"id": release, "manifest_sha256": digest(release_manifest),
                    "release_kind": "cloud_batch_candidate", "manifest": release_manifest})
    for member in members:
        row = conn.execute("SELECT dataset_revision_id FROM core.release_dataset WHERE release_id=%s AND dataset_id=%s", (release, member["dataset_id"])).fetchone()
        if row:
            require(str(row["dataset_revision_id"]) == member["dataset_revision_id"], 'The existing candidate release members conflict')
        else:
            conn.execute("INSERT INTO core.release_dataset(release_id,dataset_id,dataset_revision_id) VALUES(%s,%s,%s)",
                         (release, member["dataset_id"], member["dataset_revision_id"]))
    install_display(conn, release, groups)
    return release


def finalize_release(conn, manifest, selection, inherited, composites):
    """Declare the final set in the selection manifest and explicitly list any omitted existing datasets.
    最终集必须写在选择清单里；漏掉既有 dataset 也要明确列出。
    """
    require("expected_active_release_id" in selection, 'The finalize manifest must specify expected_active_release_id (null for an empty database)')
    chosen = selection.get("datasets", [])
    require(chosen and all(set(item) == {"dataset_id", "dataset_revision_id"} for item in chosen), 'Finalize must explicitly select the final revision of every dataset')
    require(len({item["dataset_id"] for item in chosen}) == len(chosen), 'Finalize contains duplicate datasets')
    accumulated, sources, _ = accumulated_members(conn, [])
    known = {item["dataset_id"] for item in accumulated}
    selected = {item["dataset_id"] for item in chosen}
    require(selected <= known and known - selected == set(selection.get("omitted_dataset_ids", [])),
            'Finalize omits accumulated members; explicitly list intentionally hidden datasets in omitted_dataset_ids')
    actual = plain(conn.execute("SELECT dataset_id,id dataset_revision_id FROM core.dataset_revision WHERE id=ANY(%s::uuid[]) ORDER BY dataset_id",
                                ([item["dataset_revision_id"] for item in chosen],)).fetchall())
    require(actual == sorted(chosen, key=lambda item: item["dataset_id"]), 'A finalize revision is missing or belongs to another dataset')
    sources = list(dict.fromkeys(inherited + sources + selection.get("source_release_ids", [])))
    for source in sources:
        members_of(conn, source)
    release = stage_candidate(conn, manifest, actual, sources, composites)
    current = conn.execute("SELECT release_id FROM core.active_release WHERE singleton FOR UPDATE").fetchone()
    previous = str(current["release_id"]) if current else None
    require(previous in (selection["expected_active_release_id"], release), 'The active release has changed; no switch was made')
    conn.execute("""INSERT INTO core.active_release(singleton,release_id) VALUES(true,%s)
        ON CONFLICT(singleton) DO UPDATE SET release_id=EXCLUDED.release_id,switched_at=now()""", (release,))
    return release, previous != release


def update_run(conn, run_id, status, detail):
    conn.execute("UPDATE core.ingest_run SET status=%s,finished_at=CASE WHEN %s='running' THEN NULL ELSE now() END,detail=%s WHERE id=%s",
                 (status, status, Jsonb(detail), run_id))


def save_progress(path, identity, completed, **extra):
    result = {**identity, "status": "partial", "datasets": completed, **extra}
    write_json(path, result)
    return result


def merge_batch(directory, *, upload_receipt=None, receipt_path=None, dsn_environment="ETL4_CLOUD_DSN",
                apply_migrations=False, root_key=None, finalize=None, connect=None):
    directory = Path(directory).resolve()
    manifest, manifest_sha = load_export(directory)
    upload_receipt = Path(upload_receipt or directory / "upload_receipt.json").resolve()
    upload, upload_sha, objects = upload_evidence(directory, manifest, manifest_sha, upload_receipt)
    validations, composites = local_acceptance_evidence(directory, manifest)
    receipt_path = Path(receipt_path or directory / "merge_receipt.json").resolve()
    protected = {directory / "manifest.json", directory / "local_sources.json", upload_receipt}
    protected.update((directory / name).resolve() for name in manifest["files"])
    require(receipt_path not in protected and receipt_path != directory, 'The merge receipt must not overwrite export files or the upload receipt')
    selection = read_json(finalize) if finalize else None
    storage = {key: upload[key] for key in ("bucket", "region", "prefix")}
    root_key = root_key or "etl4_s3_" + digest(storage)[:20]
    require(root_key and all(char.isalnum() or char in "_-" for char in root_key), 'An S3 root_key may contain only letters, digits, underscores, and hyphens')
    storage["root_key"] = root_key
    run_id = uid("cloud-batch", manifest["batch_id"], root_key)
    signature = digest({"identity": manifest["identity"], "tables": manifest["tables"], "assets": manifest["assets"]})
    with (connect() if connect else cloud_connection(dsn_environment)) as conn:
        conn.execute("SET search_path TO core,public")
        conn.execute("SET TIME ZONE 'UTC'")
        conn.execute("SET DateStyle TO 'ISO, YMD'")
        conn.execute("SET extra_float_digits TO 3")
        target = cloud_target(conn)
        identity = {"format": "etl4-batch-merge-v1", "batch_id": manifest["batch_id"],
                    "manifest_sha256": manifest_sha, "upload_receipt_sha256": upload_sha,
                    **{key: upload[key] for key in ("bucket", "region", "prefix")},
                    "cloud_target": target, "s3_root_key": root_key, "run_id": run_id}
        if receipt_path.exists():
            previous = read_json(receipt_path)
            require(all(previous.get(key) == identity[key] for key in ("format", "batch_id", "manifest_sha256", "cloud_target", "bucket", "region", "prefix", "s3_root_key")),
                    'The existing merge receipt belongs to another package or target; choose another --receipt')
        require(conn.execute("SELECT pg_try_advisory_lock(%s) ok", (LOCK_KEY,)).fetchone()["ok"], 'Another cloud import or publication is in progress')
        detail, completed, committed = None, [], False
        try:
            ensure_schema(conn, manifest, apply_migrations)
            check_storage_root(conn, storage)
            existing = conn.execute("SELECT status,input_digest,detail FROM core.ingest_run WHERE id=%s", (run_id,)).fetchone()
            if existing:
                detail = existing["detail"]
                require(existing["input_digest"] == manifest["batch_id"] and detail.get("business_signature") == signature
                        and detail.get("storage") == storage, 'The same batch has different business files or an S3 target; cloud records were not overwritten')
                completed = detail.get("completed_datasets", [])
            else:
                detail = {"flow": FLOW, "batch_id": manifest["batch_id"], "manifest_sha256": manifest_sha,
                          "upload_receipt_sha256": upload_sha, "business_signature": signature, "storage": storage,
                          "completed_datasets": [], "table_insert_counts": {}}
                conn.execute("""INSERT INTO core.ingest_run(id,input_digest,code_sha256,schema_sha256,holes,status,detail)
                    VALUES(%s,%s,%s,%s,%s,'running',%s)""", (run_id, manifest["batch_id"], file_info(Path(__file__), "code")["sha256"],
                    digest(manifest["identity"]["schema_migrations"]), Jsonb(sorted({item["source_hole_id"] for item in manifest["datasets"]})), Jsonb(detail)))
            expected = manifest["identity"]["datasets"]
            require(all(item in expected for item in completed) and len({item["dataset_id"] for item in completed}) == len(completed), 'The cloud resume record covers a different scope')
            save_progress(receipt_path, identity, completed, started_at=now())
            already_done = existing and existing["status"] == "staged"
            if already_done:
                require(sorted(completed, key=lambda item: item["dataset_id"]) == expected and detail.get("release_id"), 'The cloud completion record is incomplete')
                release = detail["release_id"]
                members_of(conn, release)
            else:
                update_run(conn, run_id, "running", detail)
                pending = [item for item in expected if item not in completed]
                if pending:
                    load_staging(conn, directory, manifest)
                    check_staging_scope(conn, manifest)
                for item in pending:
                    revision = item["dataset_revision_id"]
                    with conn.transaction():
                        check_source_identity(conn, revision)
                        counts = {table: insert_table(conn, table, revision, manifest) for table in DATA_TABLES}
                        register_assets(conn, revision, manifest, objects, root_key)
                        record_validation(conn, validations[revision])
                        next_completed = completed + [item]
                        next_detail = {**detail, "completed_datasets": next_completed,
                            "table_insert_counts": {**detail["table_insert_counts"], revision: counts}}
                        update_run(conn, run_id, "running", next_detail)
                    completed, detail = next_completed, next_detail
                    save_progress(receipt_path, identity, completed, last_committed_revision=revision)
                with conn.transaction():
                    members, sources, replacements = accumulated_members(conn, expected)
                    release = stage_candidate(conn, manifest, members, sources, composites)
                    detail = {**detail, "release_id": release, "replacements": replacements,
                              "completed_datasets": completed, "candidate_member_count": len(members)}
                    update_run(conn, run_id, "staged", detail)
            committed = True
            finalized, switched = None, False
            if selection is not None:
                with conn.transaction():
                    finalized, switched = finalize_release(conn, manifest, selection, [release], composites)
            result = {**identity, "status": "complete", "datasets": sorted(completed, key=lambda item: item["dataset_id"]),
                      "release_id": release, "finalized_release_id": finalized, "active_release_changed": switched,
                      "replacements": detail.get("replacements", []), "completed_at": now(),
                      "database_commit": "complete", "reused": bool(already_done)}
            write_json(receipt_path, result)
            return result
        except BaseException as error:
            if detail is not None and not committed:
                try:
                    update_run(conn, run_id, "failed", {**detail, "error_type": type(error).__name__})
                except Exception:
                    pass
            try:
                save_progress(receipt_path, identity, completed, error_type=type(error).__name__,
                              database_commit="complete" if committed else "partial")
            except OSError:
                pass
            raise
        finally:
            try:
                conn.execute("SELECT pg_advisory_unlock(%s)", (LOCK_KEY,))
            except Exception:
                pass


def main():
    parser = StepParser(description=__doc__.splitlines()[0], epilog='Common directory arguments are retained for consistent numbered entry points; the cloud connection uses only --cloud-dsn-env, not --dbname or --db-config.')
    parser.add_argument("--export-dir", type=Path, required=True, help='Batch directory from step 38, or the same structured package downloaded from S3')
    parser.add_argument("--upload-receipt", type=Path, help='Complete receipt from step 39; defaults to the export directory')
    parser.add_argument("--receipt", type=Path, help='Progress and completion receipt; defaults to merge_receipt.json')
    parser.add_argument("--cloud-dsn-env", default="ETL4_CLOUD_DSN", help='Environment variable name for the independent cloud connection; do not pass a password')
    parser.add_argument("--apply-migrations", action="store_true", help='Explicitly allow adding missing migrations recognized by the current code')
    parser.add_argument("--s3-root-key", help='S3 location configuration key; defaults to a value derived from bucket/region/prefix')
    parser.add_argument("--finalize", type=Path, help='Explicit final revision selection JSON; the active release is not switched when omitted')
    args = parser.parse_args()
    result = merge_batch(args.export_dir, upload_receipt=args.upload_receipt, receipt_path=args.receipt,
                         dsn_environment=args.cloud_dsn_env, apply_migrations=args.apply_migrations,
                         root_key=args.s3_root_key, finalize=args.finalize)
    print(f"Cloud batch commit complete: {len(result['datasets'])} datasets; candidate release {result['release_id']}")
    if result["finalized_release_id"]:
        print(f"Final release explicitly selected: {result['finalized_release_id']}")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(str(error) if isinstance(error, ValueError) else f"Cloud merge incomplete: {type(error).__name__}", file=sys.stderr)
        raise SystemExit(1)

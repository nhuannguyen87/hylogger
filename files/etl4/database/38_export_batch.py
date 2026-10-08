"""Export complete boreholes that passed both local acceptances as an independent batch without copying large files.
把已通过两次本地验收的完整孔导出为独立批次，不复制大文件。
"""

import gzip
import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from psycopg import sql
from _batch_files import (
    FORMAT, DATA_TABLES, EVIDENCE_TABLES, RELEASE_TABLES, ADMIN_TABLES, KNOWN_TABLES,
    checksum, file_info, load_export, object_key, write_json,
)
from _db_common import CODE, connection
from _db_files import digest, encoded, read
from _run_reports import main_guard
from _settings import StepParser, configure, local_asset_path, resolve_reference, settings


REVISION_TABLES = {
    "sample_axis", "spectral_stream", "interpretation_set", "interpretation_input",
    "metric_definition", "scan_log", "log_axis_binding", "asset", "log_asset",
    "data_chunk", "image_frame", "source_document", "source_reference", "quality_issue",
    "revision_validation", "dataset_segment", "spectral_array", "spectral_sample_map",
    "image_region", "image_sample_mapping", "image_region_asset",
}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def plain(value):
    """Convert UUIDs and dates for JSON only; do not create new business identifiers.
    UUID 和日期转换只影响 JSON 表示，不生成新的业务标识。
    """
    return json.loads(encoded(value))


def export_check_summary(value):
    """Keep acceptance results and counts in delivery reports; omit examples containing local asset paths.
    本机读取示例带本地资产地址，交付报告只保留验收结论和计数。
    """
    if isinstance(value, dict):
        return {key: export_check_summary(item) for key, item in value.items() if key != "example_selections"}
    if isinstance(value, list):
        return [export_check_summary(item) for item in value]
    return value


def read_report(path):
    """Read each small report once for both its content and source digest.
    报告内容和来源摘要使用同一次小文件读取。
    """
    data = Path(path).read_bytes()
    value = json.loads(data.decode("utf-8-sig"))
    value["export_evidence"] = {"source_report_sha256": hashlib.sha256(data).hexdigest()}
    return value


def read_acceptances(path):
    second = read_report(path)
    members = second.get("dataset_contents", {})
    require(second.get("stage") == 2 and second.get("status") == "passed",
            'The second local acceptance has not passed')
    require(second.get("database") == settings.dbname, 'The database in the second acceptance does not match --dbname')
    require(members and second.get("content_sha256") == digest(members), 'The content digest in the second acceptance does not match')
    require(second.get("prepared_acceptances"), 'The second acceptance does not reference first-stage reports; rerun the updated step 37')
    revisions = [item["dataset_revision_id"] for item in members.values()]
    require(len(set(revisions)) == len(revisions), 'The second acceptance contains duplicate revisions')
    prepared, reports = {}, {}
    for link in second["prepared_acceptances"]:
        first = read_report(resolve_reference(link["report_path"]))
        content = first.get("content", {})
        key = link["package_sha256"]
        require(checksum(key), 'The first-stage prepared package digest is invalid')
        require(first.get("stage") == 1 and first.get("status") == "passed"
                and content.get("package_sha256") == key
                and first.get("content_sha256") == link["content_sha256"] == digest(content),
                'The first-stage acceptance status or digest does not match')
        require(key not in reports, 'The second acceptance references the same prepared package more than once')
        reports[key] = first
        for dataset, item in content.get("datasets", {}).items():
            require(dataset not in prepared, 'The first-stage reports contain duplicate datasets')
            prepared[dataset] = item
    require(set(prepared) == set(members), 'The two acceptances cover different datasets')
    return second, reports, prepared


def inspect_schema(conn):
    tables = {row["table_name"] for row in conn.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema='core' AND table_type='BASE TABLE'"
    ).fetchall()}
    require(tables == KNOWN_TABLES,
            f"The table scope changed; update the export format. Added: {sorted(tables - KNOWN_TABLES)}; missing: {sorted(KNOWN_TABLES - tables)}")
    columns = conn.execute("""SELECT c.relname table_name,a.attname column_name,
        format_type(a.atttypid,a.atttypmod) data_type,NOT a.attnotnull nullable
        FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid
        JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='core' AND c.relkind='r' AND a.attnum>0 AND NOT a.attisdropped
        ORDER BY c.relname,a.attnum""").fetchall()
    keys = conn.execute("""SELECT c.relname table_name,
        array_agg(a.attname ORDER BY k.ordinality) primary_key
        FROM pg_constraint p JOIN pg_class c ON c.oid=p.conrelid
        JOIN pg_namespace n ON n.oid=c.relnamespace
        CROSS JOIN LATERAL unnest(p.conkey) WITH ORDINALITY k(attnum,ordinality)
        JOIN pg_attribute a ON a.attrelid=c.oid AND a.attnum=k.attnum
        WHERE n.nspname='core' AND p.contype='p' GROUP BY c.relname""").fetchall()
    result = {table: {"columns": [], "primary_key": []} for table in sorted(tables)}
    for row in columns:
        result[row["table_name"]]["columns"].append({k: row[k] for k in ("column_name", "data_type", "nullable")})
    for row in keys:
        result[row["table_name"]]["primary_key"] = list(row["primary_key"])
    require(all(item["columns"] and item["primary_key"] for item in result.values()), 'An exported table is missing column or primary key definitions')
    migrations = plain(conn.execute("SELECT name,sha256 FROM core.schema_migration ORDER BY name").fetchall())
    expected = [{"name": path.name, "sha256": file_info(path, "schema")["sha256"]}
                for path in sorted((CODE / "migrations").glob("*.sql"))]
    require(migrations == expected, 'Database migrations do not match the local code')
    return {"migrations": migrations, "tables": result,
            "geometry": {"borehole_revision.collar_geom": "hex EWKB, includes SRID; restore with ST_GeomFromEWKB(decode(value,'hex'))"},
            "restore_notes": ["Use one transaction; core_interval sample foreign keys are deferred.",
                              "Insert tray intervals before sections and then scan_sample.",
                              "Insert borehole_branch parents before children.",
                              "Rebuild releases and composites for the merged destination release."]}


def select_scope(conn, second, prepared):
    members = second["dataset_contents"]
    revision_ids = sorted(item["dataset_revision_id"] for item in members.values())
    rows = plain(conn.execute("""SELECT r.*,d.source_dataset_id,b.source_hole_id
        FROM core.dataset_revision r JOIN core.dataset d ON d.id=r.dataset_id
        JOIN core.borehole b ON b.id=r.borehole_id WHERE r.id=ANY(%s::uuid[]) ORDER BY r.dataset_id""",
        (revision_ids,)).fetchall())
    require(len(rows) == len(members), 'A revision referenced by the acceptance is missing from the database')
    for row in rows:
        expected = members.get(row["dataset_id"])
        require(expected == {"dataset_revision_id": row["id"], "input_digest": row["input_digest"]},
                'The accepted revision or content does not match the database')
        original = prepared[row["dataset_id"]]
        metadata = row["normalized_metadata"]
        raw = sorted([a["source_relative_path"], a["byte_size"], a["sha256"]] for a in original["raw_assets"])
        require(metadata.get("prepared_revision_id") == original["source_revision_id"]
                and metadata.get("final_identity", {}).get("raw_files") == raw
                and metadata.get("content_sha256") == row["input_digest"],
                'The first-stage input does not match the final revision; rerun step 37')
    holes = sorted({row["borehole_id"] for row in rows})
    published = plain(conn.execute("""SELECT rd.dataset_id,rd.dataset_revision_id
        FROM core.release_dataset rd JOIN core.dataset_revision r ON r.id=rd.dataset_revision_id
        WHERE rd.release_id=%s AND r.borehole_id=ANY(%s::uuid[]) ORDER BY rd.dataset_id""",
        (second["release_id"], holes)).fetchall())
    expected = [{"dataset_id": key, "dataset_revision_id": members[key]["dataset_revision_id"]} for key in sorted(members)]
    require(published == expected, 'Export complete boreholes: this batch does not match all datasets for these boreholes in the accepted release')
    validations = plain(conn.execute("SELECT * FROM core.revision_validation WHERE dataset_revision_id=ANY(%s::uuid[]) ORDER BY dataset_revision_id",
                                     (revision_ids,)).fetchall())
    by_revision = {row["dataset_revision_id"]: row for row in validations}
    for row in rows:
        check = by_revision.get(row["id"], {})
        require(check.get("input_digest") == row["input_digest"] and check.get("result", {}).get("status") == "passed",
                'The database revision acceptance record is missing or does not match')
    datasets = [{"dataset_id": row["dataset_id"], "dataset_revision_id": row["id"],
                 "input_digest": row["input_digest"], "borehole_id": row["borehole_id"],
                 "source_hole_id": row["source_hole_id"], "source_dataset_id": row["source_dataset_id"],
                 "processing_rules": {key: row["normalized_metadata"]["final_identity"].get(key)
                                      for key in ("preparation_rules", "media_input_key", "final_rules")}}
                for row in rows]
    return {"revisions": revision_ids, "holes": holes, "datasets": datasets,
            "borehole_revisions": sorted({row["borehole_revision_id"] for row in rows}),
            "dataset_ids": sorted(members), "validations": validations}


def selected_decisions(value, hole_names):
    result = dict(value or {})
    result["orders"] = {key: order for key, order in result.get("orders", {}).items() if key in hole_names}
    return result


def read_composites(conn, release, scope):
    composites = plain(conn.execute("SELECT * FROM core.borehole_composite WHERE release_id=%s AND borehole_id=ANY(%s::uuid[]) ORDER BY borehole_id",
                                    (release, scope["holes"])).fetchall())
    require({item["borehole_id"] for item in composites} == set(scope["holes"]), 'Selected boreholes are missing 3D display composites')
    parts = plain(conn.execute("SELECT * FROM core.borehole_composite_part WHERE release_id=%s AND borehole_id=ANY(%s::uuid[]) ORDER BY borehole_id,sequence_no",
                              (release, scope["holes"])).fetchall())
    require({p["dataset_revision_id"] for p in parts} == set(scope["revisions"]), 'The 3D composites contain out-of-scope revisions or omit selected revisions')
    relation_ids = sorted({part["predecessor_relation_id"] for part in parts if part["predecessor_relation_id"]})
    relations = plain(conn.execute("SELECT * FROM core.dataset_succession WHERE id=ANY(%s::uuid[]) ORDER BY id",
                                  (relation_ids,)).fetchall())
    require(len(relations) == len(relation_ids) and all(
        r["predecessor_revision_id"] in scope["revisions"] and r["successor_revision_id"] in scope["revisions"]
        for r in relations), 'The 3D succession relationships extend beyond this batch')
    source = conn.execute("SELECT manifest FROM core.data_release WHERE id=%s", (release,)).fetchone()
    require(source is not None, 'The accepted release does not exist')
    hole_names = {row["source_hole_id"] for row in scope["datasets"]}
    decisions = selected_decisions(source["manifest"].get("succession_decisions", {}), hole_names)
    business_decisions = {key: decisions[key] for key in (
        "orders", "contract", "switch_depth_rule", "display_2d", "display_3d",
        "missing_values", "return_to_predecessor") if key in decisions}
    # Keep original records and UUIDs as source evidence; derive batch identity only from the selected boreholes' business definitions. / 原记录及其 UUID 作为来源依据保留；批次身份只取所选孔的业务定义。
    definition = {
        "composites": [{k: v for k, v in item.items() if k not in ("id", "release_id", "definition_sha256", "evidence")}
                       for item in composites],
        "parts": [{k: v for k, v in item.items() if k not in ("composite_id", "release_id", "predecessor_relation_id")}
                  for item in parts],
        "relations": sorted([{k: v for k, v in item.items() if k not in ("id", "confirmation_basis")}
                             for item in relations], key=lambda item: (item["borehole_id"], item["predecessor_revision_id"], item["successor_revision_id"])),
        "decisions": business_decisions,
    }
    scope["relations"] = relation_ids
    return {"source_release_id": release, "composites": composites, "parts": parts,
            "succession": relations, "decisions": decisions, "definition": definition}


def where_clause(table, scope):
    if table in REVISION_TABLES:
        return "t.dataset_revision_id=ANY(%s::uuid[])", (scope["revisions"],)
    if table in ("core_interval", "scan_sample"):
        return "t.axis_id IN (SELECT id FROM core.sample_axis WHERE dataset_revision_id=ANY(%s::uuid[]))", (scope["revisions"],)
    if table == "spectral_block":
        return "t.spectral_array_id IN (SELECT id FROM core.spectral_array WHERE dataset_revision_id=ANY(%s::uuid[]))", (scope["revisions"],)
    if table == "asset_location":
        return "t.asset_id IN (SELECT id FROM core.asset WHERE dataset_revision_id=ANY(%s::uuid[]))", (scope["revisions"],)
    if table in ("borehole_branch", "survey_station"):
        return "t.borehole_id=ANY(%s::uuid[])", (scope["holes"],)
    key = {"borehole": "holes", "borehole_revision": "borehole_revisions", "dataset": "dataset_ids",
           "dataset_revision": "revisions", "dataset_succession": "relations"}.get(table)
    require(key is not None, f"No batch scope is defined for {table}")
    return "t.id=ANY(%s::uuid[])", (scope[key],)


def export_table(conn, table, schema, scope, destination):
    description = schema["tables"][table]
    columns = [item["column_name"] for item in description["columns"]]
    selected = [sql.SQL("encode(ST_AsEWKB(t.collar_geom),'hex') AS collar_geom")
                if table == "borehole_revision" and name == "collar_geom"
                else sql.SQL("t.{}").format(sql.Identifier(name)) for name in columns]
    order = sql.SQL(",").join(sql.SQL("t.{}").format(sql.Identifier(name)) for name in description["primary_key"])
    if table == "core_interval":
        order = sql.SQL("t.axis_id,CASE WHEN t.interval_kind='tray' THEN 0 ELSE 1 END,t.ordinal,t.id")
    condition, parameters = where_clause(table, scope)
    query = sql.SQL("SELECT row_to_json(q)::text row_json FROM (SELECT {} FROM core.{} t WHERE {} ORDER BY {}) q").format(
        sql.SQL(",").join(selected), sql.Identifier(table), sql.SQL(condition), order)
    destination.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with destination.open("wb") as output, gzip.GzipFile(filename="", mode="wb", fileobj=output, mtime=0) as zipped:
        with conn.cursor(name="export_" + table) as cursor:
            cursor.itersize = 2000
            cursor.execute(query, parameters)
            for row in cursor:
                zipped.write(row["row_json"].encode("utf-8") + b"\n")
                count += 1
    return {"path": "tables/" + destination.name, "rows": count, "columns": columns,
            "primary_key": description["primary_key"], "encoding": "postgres-jsonl-gzip",
            **{key: value for key, value in file_info(destination, "table").items() if key != "kind"}}


def select_assets(conn, scope, release, asset_manifest_path):
    assets = plain(conn.execute("SELECT * FROM core.asset WHERE dataset_revision_id=ANY(%s::uuid[]) ORDER BY id",
                                (scope["revisions"],)).fetchall())
    locations = plain(conn.execute("""SELECT l.* FROM core.asset_location l JOIN core.asset a ON a.id=l.asset_id
        WHERE a.dataset_revision_id=ANY(%s::uuid[]) ORDER BY l.id""", (scope["revisions"],)).fetchall())
    published = read(asset_manifest_path)
    require(published.get("release_id") == release, 'The release-specific asset manifest does not match the accepted release')
    expected = {item["id"]: item for item in published.get("assets", []) if item["dataset_revision_id"] in scope["revisions"]}
    require(set(expected) == {item["id"] for item in assets}, "The asset manifest does not match this batch's database assets")
    by_asset = {}
    for location in locations:
        by_asset.setdefault(location["asset_id"], []).append(location)
    public, private = [], {}
    for asset in assets:
        require(all(asset[key] == expected[asset["id"]][key] for key in ("sha256", "byte_size", "dataset_revision_id", "media_type")),
                'The asset manifest content identity does not match the database')
        key = object_key(asset["sha256"])
        record = {"asset_id": asset["id"], **{name: asset[name] for name in ("dataset_revision_id", "sha256", "byte_size", "media_type")},
                  "object_key": key}
        found = None
        for location in by_asset.get(asset["id"], []):
            if location["backend"] != "local" or location["verified_sha256"] != asset["sha256"]:
                continue
            try:
                path = local_asset_path(location)
                if path.is_file() and path.stat().st_size == asset["byte_size"]:
                    with path.open("rb") as stream:
                        stream.read(1)
                    found = path
                    break
            except (OSError, ValueError):
                continue
        require(found is not None, f"This batch asset has no readable local file: {asset['id']}")
        source = {"path": str(found.resolve()), "sha256": asset["sha256"], "byte_size": asset["byte_size"]}
        require(key not in private or all(private[key][name] == source[name] for name in ("sha256", "byte_size")), 'The same object key refers to different file identities')
        private.setdefault(key, source)
        public.append(record)
    return public, private


def export_identity(schema, scope, composites):
    return {"format": FORMAT, "schema_migrations": {item["name"]: item["sha256"] for item in schema["migrations"]},
            "datasets": [{key: item[key] for key in ("dataset_id", "dataset_revision_id", "input_digest")}
                         for item in sorted(scope["datasets"], key=lambda item: item["dataset_id"])],
            "composite_definition_sha256": digest(composites["definition"])}


def add_json_file(folder, files, relative, value, kind):
    path = folder / relative
    write_json(path, value)
    files[relative] = file_info(path, kind)


def stable_export_content(manifest):
    return {"identity": manifest["identity"], "tables": manifest["tables"], "assets": manifest["assets"],
            "files": {name: value for name, value in manifest["files"].items() if value["kind"] not in ("evidence", "source_composites")}}


def finish_export(temporary, parent, manifest, sources):
    destination = parent / manifest["batch_id"]
    if destination.exists():
        previous, previous_sha = load_export(destination)
        require(stable_export_content(previous) == stable_export_content(manifest),
                'The same batch identity produced different business content; check inputs or processing rules. The existing export was not overwritten')
        write_json(destination / "local_sources.json", {"batch_id": manifest["batch_id"],
                   "manifest_sha256": previous_sha, "objects": sources})
        return destination, True
    write_json(temporary / "manifest.json", manifest)
    manifest_sha = file_info(temporary / "manifest.json", "manifest")["sha256"]
    write_json(temporary / "local_sources.json", {"batch_id": manifest["batch_id"],
               "manifest_sha256": manifest_sha, "objects": sources})
    # Publish the directory only after all files are complete; never replace an existing complete directory. / 完成全部文件后才发布目录；已有完整目录从不被替换。
    temporary.rename(destination)
    return destination, False


def export_batch(acceptance_path=None, output_parent=None):
    acceptance_path = Path(acceptance_path or settings.reports_dir / "local_acceptance_2.json").resolve()
    second, first_reports, prepared = read_acceptances(acceptance_path)
    second_sha = second["export_evidence"]["source_report_sha256"]
    parent = Path(output_parent or settings.database_dir / "exports").resolve()
    parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".export-", dir=parent))
    try:
        with connection("reader") as conn:
            with conn.transaction():
                conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                conn.execute("SET LOCAL TIME ZONE 'UTC'")
                conn.execute("SET LOCAL DateStyle TO 'ISO, YMD'")
                conn.execute("SET LOCAL extra_float_digits TO 3")
                actual_db = conn.execute("SELECT current_database() database").fetchone()["database"]
                require(actual_db == second["database"], 'The connected database does not match the second acceptance')
                schema = inspect_schema(conn)
                scope = select_scope(conn, second, prepared)
                composites = read_composites(conn, second["release_id"], scope)
                identity = export_identity(schema, scope, composites)
                batch_id = digest(identity)
                tables, files = {}, {}
                for table in DATA_TABLES:
                    tables[table] = export_table(conn, table, schema, scope, temporary / "tables" / (table + ".jsonl.gz"))
                    files[tables[table]["path"]] = file_info(temporary / tables[table]["path"], "table")
                assets, sources = select_assets(conn, scope, second["release_id"],
                                                acceptance_path.parent / f"asset_manifest_{second['release_id']}.json")
                add_json_file(temporary, files, "evidence/revision_validation.json", {
                    "source_rows_sha256": digest(scope["validations"]),
                    "omitted": "example_selections: local read examples are not cloud asset addresses",
                    "rows": export_check_summary(scope["validations"])}, "evidence")
        for migration in schema["migrations"]:
            relative = "schema/" + migration["name"]
            target = temporary / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(CODE / "migrations" / migration["name"], target)
            files[relative] = file_info(target, "schema")
            require(files[relative]["sha256"] == migration["sha256"], 'A migration file changed during export; export again')
        public_second = export_check_summary(second)
        public_second["export_evidence"] = {"source_report_sha256": second_sha,
            "omitted": "example_selections: local read examples are not cloud asset addresses"}
        public_second["prepared_acceptances"] = []
        for link in second["prepared_acceptances"]:
            relative = "evidence/acceptance_1_" + link["package_sha256"] + ".json"
            add_json_file(temporary, files, relative, first_reports[link["package_sha256"]], "evidence")
            public_second["prepared_acceptances"].append({**link, "report_path": relative})
        add_json_file(temporary, files, "evidence/acceptance_2.json", public_second, "evidence")
        add_json_file(temporary, files, "composites.json", composites, "source_composites")
        manifest = {"format": FORMAT, "batch_id": batch_id, "identity": identity,
                    "schema": schema, "datasets": scope["datasets"], "tables": tables,
                    "files": files, "assets": assets,
                    "excluded_tables": {"administration": list(ADMIN_TABLES), "release_rebuilt_on_merge": list(RELEASE_TABLES),
                                        "validation_evidence": ["revision_validation"], "local_sources_only": ["asset_location"]}}
        destination, reused = finish_export(temporary, parent, manifest, sources)
        return {"status": "passed", "batch_id": batch_id, "directory": str(destination),
                "datasets": len(scope["datasets"]), "holes": len(scope["holes"]), "assets": len(assets), "reused": reused}
    finally:
        # Remove only the temporary directory created by this mkdtemp call that remains inside the output parent directory. / 只清理本次 mkdtemp 创建、且仍在输出父目录中的临时目录。
        if temporary.exists() and temporary.parent.resolve() == parent and temporary.name.startswith(".export-"):
            shutil.rmtree(temporary)


def main():
    parser = StepParser(description=__doc__.splitlines()[0])
    parser.add_argument("--acceptance", type=Path, help='Second local acceptance report; defaults to the latest report in this work directory')
    parser.add_argument("--output-dir", type=Path, help='Export parent directory; output is placed in a subdirectory named after the batch digest')
    args = parser.parse_args()
    configure(args)
    result = export_batch(args.acceptance, args.output_dir)
    print(f"Batch exported: {result['directory']}; {result['holes']} boreholes, {result['datasets']} datasets; large files were not copied")


if __name__ == "__main__":
    main_guard(main)

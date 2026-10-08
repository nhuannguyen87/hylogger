"""Import a complete local batch from packages that passed the first acceptance, then run the second acceptance.
正式本地批次入口：读取第一次验收的准备包，完整入库并进行第二次验收。
"""

import json
import sys
import time
import uuid
from pathlib import Path

from psycopg.types.json import Jsonb

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _db_common import LOCK_KEY, assert_schema, connection, insert, schema_hash
from _db_files import digest, encoded, read, sha, write
from _file_checks import fresh_file_checks
from _final_import import FINAL_RULE_VERSION, import_final_dataset, load_final_inputs
from _media_common import require
from _media_verification import verify_dataset
from _multi_db import install_composites, verify_composites
from _publication import publish_release, release_identity, release_members, stage_release
from _run_reports import main_guard
from _settings import StepParser, configure, settings


def release_plan(conn, batch, items, previous):
    """When appending, retain the explicit source release and replace the members in this batch.
    追加时沿用明确的来源发布，再替换本批成员。
    """
    members = {}
    source_release = batch.get("source_release_id")
    if source_release:
        for member in release_members(conn, source_release):
            members[str(member["dataset_id"])] = member
    for item in items:
        members[item["dataset_id"]] = {
            "dataset_id": item["dataset_id"], "dataset_revision_id": item["dataset_revision_id"],
        }
    decisions = batch.get("decisions", {"orders": {}})
    metadata = {"flow": FINAL_RULE_VERSION, "succession_decisions": decisions}
    planned_release, _ = release_identity(list(members.values()), metadata)
    if source_release:
        require(previous in (source_release, planned_release), "The source release for the incremental batch has changed")
    return list(members.values()), metadata


def verify_final_dataset(conn, item):
    """Save acceptance results and data in one transaction so that any failure rolls both back.
    验收结果与数据在同一事务中保存，失败时一起回滚。
    """
    check = verify_dataset(
        conn, item["unit"], item, None, item["package"],
        storage=item["context"]["storage"], source_context=item["context"],
        content_digest=item["input_digest"],
    )
    check.update(status="passed", dataset_revision_id=item["dataset_revision_id"],
                 input_digest=item["input_digest"])
    conn.execute(
        """INSERT INTO core.revision_validation
        (dataset_revision_id,input_digest,verification_code_sha256,result) VALUES (%s,%s,%s,%s)
        ON CONFLICT(dataset_revision_id) DO UPDATE SET validated_at=now(),
        input_digest=EXCLUDED.input_digest,verification_code_sha256=EXCLUDED.verification_code_sha256,
        result=EXCLUDED.result""",
        (item["dataset_revision_id"], item["input_digest"], sha(Path(__file__)), Jsonb(json.loads(encoded(check)))),
    )
    return check


def publish_final_batch(conn, members, metadata, checks, previous, run_id, results):
    """Commit display assemblies and the release pointer only after every member passes.
    全部成员通过后，再提交展示组合和发布指针。
    """
    decisions = metadata["succession_decisions"]
    with conn.transaction():
        release = stage_release(conn, members, "single_final", metadata)
        install_composites(conn, release, decisions)
        verify_composites(conn, release, decisions)
        publish_release(conn, release, checks, __file__, expected_previous=previous, update_latest=False)
        conn.execute(
            "UPDATE core.ingest_run SET status='staged',finished_at=now(),detail=%s WHERE id=%s",
            (Jsonb({"status": "passed", "release_id": release, "datasets": results}), run_id),
        )
    # 最新清单可重建；此时仍持有导入锁，避免覆盖另一批的入口。 / The latest manifest can be rebuilt; keep the import lock to avoid overwriting another batch entry.
    try:
        write(settings.reports_dir / "deployment_asset_manifest.json",
              read(settings.reports_dir / f"asset_manifest_{release}.json"))
    except OSError as error:
        print(f"Final version committed; the latest manifest entry needs rebuilding: {type(error).__name__}", flush=True)
    return release


def import_checked_batch(batch, *, fail_after_samples=False):
    """Read the batch, import and verify each dataset, then publish and save the run report.
    读取本批，逐个入库并验收，最后发布并保存运行报告。
    """
    require(settings.dbname != "etl4_core", "Use an explicitly selected local batch database; do not overwrite the previous delivery database")
    started = time.perf_counter()
    items = load_final_inputs(batch)
    run_id = str(uuid.uuid4())
    results, checks = [], []
    with connection("ingest") as conn:
        assert_schema(conn)
        require(conn.execute("SELECT pg_try_advisory_lock(%s) ok", (LOCK_KEY,)).fetchone()["ok"],
                "Another import or acceptance is running")
        try:
            insert(conn, "ingest_run", {
                "id": run_id, "input_digest": digest([item["dataset_revision_id"] for item in items]),
                "code_sha256": sha(Path(__file__)), "schema_sha256": schema_hash(),
                "holes": Jsonb(sorted({item["unit"]["hole_id"] for item in items})), "status": "running",
            })
            current = conn.execute("SELECT release_id FROM core.active_release WHERE singleton").fetchone()
            previous = str(current["release_id"]) if current else None
            members, metadata = release_plan(conn, batch, items, previous)

            for item in items:
                print(f"Importing and verifying {item['unit']['hole_id']} / {item['unit']['source_dataset_id']}", flush=True)
                with conn.transaction():
                    imported = import_final_dataset(conn, item, fail_after_samples)
                    check = verify_final_dataset(conn, item)
                results.append(imported)
                checks.append(check)

            release = publish_final_batch(conn, members, metadata, checks, previous, run_id, results)
        except Exception as error:
            conn.execute(
                "UPDATE core.ingest_run SET status='failed',finished_at=now(),detail=%s WHERE id=%s",
                (Jsonb({"error_type": type(error).__name__, "message": str(error), "completed_datasets": results}), run_id),
            )
            raise
        finally:
            conn.execute("SELECT pg_advisory_unlock(%s)", (LOCK_KEY,))

    result = {
        "status": "passed", "run_id": run_id, "release_id": release,
        "database": settings.dbname, "datasets": results, "checks": checks,
        "dataset_contents": {item["dataset_id"]: {
            "dataset_revision_id": item["dataset_revision_id"], "input_digest": item["input_digest"]
        } for item in items},
        "prepared_acceptances": list({
            item["prepared_acceptance"]["package_sha256"]: item["prepared_acceptance"]
            for item in items
        }.values()),
        "elapsed_seconds": time.perf_counter() - started,
    }
    write(settings.reports_dir / f"final_run_{run_id}.json", result)
    return result


@fresh_file_checks
def run_final_import(batch, *, fail_after_samples=False):
    """Run the second local acceptance and prevent failed batches from being exported as successful.
    第二次本地验收：失败结果不能被后续导出当成成功批次。
    """
    path = settings.reports_dir / "local_acceptance_2.json"
    write(path, {"stage": 2, "status": "running"})
    try:
        result = import_checked_batch(batch, fail_after_samples=fail_after_samples)
    except Exception as error:
        write(path, {"stage": 2, "status": "failed", "error_type": type(error).__name__})
        raise
    acceptance = {"stage": 2, "status": "passed", "run_id": result["run_id"],
                  "release_id": result["release_id"], "database": result["database"],
                  "datasets": result["datasets"], "checks": result["checks"],
                  "dataset_contents": result["dataset_contents"],
                  "prepared_acceptances": result["prepared_acceptances"],
                  "content_sha256": digest(result["dataset_contents"])}
    write(settings.reports_dir / f"local_acceptance_2_{result['run_id']}.json", acceptance)
    write(path, acceptance)
    return result


def main():
    parser = StepParser(description=__doc__.splitlines()[0])
    parser.add_argument("--batch", type=Path, required=True, help="Package paths, preparation directories, and segment decisions for multiple datasets")
    args = parser.parse_args()
    configure(args)
    result = run_final_import(read(args.batch))
    print(f"Final release: {result['release_id']}; {len(result['datasets'])} datasets in this batch")


if __name__ == "__main__":
    main_guard(main)

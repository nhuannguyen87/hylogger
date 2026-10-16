"""Prepare the manifest, then record validation and switch the release pointer in a transaction.

发布流程：先准备清单，再在事务中登记验证并切换指针。
"""

import json
from pathlib import Path
from psycopg.types.json import Jsonb
from _db_common import ETL, insert, pick, schema_hash, uid
from _db_files import digest, encoded, inside, read, sha, write
from _settings import settings, local_asset_path


def release_identity(members, metadata=None):
    """Derive release identity from explicit members and the business manifest to check resume targets before writing.

    发布身份由明确成员和业务清单决定，允许在写入前核对续跑目标。
    """
    members = json.loads(encoded(members))
    members = sorted(
        [pick(item, "dataset_id", "dataset_revision_id") for item in members],
        key=lambda item: str(item["dataset_id"]),
    )
    if not members or len({str(item["dataset_id"]) for item in members}) != len(members):
        raise ValueError("A release must contain a nonempty list of distinct datasets")
    manifest = {"dataset_revisions": members, "schema_sha256": schema_hash(), **(metadata or {})}
    release = uid("release-v2", digest(manifest))
    return release, manifest


def stage_release(conn, members, kind, metadata=None):
    release, manifest = release_identity(members, metadata)
    members = manifest["dataset_revisions"]
    existing = conn.execute(
        "SELECT manifest_sha256 FROM core.data_release WHERE id=%s", (release,)
    ).fetchone()
    if existing:
        if existing["manifest_sha256"] != digest(manifest):
            raise ValueError("Release identity conflicts with the existing manifest")
        return release
    insert(
        conn,
        "data_release",
        {
            "id": release,
            "manifest_sha256": digest(manifest),
            "release_kind": kind,
            "manifest": manifest,
        },
    )
    for member in members:
        insert(conn, "release_dataset", {"release_id": release, **member})
    return release


def release_members(conn, release):
    row = conn.execute(
        "SELECT manifest,manifest_sha256 FROM core.data_release WHERE id=%s", (release,)
    ).fetchone()
    if row is None or digest(row["manifest"]) != row["manifest_sha256"]:
        raise ValueError("Release manifest is missing or its content does not match")
    members = conn.execute(
        """SELECT rd.dataset_id,rd.dataset_revision_id,r.input_digest
        FROM core.release_dataset rd JOIN core.dataset_revision r ON r.id=rd.dataset_revision_id
        WHERE rd.release_id=%s ORDER BY rd.dataset_id""",
        (release,),
    ).fetchall()
    expected = sorted(
        (str(x["dataset_id"]), str(x["dataset_revision_id"]))
        for x in row["manifest"]["dataset_revisions"]
    )
    actual = sorted((str(x["dataset_id"]), str(x["dataset_revision_id"])) for x in members)
    if not actual or actual != expected or len({x[0] for x in actual}) != len(actual):
        raise ValueError("Release members do not match the manifest")
    return members


def write_asset_manifest(conn, release):
    assets = conn.execute(
        """SELECT a.* FROM core.asset a JOIN core.release_dataset d
        ON d.dataset_revision_id=a.dataset_revision_id WHERE d.release_id=%s ORDER BY a.id""",
        (release,),
    ).fetchall()
    locations = conn.execute(
        """SELECT l.* FROM core.asset_location l JOIN core.asset a ON a.id=l.asset_id
        JOIN core.release_dataset d ON d.dataset_revision_id=a.dataset_revision_id
        WHERE d.release_id=%s ORDER BY l.id""",
        (release,),
    ).fetchall()
    by_id = {str(asset["id"]): asset for asset in assets}
    for location in locations:
        if location["backend"] == "local":
            asset = by_id[str(location["asset_id"])]
            path = local_asset_path(location)
            if path.stat().st_size != asset["byte_size"] or sha(path) != asset["sha256"]:
                raise ValueError("Content of an asset referenced by the release has changed: " + location["object_key"])
    manifest = {
        "release_id": str(release),
        "schema_sha256": schema_hash(),
        "assets": assets,
        "locations": locations,
        "deployment_performed": False,
        "object_storage_keys_are_relative": True,
        "runtime_credentials_included": False,
    }
    path = settings.reports_dir / f"asset_manifest_{release}.json"
    write(path, manifest)
    if read(path) != json.loads(encoded(manifest)):
        raise ValueError("The written asset manifest does not match")
    return assets, locations


def publish_release(conn, release, checks, verifier_file, activate=True, expected_previous=None, *, update_latest=True):
    """The caller holds the import lock; existing members also need validation matching their current content.

    调用方持有导入锁；已有成员也必须有匹配当前内容的验证记录。
    """
    members = release_members(conn, release)
    supplied = {str(item["dataset_revision_id"]): item for item in checks}
    known = {str(item["dataset_revision_id"]) for item in members}
    if set(supplied) - known:
        raise ValueError("Validation results contain revisions from another release")
    for member in members:
        revision = str(member["dataset_revision_id"])
        check = supplied.get(revision)
        if check is None:
            check = conn.execute(
                "SELECT input_digest,result FROM core.revision_validation WHERE dataset_revision_id=%s",
                (revision,),
            ).fetchone()
            if check is None or check["result"].get("status") != "passed":
                raise ValueError("A release member has not been validated")
        elif check.get("status") != "passed":
            raise ValueError("A release member failed validation")
        if check["input_digest"] != member["input_digest"]:
            raise ValueError("Validation results do not match the current data content")
    # 文件写入失败必须发生在活动指针改变之前。 / File write failures must occur before the active pointer changes.
    assets, locations = write_asset_manifest(conn, release)
    with conn.transaction():
        previous = conn.execute(
            "SELECT release_id FROM core.active_release WHERE singleton FOR UPDATE"
        ).fetchone()
        current = str(previous["release_id"]) if previous else None
        if (
            activate
            and expected_previous is not None
            and current not in (expected_previous, str(release))
        ):
            raise ValueError("The active release has changed; check the source release again")
        release_members(conn, release)
        for revision, check in supplied.items():
            conn.execute(
                """INSERT INTO core.revision_validation
                (dataset_revision_id,input_digest,verification_code_sha256,result) VALUES (%s,%s,%s,%s)
                ON CONFLICT(dataset_revision_id) DO UPDATE SET validated_at=now(),
                input_digest=EXCLUDED.input_digest,verification_code_sha256=EXCLUDED.verification_code_sha256,
                result=EXCLUDED.result""",
                (
                    revision,
                    check["input_digest"],
                    sha(Path(verifier_file)),
                    Jsonb(json.loads(encoded(check))),
                ),
            )
        if activate:
            conn.execute(
                """INSERT INTO core.active_release(singleton,release_id) VALUES(true,%s)
                ON CONFLICT(singleton) DO UPDATE SET release_id=EXCLUDED.release_id,switched_at=now()""",
                (release,),
            )
    if activate and update_latest:
        try:
            write(
                settings.reports_dir / "deployment_asset_manifest.json",
                read(settings.reports_dir / f"asset_manifest_{release}.json"),
            )
        except OSError as error:
            print(f"Release completed; the latest-manifest reference needs rebuilding: {type(error).__name__}", flush=True)
    return assets, locations

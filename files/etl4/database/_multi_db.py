"""Record the source release and build traceable display segments.
记录来源发布，并建立可追溯的展示取段。
"""

import hashlib
import json
from _db_common import connection, insert, pick, uid
from _db_files import digest, encoded, read, write
from _settings import settings
from _multi_common import require
from psycopg import sql


def sample_hash(conn, axis):
    h = hashlib.sha256()
    with conn.cursor().copy(
        "COPY (SELECT row_to_json(s)::text FROM core.scan_sample s WHERE axis_id="
        + sql.Literal(str(axis)).as_string(conn)
        + "::uuid ORDER BY sample_no) TO STDOUT"
    ) as cp:
        for block in cp:
            h.update(block)
    return h.hexdigest()


def load_or_record_source_release(source_release=None, work=None, dbname=None):
    work = settings.multi_work_dir if work is None else work
    path = work / "34_baseline.json"
    if path.exists():
        baseline = read(path)
        if source_release and source_release != baseline["release_id"]:
            raise ValueError("Use a separate work directory for a different source release")
        return baseline
    if not source_release:
        raise ValueError("First multi-dataset import requires --source-release")
    from _publication import release_members

    with connection("reader", dbname=dbname) as conn:
        release_members(conn, source_release)
        members = conn.execute(
            "SELECT * FROM core.release_dataset WHERE release_id=%s ORDER BY dataset_id",
            (source_release,),
        ).fetchall()
    baseline = {"release_id": source_release, "members": members}
    write(path, baseline)
    return baseline


def composite_rows(conn, release, decisions):
    records = conn.execute(
        """SELECT rd.dataset_id,rd.dataset_revision_id,d.source_dataset_id,d.borehole_id,
      b.source_hole_id,r.source_dataset_name,a.id axis_id,a.sample_count,a.depth_min_m,a.depth_max_m
      FROM core.release_dataset rd JOIN core.dataset d ON d.id=rd.dataset_id JOIN core.borehole b ON b.id=d.borehole_id
      JOIN core.dataset_revision r ON r.id=rd.dataset_revision_id JOIN core.sample_axis a ON a.dataset_revision_id=r.id
      WHERE rd.release_id=%s ORDER BY b.source_hole_id,d.source_dataset_id""",
        (release,),
    ).fetchall()
    holes = {}
    for r in records:
        holes.setdefault(r["source_hole_id"], []).append(r)
    output = []
    for hole, members in holes.items():
        if len(members) > 1:
            order = decisions["orders"][hole]
            require(
                set(order) == {r["source_dataset_id"] for r in members}
                and len(order) == len(members),
                "Confirmed order differs from dataset membership",
            )
            members = sorted(members, key=lambda r: order.index(r["source_dataset_id"]))
        require(
            all(a["depth_min_m"] <= b["depth_min_m"] for a, b in zip(members, members[1:])),
            "Nonmonotone successor start requires explicit segment review",
        )
        cid = uid("borehole-composite", release, members[0]["borehole_id"])
        definition = {
            "members": members,
            "decisions": decisions if len(members) > 1 else {"single_dataset": True},
        }
        composite = {
            "id": cid,
            "release_id": release,
            "borehole_id": members[0]["borehole_id"],
            "composition_kind": (
                "confirmed_successor_chain" if len(members) > 1 else "single_dataset"
            ),
            "boundary_rule": "successor_first_actual_sample_md_m",
            "missing_policy": "preserve_no_fallback",
            "trajectory_kind": "display_composite_not_surveyed_path",
            "definition_sha256": digest(definition),
            "evidence": {
                "hole_id": hole,
                "decisions": decisions if len(members) > 1 else {"single_dataset": True},
                "depth_units": "m",
                "range_semantics": "sample selection, not proof of continuous physical recovery",
            },
        }
        parts = []
        relations = []
        for i, r in enumerate(members):
            cut = members[i + 1]["depth_min_m"] if i + 1 < len(members) else None
            a = r["depth_min_m"]
            b = min(r["depth_max_m"], cut) if cut is not None else r["depth_max_m"]
            inclusive = cut is None or r["depth_max_m"] < cut
            chosen = conn.execute(
                """SELECT min(sample_no) a,max(sample_no) b,count(*) n FROM core.scan_sample
              WHERE axis_id=%s AND md_m>=%s AND (md_m<%s OR (%s AND md_m=%s))""",
                (r["axis_id"], a, b, inclusive, b),
            ).fetchone()
            relation = None
            if i:
                prev = members[i - 1]
                relation = uid(
                    "dataset-succession",
                    prev["dataset_revision_id"],
                    r["dataset_revision_id"],
                    digest(decisions),
                )
                relations.append(
                    {
                        "id": relation,
                        "borehole_id": r["borehole_id"],
                        "predecessor_revision_id": prev["dataset_revision_id"],
                        "successor_revision_id": r["dataset_revision_id"],
                        "relation_kind": "confirmed_display_successor",
                        "switch_md_m": a,
                        "switch_basis": "successor_first_actual_sample_md_m",
                        "confirmation_basis": decisions,
                    }
                )
            parts.append(
                {
                    "composite_id": cid,
                    "release_id": release,
                    "borehole_id": r["borehole_id"],
                    "sequence_no": i,
                    **pick(r, "dataset_id", "dataset_revision_id", "axis_id"),
                    "predecessor_relation_id": relation,
                    "selection_from_md_m": a,
                    "selection_to_md_m": b,
                    "upper_inclusive": inclusive,
                    "sample_no_from": chosen["a"],
                    "sample_no_to": chosen["b"],
                    "selection_status": "selected" if chosen["n"] else "fully_superseded",
                }
            )
        output.append(
            {
                "hole_id": hole,
                "composite": composite,
                "parts": parts,
                "relations": relations,
                "datasets": members,
            }
        )
    return output


def check_composite(conn, group, existing):
    """Compare existing records only and reject missing or inconsistent records without creating replacements.
    只比较已存记录；缺失或不一致都报错，不补建。
    """
    c = group["composite"]
    require(existing is not None, "Missing composite; rerun the import step")
    require(existing["definition_sha256"] == c["definition_sha256"], "Composite identity conflict")
    actual = conn.execute(
        "SELECT * FROM core.borehole_composite_part WHERE composite_id=%s ORDER BY sequence_no",
        (c["id"],),
    ).fetchall()
    require(
        json.loads(encoded(actual)) == json.loads(encoded(group["parts"])),
        "Existing composite parts differ",
    )
    for relation in group["relations"]:
        actual = conn.execute(
            "SELECT * FROM core.dataset_succession WHERE id=%s", (relation["id"],)
        ).fetchone()
        require(actual is not None, "Missing dataset succession")
        require(
            json.loads(encoded(pick(actual, *relation))) == json.loads(encoded(relation)),
            "Existing dataset succession differs",
        )


def verify_composites(conn, release, decisions):
    """Verify display segments without changing database records.
    验收展示取段，不修改数据库记录。
    """
    groups = composite_rows(conn, release, decisions)
    for group in groups:
        existing = conn.execute(
            "SELECT definition_sha256 FROM core.borehole_composite WHERE id=%s",
            (group["composite"]["id"],),
        ).fetchone()
        check_composite(conn, group, existing)
    return groups


def install_composites(conn, release, decisions):
    groups = composite_rows(conn, release, decisions)
    for group in groups:
        c = group["composite"]
        existing = conn.execute(
            "SELECT definition_sha256 FROM core.borehole_composite WHERE id=%s", (c["id"],)
        ).fetchone()
        if existing:
            check_composite(conn, group, existing)
            continue
        for r in group["relations"]:
            if not conn.execute(
                "SELECT 1 FROM core.dataset_succession WHERE id=%s", (r["id"],)
            ).fetchone():
                insert(conn, "dataset_succession", r)
        insert(conn, "borehole_composite", c)
        for p in group["parts"]:
            insert(conn, "borehole_composite_part", p)
    return groups

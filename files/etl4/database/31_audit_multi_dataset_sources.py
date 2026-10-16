"""Check log membership across multiple datasets using saved source evidence.
核对多个 dataset 的日志归属，使用已保存的来源证据。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import csv
import xml.etree.ElementTree as XML
import httpx
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from _db_common import ETL
from _run_reports import main_guard, now
from _db_files import read, sha, write
from _multi_common import MULTI_HOLES, SERVICE, raw_metadata, require
from _settings import StepParser
from _settings import settings, configure, add_hole_arguments, selected_holes, raw_path


def membership(dataset_id, kind, fetch):
    endpoint = {"scalar": "getLogCollection.html", "spectral": "getspectrallogs.html"}[kind]
    path = settings.multi_evidence_dir / "membership" / dataset_id / (kind + ".json")
    params = {"datasetid": dataset_id, "outputformat": "json"}
    if not path.exists():
        require(fetch, f"Missing source membership evidence: {dataset_id}/{kind}")
        with httpx.Client(timeout=30, follow_redirects=True) as client:
            r = client.get(SERVICE + endpoint, params=params)
            r.raise_for_status()
        write(
            path,
            {
                "url": str(r.url),
                "parameters": params,
                "retrieved_at": now(),
                "body": r.text,
                "body_sha256": hashlib.sha256(r.content).hexdigest(),
            },
        )
    evidence = read(path)
    require(evidence["parameters"] == params, "Membership evidence identity mismatch")
    body = evidence["body"]
    if body.lstrip().startswith("<"):
        require("<!DOCTYPE" not in body.upper() and "<!ENTITY" not in body.upper(), "Unsafe XML")
        values = [
            x.text for x in XML.fromstring(body).iter() if x.tag.split("}")[-1].lower() == "logid"
        ]
    else:
        response = json.loads(body)
        entries = response["SpectralLogCollection"]
        values = [e["logID"] for e in entries]
    require(values and len(values) == len(set(values)), "Empty/duplicate source membership")
    return values, {"path": path.relative_to(settings.database_dir).as_posix(), "sha256": sha(path)}


def main():
    parser = StepParser()
    parser.add_argument("--fetch-reference", action="store_true")
    add_hole_arguments(parser)
    args = parser.parse_args()
    configure(args)
    raw = {h: raw_metadata(h) for h in selected_holes(args, MULTI_HOLES)}
    jobs = [
        (d["dataset_id"], k)
        for r in raw.values()
        for d in r["datasets.json"]
        for k in ("scalar", "spectral")
    ]
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(lambda job: membership(*job, args.fetch_reference), jobs))
    members = dict(zip(jobs, responses, strict=True))
    datasets, snapshots = [], {}
    for hole, r in raw.items():
        manifests = {
            k: r[n]
            for k, n in [
                ("scalar", "scalar_manifest.json"),
                ("spectral", "spectral_manifest.json"),
                ("profile", "profilometer_manifest.json"),
            ]
        }
        by_scalar = {e["log_id"]: e for e in manifests["scalar"]}
        counts = {}
        for d in r["datasets.json"]:
            with raw_path(hole, by_scalar[d["tray_id"]]["file"]).open(
                encoding="utf-8-sig", newline=""
            ) as f:
                rows = list(csv.reader(f))[1:]
            end = float(rows[-1][1])
            require(end.is_integer(), "Noninteger last tray sample index")
            counts[d["dataset_id"]] = int(end) + 1
        seen = {k: set() for k in ("scalar", "spectral", "profile", "image")}
        for d in r["datasets.json"]:
            did = d["dataset_id"]
            n = counts[did]
            selected = {}
            evidence = {}
            for kind in ("scalar", "spectral"):
                values, proof = members[(did, kind)]
                local = {e["log_id"] for e in manifests[kind]}
                selected[kind] = sorted(local.intersection(values))
                require(
                    not seen[kind].intersection(selected[kind]),
                    "Source log belongs to multiple datasets",
                )
                seen[kind].update(selected[kind])
                evidence[kind] = {
                    **proof,
                    "basis": "official dataset-filtered source log collection",
                    "additional_remote_log_ids_not_imported": sorted(set(values) - local),
                }
            require(
                d["tray_id"] in selected["scalar"] and d["section_id"] in selected["scalar"],
                "Source interval IDs not in dataset",
            )
            require(
                list(counts.values()).count(n) == 1,
                "Profile membership ambiguous: explicit source evidence required",
            )
            selected["profile"] = [
                e["log_id"] for e in manifests["profile"] if e["sample_count"] == n
            ]
            require(
                len(selected["profile"]) == 1,
                "Expected one uniquely sized profile; inspect new source structure",
            )
            seen["profile"].update(selected["profile"])
            evidence["profile"] = {
                "basis": "unique dataset sample count; requires full prof_min cross-check in step 32",
                "status": "pending_full_value_verification",
            }
            selected["image"] = [
                e["log_id"] for e in r["image_logs_by_dataset.json"] if e["dataset_id"] == did
            ]
            seen["image"].update(selected["image"])
            image = next(
                e
                for e in r["images_manifest.json"]["logs"]
                if e["dataset_id"] == did and e["log_name"] == "Tray Thumbnail Images"
            )
            depths = r["tray_depths.json"][image["log_id"]]
            with raw_path(hole, by_scalar[d["section_id"]]["file"]).open(
                encoding="utf-8-sig", newline=""
            ) as f:
                sections = list(csv.reader(f))[1:]
            datasets.append(
                {
                    "hole_id": hole,
                    "source_dataset_id": did,
                    "source_dataset_name": d["dataset_name"],
                    "sample_count": n,
                    "thumbnail_count": len(depths),
                    "section_count": len(sections),
                    "depth_min_m": float(depths[0]["start_value"]),
                    "depth_max_m": float(depths[-1]["end_value"]),
                    "log_ids": selected,
                    "membership_evidence": evidence,
                }
            )
        for kind, manifest in manifests.items():
            require(
                seen[kind] == {e["log_id"] for e in manifest}, f"Unassigned {kind} logs in {hole}"
            )
        require(
            seen["image"] == {e["log_id"] for e in r["logs_image.json"]}, "Unassigned image log"
        )
        snapshots[hole] = {n: sha(raw_path(hole, n)) for n in r}
    result = {
        "status": "passed",
        "scope": list(raw),
        "datasets": datasets,
        "raw_metadata_sha256": snapshots,
        "source_files_modified": False,
        "association_scope": "Existing etl4 source IDs only; remote responses supplement missing membership, not scientific values",
    }
    write(settings.multi_work_dir / "31_source_audit.json", result)
    lines = [
        "# Multiple dataset source audit",
        "",
        "| Hole | Dataset | Samples | Depth range (m) | Trays |",
        "|---|---|---:|---|---:|",
    ]
    for u in datasets:
        lines.append(
            f"| {u['hole_id']} | {u['source_dataset_name']} | {u['sample_count']} | {u['depth_min_m']}–{u['depth_max_m']} | {u['thumbnail_count']} |"
        )
    lines += [
        "",
        "Log membership evidence: intersect official scalar and spectral log IDs queried by dataset with each original package; use explicit source dataset_id values for images; compare all prof_min values for profiles in step 32.",
        "Do not infer succession relationships from the table order; record user confirmation separately.",
    ]
    write(settings.multi_work_dir / "31_source_audit.md", "\n".join(lines) + "\n")
    print(f"Verified membership of {len(datasets)} datasets; all local log IDs accounted for.")


if __name__ == "__main__":
    main_guard(main)

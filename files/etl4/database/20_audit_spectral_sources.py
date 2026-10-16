"""Select the source release for media processing and check spectra against the file manifest.
明确媒体处理的来源发布，核对光谱与文件清单。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _db_common import ETL, connection
from _db_files import inside, read, sha
from _run_reports import main_guard
from _prepared_inputs import prepared_holes
from _media_common import DIRECTION, require, save_stage, stage
from _settings import StepParser
from _settings import settings, configure, raw_path, path_reference


def main():
    parser = StepParser()
    parser.add_argument("--source-release")
    parser.add_argument("--files-only", action="store_true", help="Prepare the final version from files without importing a base version first")
    parser.add_argument("--prepared-work-dir", type=Path, help="Explicit work directory containing existing prepared files")
    args = parser.parse_args()
    configure(args)
    prepared_work = args.prepared_work_dir.resolve() if args.prepared_work_dir else settings.work_dir
    selected = prepared_holes(work_dir=prepared_work, verify_files=not args.files_only)
    previous = (
        stage(20)
        if (settings.media_work_dir / "20.json").exists()
        else {}
    )
    direction = dict(DIRECTION)
    datasets = []
    spectra = []
    for h in selected:
        m = h["metadata"]
        hole = h["summary"]["hole_id"]
        d = m["dataset"]
        r = m["dataset_revision"]
        # 这里接收单 dataset 准备结果；多 dataset 使用各自的上下文。 / Accept prepared results for a single dataset here; multiple datasets use separate contexts.
        raw = read(raw_path(hole, "datasets.json"))
        require(
            len(raw) == 1,
            "Legacy prepared adapter cannot consume a multi-dataset package; prepare separate dataset contexts",
        )
        for a in h["storage"]["raw_assets"]:
            p = raw_path(hole, a["source_relative_path"])
            require(
                p.stat().st_size == a["byte_size"] and (args.files_only or sha(p) == a["sha256"]),
                f'Changed raw asset: {hole}/{a["source_relative_path"]}',
            )
        u = {
            "hole_id": hole,
            "dataset_id": d["id"],
            "source_dataset_id": d["source_dataset_id"],
            "source_revision_id": r["id"],
            "source_snapshot_id": r["source_snapshot_id"],
            "source_axis_id": h["alignment"]["axis_id"],
            "sample_count": h["alignment"]["sample_count"],
            "prepared_directory": h["folder"].relative_to(prepared_work / "processing/outputs").as_posix(),
            "prepared_object_key": path_reference(h["folder"]),
            "prepared_hashes": h["prepared_hashes"],
            "prepared_summary": h["summary"],
        }
        datasets.append(u)
        logs = {l["source_log_id"]: l for l in m["logs"] if l["log_kind"] == "spectral"}
        streams = {s["id"]: s for s in m["spectral_streams"]}
        for a in read(raw_path(hole, "spectral_manifest.json")):
            l = logs[a["log_id"]]
            st = streams[l["spectral_stream_id"]]
            require(a["band_count"] == st["wavelength_count"], "Channel count mismatch")
            if a["file"]:
                require(
                    a["dtype"] == "float32" and a["byte_order"] == "little",
                    "Unsupported declared dtype",
                )
                require(
                    a["nbytes"] == a["sample_count"] * a["band_count"] * 4,
                    "Spectral byte size mismatch",
                )
            spectra.append(
                {
                    **u,
                    **a,
                    "source_log_pk": l["id"],
                    "spectral_stream_id": st["id"],
                    "region_code": st["region_code"],
                    "wavelengths": st["wavelengths"],
                    "wavelength_unit": st["wavelength_unit"],
                    "value_unit": l["unit"],
                    "script_raw": l["source_script_raw"],
                    "layout_status": "unconfirmed",
                    "binding_status": "unconfirmed",
                }
            )
    release = None
    if args.files_only:
        require(not args.source_release, "File preparation and a source release cannot be selected together")
    else:
        release = args.source_release or previous.get("source_release_id")
        require(release, "First media preparation requires --source-release")
        with connection() as conn:
            for u in datasets:
                require(
                    conn.execute(
                        "SELECT 1 FROM core.release_dataset WHERE release_id=%s AND dataset_id=%s AND dataset_revision_id=%s",
                        (release, u["dataset_id"], u["source_revision_id"]),
                    ).fetchone(),
                    "Baseline release does not match locked prepared inputs",
                )
    result = {
        "status": "passed",
        "source_release_id": release,
        "input_lock_sha256": sha(prepared_work / "database/reports/7_input_lock.json"),
        "datasets": datasets,
        "spectra": spectra,
        "direction": direction,
    }
    save_stage(20, result)


if __name__ == "__main__":
    main_guard(main)

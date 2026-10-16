"""Compare base values sample by sample and check media sources and reader results.
逐样本核对基础数值，并检查媒体来源和读取结果。
"""

import time
import numpy as np
import pyarrow.parquet as pq
from _db_common import ETL
from _db_files import inside, read, sha
from _media_common import decode, require, source_folder, source_path
from _media_reader import read_sample, read_revision_sample
from _verification import verify_hole
from _settings import local_asset_path


def read_parquet_samples(path, samples):
    """Read expected values independently and read each required row group only once.
    独立读取期望值，每个需要的行组只读一次。
    """
    samples = sorted(set(samples))
    if not samples:
        return {}
    expected = {}
    with pq.ParquetFile(path) as parquet:
        require(
            0 <= samples[0] <= samples[-1] < parquet.metadata.num_rows,
            "Requested sample outside canonical file",
        )
        offset = 0
        pending = 0
        for group in range(parquet.num_row_groups):
            stop = offset + parquet.metadata.row_group(group).num_rows
            selected = []
            while pending < len(samples) and samples[pending] < stop:
                selected.append(samples[pending])
                pending += 1
            if selected:
                table = parquet.read_row_group(group)
                for sample in selected:
                    expected[sample] = table.slice(sample - offset, 1).to_pylist()[0]
            offset = stop
            if pending == len(samples):
                break
    return expected


def verify_crops(conn, unit, revision, rows):
    """Decode each source image once while checking every crop against its database source and pixels.
    同一原图只解码一次，仍逐张检查裁图的数据库来源和像素。
    """
    frames = {}
    for row in rows:
        path = source_path(unit, row["source_path"])
        frames.setdefault(path, []).append(row)
    for path, regions in frames.items():
        with decode(path) as original:
            for row in regions:
                linked = conn.execute(
                    """SELECT a.sha256, loc.object_key, loc.root_key, a.provenance, reg.id,
                  frame.id frame_id, original.sha256 source_sha256, original.logical_path source_path
                  FROM core.image_region reg JOIN core.image_region_asset link ON link.region_id=reg.id
                  JOIN core.asset a ON a.id=link.asset_id JOIN core.asset_location loc ON loc.asset_id=a.id
                  JOIN core.image_frame frame ON frame.id=reg.image_frame_id
                  JOIN core.asset original ON original.id=frame.asset_id
                  JOIN core.scan_log log ON log.id=frame.log_id
                  WHERE reg.dataset_revision_id=%s AND frame.image_ordinal=%s
                  AND log.source_log_id=%s AND reg.region_ordinal=%s AND loc.backend='local'""",
                    (revision, row["image_ordinal"], row["source_log_id"], row["region_ordinal"]),
                ).fetchall()
                require(len(linked) == 1, "Image region must have exactly one native crop")
                asset = linked[0]
                require(
                    asset["source_path"] == row["source_path"]
                    and asset["source_sha256"] == row["source_sha256"] == row["recipe"]["source_sha256"]
                    and asset["provenance"]["source_frame_id"] == str(asset["frame_id"])
                    and asset["provenance"]["region_id"] == str(asset["id"]),
                    "Stored crop is linked to the wrong source frame",
                )
                require(
                    asset["sha256"] == row["crop_sha256"]
                    and asset["provenance"]["recipe"] == row["recipe"],
                    "Crop source or recipe differs",
                )
                crop_path = local_asset_path(asset)
                with decode(crop_path) as actual, original.crop(row["recipe"]["crop_box_half_open"]) as expected:
                    require(
                        actual.size == expected.size and actual.tobytes() == expected.tobytes(),
                        "Stored crop differs from source pixels",
                    )


def checked_sample(conn, release, dataset_id, axis, sample, logs, revision):
    if release is None:
        return read_revision_sample(conn, revision, dataset_id, axis, sample, logs)
    return read_sample(conn, release, dataset_id, axis, sample, logs)


def verify_dataset(conn, u, new, release, package, storage=None, *, source_context=None, content_digest=None):
    dest = package["directory"]
    selected = package["datasets"][u["dataset_id"]]
    axis = new["axis_id"]
    old = u["source_axis_id"]
    rev = new["dataset_revision_id"]
    n = conn.execute(
        "SELECT count(*) n FROM core.scan_sample WHERE axis_id=%s", (axis,)
    ).fetchone()["n"]
    require(n == u["sample_count"], "Sample count differs")
    source_depths = None
    base_check = None
    if source_context is not None:
        base_check = verify_hole(
            conn, source_context, verify_assets=False,
            content_digest=content_digest, media_asset_count=len(selected["rows"]),
        )
        with pq.ParquetFile(source_context["folder"] / "5_canonical/sample_axis.parquet") as parquet:
            source_depths = parquet.read(columns=["md_m"])["md_m"].to_pylist()
    else:
        mismatch = conn.execute(
            """SELECT count(*) n FROM
          (SELECT * FROM core.scan_sample WHERE axis_id=%s) s FULL JOIN
          (SELECT * FROM core.scan_sample WHERE axis_id=%s) o ON s.sample_no=o.sample_no
          WHERE s.sample_no IS NULL OR o.sample_no IS NULL OR s.md_m IS DISTINCT FROM o.md_m OR
          s.tray_sample_no IS DISTINCT FROM o.tray_sample_no OR s.section_sample_no IS DISTINCT FROM o.section_sample_no OR
          s.section_distance_mm IS DISTINCT FROM o.section_distance_mm""",
            (axis, old),
        ).fetchone()["n"]
        require(mismatch == 0, "Source sample values changed")
    coverage = conn.execute(
        """SELECT count(*) n FROM core.scan_sample s WHERE s.axis_id=%s AND
      (SELECT count(*) FROM core.image_sample_mapping m WHERE m.axis_id=s.axis_id AND m.section_interval_id=s.section_interval_id
       AND s.sample_no BETWEEN m.sample_no_from AND m.sample_no_to)<>1""",
        (axis,),
    ).fetchone()["n"]
    require(coverage == 0, "A real sample does not have exactly one row mapping")
    mappings = conn.execute(
        """SELECT m.*,r.width_px FROM core.image_sample_mapping m JOIN core.image_region r ON r.id=m.image_region_id WHERE m.axis_id=%s ORDER BY m.sample_no_from""",
        (axis,),
    ).fetchall()
    for m in mappings:
        a = m["sample_no_from"]
        b = m["sample_no_to"]
        w = m["width_px"]
        require(
            all(isinstance(v, int) and not isinstance(v, bool) for v in (a, b, w))
            and 0 <= a <= b < n and w >= 1,
            "Invalid image mapping interval or width",
        )
        require(
            m["indicator_status"] == "approximate"
            and m["error_px"] is None
            and m["error_depth_m"] is None,
            "False pixel calibration",
        )
    # 核对复用的 Parquet 和新增行图的实际文件。 / Check the actual files for reused Parquet assets and newly created row images.
    rows = conn.execute(
        """SELECT a.*,l.object_key,l.root_key,l.verified_sha256 FROM core.asset a JOIN core.asset_location l ON l.asset_id=a.id
      WHERE a.dataset_revision_id=%s AND l.backend='local' """,
        (rev,),
    ).fetchall()
    for a in rows:
        p = local_asset_path(a)
        require(p.stat().st_size == a["byte_size"] and sha(p) == a["sha256"], "Asset bytes differ")
    verify_crops(conn, u, rev, selected["rows"])
    # 读取时保留解释结果的算法变体和原始值。 / Preserve interpretation algorithm variants and original values when reading results.
    logs = conn.execute(
        "SELECT id,source_log_id,source_log_name FROM core.scan_log WHERE dataset_revision_id=%s AND (log_kind='spectral' OR source_log_name IN ('Min1 sTSAS','Wt1 sTSAS','Min1 uTSAS','Wt1 uTSAS'))",
        (rev,),
    ).fetchall()
    chosen = [str(l["id"]) for l in logs]
    samples = {0, n - 1, n // 2}
    if storage is None:
        storage = read(source_folder(u) / "5_storage_report.json")
    payloads = {a["source_log_id"]: a for a in storage["assets"] if a.get("source_log_id")}
    arrays = {a["log_id"]: a for a in selected["arrays"]}
    # 边界常为空值，另选有矿物名称和权重的样本核对。 / Boundary values are often null; also check samples with mineral names and weights.
    for log in [l for l in logs if l["source_log_name"] in ("Min1 sTSAS", "Min1 uTSAS")]:
        payload = payloads[log["source_log_id"]]
        with pq.ParquetFile(source_folder(u) / payload["relative_path"]) as pf:
            for batch in pf.iter_batches(columns=["sample_no", "value_text"]):
                valid = next(
                    (r["sample_no"] for r in batch.to_pylist() if r["value_text"] is not None), None
                )
                if valid is not None:
                    samples.add(valid)
                    break
    first = mappings[0]
    samples.update({first["sample_no_from"], first["sample_no_to"]})
    if len(mappings) > 1:
        samples.add(mappings[1]["sample_no_from"])
    samples = sorted(x for x in samples if x < n)
    scalar_results = {}
    outputs = []
    times = []
    for s in samples:
        start = time.perf_counter()
        out = checked_sample(conn, release, u["dataset_id"], axis, s, chosen, rev)
        times.append((time.perf_counter() - start) * 1000)
        if source_depths is None:
            depth = conn.execute(
                "SELECT md_m FROM core.scan_sample WHERE axis_id=%s AND sample_no=%s", (old, s)
            ).fetchone()["md_m"]
        else:
            depth = source_depths[s]
        require(
            out["md_m"] == depth and out["image_status"] == "available",
            "Sample read or linked image differs",
        )
        for result in out["results"]:
            if result["log_kind"] == "scalar" and result.get("value") is not None:
                scalar_results.setdefault(result["source_log_id"], []).append((s, result["value"]))
            if result["log_kind"] == "spectral" and result["status"] == "available":
                a = arrays[result["source_log_id"]]
                with source_path(u, a["file"]).open("rb") as f:
                    f.seek(s * a["band_count"] * 4)
                    data = f.read(a["band_count"] * 4)
                expected = np.frombuffer(data, dtype="<f4")
                require(
                    result["spectra"] == [float(x) if np.isfinite(x) else None for x in expected],
                    "Sample curve differs from preserved F32",
                )
        outputs.append(out)
    for log_id, values in scalar_results.items():
        expected = read_parquet_samples(
            source_folder(u) / payloads[log_id]["relative_path"], [sample for sample, _ in values]
        )
        for sample, value in values:
            require(value == expected[sample], "Sample result differs from canonical source values")
    if len(mappings) > 1:
        a = next(o for o in outputs if o["sample_no"] == first["sample_no_to"])
        b = next(o for o in outputs if o["sample_no"] == mappings[1]["sample_no_from"])
        require(
            a["images"][0]["image_region_id"] != b["images"][0]["image_region_id"]
            and a["images"][0]["indicator"]["p"] == (0.5 if first["sample_no_from"] == first["sample_no_to"] else 1)
            and b["images"][0]["indicator"]["p"] == (0.5 if mappings[1]["sample_no_from"] == mappings[1]["sample_no_to"] else 0),
            "Boundary samples and positions are inconsistent between adjacent image rows",
        )
    # 将官方样本与离线读取结果按 float32 比较。 / Compare official samples and offline reader results as float32 values.
    refs = 0
    for a in selected["arrays"]:
        log = next(l for l in logs if l["source_log_id"] == a["log_id"])
        for ref in a["references"]:
            for value in read(inside(dest, dest / ref["path"]))["response"]:
                out = checked_sample(
                    conn, release, u["dataset_id"], axis, value["sampleNo"], [str(log["id"])], rev
                )["results"][0]
                require(
                    np.asarray(out["spectra"], dtype="<f4").tobytes()
                    == np.asarray(value["floatspectraldata"], dtype="<f4").tobytes(),
                    "DB reader differs from official reference",
                )
                refs += 1
    return {
        "hole_id": u["hole_id"],
        "base_check": base_check,
        "sample_count": n,
        "row_count": len(mappings),
        "assets_checked": len(rows),
        "official_reference_spectra_compared": refs,
        "sample_read_ms": times,
        "example_selections": outputs,
    }



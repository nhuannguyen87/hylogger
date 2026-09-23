import hashlib
import os
from functools import lru_cache
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq
from dotenv import load_dotenv

from app.s3_storage import S3Store


load_dotenv()

ETL4_ROOT = Path(os.environ.get("ETL4_ROOT", "")).resolve()


def asset_backend():
    backend = os.getenv(
        "ETL4_ASSET_BACKEND",
        "local",
    ).strip().lower()

    if backend not in ("local", "s3"):
        raise RuntimeError(
            "ETL4_ASSET_BACKEND must be either 'local' or 's3'."
        )

    return backend


@lru_cache(maxsize=1)
def get_s3_store():
    if asset_backend() != "s3":
        return None

    bucket = os.getenv("ETL4_S3_BUCKET")
    prefix = os.getenv("ETL4_S3_PREFIX")
    region = os.getenv("ETL4_AWS_REGION")
    root_key = os.getenv(
        "ETL4_S3_ROOT_KEY",
        "etl4_s3_primary",
    )

    if not bucket or not prefix or not region:
        raise RuntimeError(
            "ETL4_S3_BUCKET, ETL4_S3_PREFIX and "
            "ETL4_AWS_REGION are required for S3 mode."
        )

    try:
        import boto3
    except ImportError as exc:
        raise RuntimeError(
            "AWS S3 access requires boto3. "
            "Install backend/requirements-aws.txt."
        ) from exc

    client = boto3.client(
        "s3",
        region_name=region,
    )

    return S3Store(
        client,
        bucket,
        root_key,
        prefix,
    )


INDICATOR = {
    "range_basis": "row_sample_interval",
    "endpoint_rule": "first_last_pixel_center",
    "single_sample_rule": "center",
    "coordinate_space": "row_crop_pixels",
}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(path):
    h = hashlib.sha256()

    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)

    return h.hexdigest()


def inside(root, path):
    root = Path(root).resolve()
    path = Path(path).resolve()

    if path == root or not path.is_relative_to(root):
        raise ValueError("Path outside permitted ETL4 root")

    return path


def marker(sample_no, sample_no_from, sample_no_to, width):
    require(
        all(
            isinstance(v, int) and not isinstance(v, bool)
            for v in (sample_no, sample_no_from, sample_no_to, width)
        ),
        "Integer sample identities and width required",
    )

    require(
        0 <= sample_no_from <= sample_no <= sample_no_to and width >= 1,
        "Sample outside mapping interval or invalid image width",
    )

    p = (
        (sample_no - sample_no_from) / (sample_no_to - sample_no_from)
        if sample_no_to > sample_no_from
        else 0.5
    )

    return {
        "method": "sample_index_linear",
        "version": 1,
        "status": "approximate",
        "p": p,
        "x_px": p * (width - 1),
    }


@lru_cache(maxsize=32)
def parquet_group(path, checksum, size, mtime_ns, group):
    require(
        sha(Path(path)) == checksum,
        "Canonical asset content changed",
    )

    with pq.ParquetFile(path) as parquet:
        return parquet.read_row_group(group)


@lru_cache(maxsize=16)
def spectral_bytes(path, size, mtime_ns, offset, length, checksum):
    with Path(path).open("rb") as f:
        f.seek(offset)
        data = f.read(length)

    require(
        len(data) == length
        and hashlib.sha256(data).hexdigest() == checksum,
        "Spectral block content changed",
    )

    return data


def local_asset(conn, asset_id):
    rows = conn.execute(
        """
        SELECT
            a.*,
            l.root_key,
            l.object_key,
            l.access_status
        FROM core.asset a
        JOIN core.asset_location l
            ON l.asset_id = a.id
           AND l.verified_sha256 = a.sha256
        WHERE a.id = %s
          AND l.backend = 'local'
          AND l.access_status = 'verified_local'
        ORDER BY l.root_key, l.object_key
        """,
        (asset_id,),
    ).fetchall()

    for row in rows:
        if row["root_key"] != "etl4":
            continue

        path = inside(
            ETL4_ROOT,
            ETL4_ROOT / row["object_key"],
        )

        if path.is_file() and path.stat().st_size == row["byte_size"]:
            return row, path

    return None, None

INDICATOR_LABEL = (
    "Marker position is estimated from sample index; "
    "depth and results come from the selected sample."
)


def read_result(conn, log, axis_id, sample_no):
    base = {
        key: log[key]
        for key in (
            "id",
            "source_log_id",
            "source_log_name",
            "log_kind",
            "unit",
            "interpretation_set_id",
            "variant_code",
            "algorithm_family",
            "output_region",
            "component_rank",
            "metric_key",
        )
    }

    base["log_id"] = base.pop("id")

    if log["availability_status"] != "payload_present":
        return {
            **base,
            "status": log["availability_status"],
            "value": None,
        }

    if (
        log["binding_status"] != "verified"
        or str(log["axis_id"]) != str(axis_id)
    ):
        return {
            **base,
            "status": "axis_binding_unavailable",
            "value": None,
        }

    if log["log_kind"] == "spectral":
        candidates = conn.execute(
            """
            SELECT
                a.*,
                m.source_row_from,
                m.sample_no_from,
                m.sample_no_to
            FROM core.spectral_array a
            JOIN core.spectral_sample_map m
                ON m.spectral_array_id = a.id
            WHERE a.log_id = %s
              AND m.axis_id = %s
              AND %s BETWEEN m.sample_no_from AND m.sample_no_to
              AND m.binding_status = 'verified'
              AND a.layout_status = 'verified_sample_major'
            """,
            (log["id"], axis_id, sample_no),
        ).fetchall()

        if len(candidates) != 1:
            return {
                **base,
                "status": "spectral_mapping_unavailable",
                "value": None,
            }

        array = candidates[0]

        source_row = (
            array["source_row_from"]
            + sample_no
            - array["sample_no_from"]
        )

        blocks = conn.execute(
            """
            SELECT *
            FROM core.spectral_block
            WHERE spectral_array_id = %s
              AND source_row_from <= %s
              AND source_row_to_exclusive > %s
            """,
            (array["id"], source_row, source_row),
        ).fetchall()

        require(
            len(blocks) == 1,
            "Ambiguous spectral byte block",
        )

        block = blocks[0]

        asset, path = local_asset(
            conn,
            array["asset_id"],
        )

        if path is None:
            return {
                **base,
                "status": "asset_unavailable",
                "value": None,
            }

        stat = path.stat()

        data = spectral_bytes(
            str(path),
            stat.st_size,
            stat.st_mtime_ns,
            block["byte_offset"],
            block["byte_length"],
            block["sha256"],
        )

        offset = (
            source_row - block["source_row_from"]
        ) * array["sample_stride_bytes"]

        values = np.frombuffer(
            data,
            dtype=array["dtype_code"],
            count=array["channel_count"],
            offset=offset,
        )

        stream = conn.execute(
            """
            SELECT *
            FROM core.spectral_stream
            WHERE id = %s
            """,
            (array["spectral_stream_id"],),
        ).fetchone()

        return {
            **base,
            "status": "available",
            "source_row": source_row,
            "region_code": stream["region_code"],
            "wavelength": stream["wavelengths"],
            "wavelength_unit": stream["wavelength_unit"],
            "spectra": [
                float(value)
                if np.isfinite(value)
                else None
                for value in values
            ],
            "nonfinite_channels": [
                i
                for i, value in enumerate(values)
                if not np.isfinite(value)
            ],
            "value_unit": array["value_unit"],
            "scaling_applied": False,
            "array_id": array["id"],
        }

    chunks = conn.execute(
        """
        SELECT
            c.*,
            a.sha256
        FROM core.data_chunk c
        JOIN core.asset a
            ON a.id = c.asset_id
        WHERE c.log_id = %s
          AND c.axis_id = %s
          AND c.source_row_from <= %s
          AND c.source_row_to_exclusive > %s
        """,
        (
            log["id"],
            axis_id,
            sample_no,
            sample_no,
        ),
    ).fetchall()

    if len(chunks) != 1:
        return {
            **base,
            "status": "sample_result_unavailable",
            "value": None,
        }

    chunk = chunks[0]

    asset, path = local_asset(
        conn,
        chunk["asset_id"],
    )

    if path is None:
        return {
            **base,
            "status": "asset_unavailable",
            "value": None,
        }

    stat = path.stat()

    table = parquet_group(
        str(path),
        chunk["sha256"],
        stat.st_size,
        stat.st_mtime_ns,
        chunk["row_group"],
    )

    row = table.slice(
        sample_no - chunk["source_row_from"],
        1,
    ).to_pylist()[0]

    require(
        row["sample_no"] == sample_no,
        "Canonical row has a different sample identity",
    )

    if log["log_kind"] == "scalar":
        status = (
            "source_null"
            if row["value_numeric"] is None
            and row["value_text"] is None
            else "available"
        )
    else:
        status = "available_uncalibrated_profile"

    return {
        **base,
        "status": status,
        "value": row,
    }


def read_sample(
    conn,
    release_id,
    dataset_id,
    axis_id,
    sample_no,
    log_ids=None,
    image_log_id=None,
):
    require(
        isinstance(sample_no, int)
        and not isinstance(sample_no, bool)
        and sample_no >= 0,
        "Invalid sample number",
    )

    context = conn.execute(
        """
        SELECT
            d.dataset_revision_id,
            a.sample_count
        FROM core.release_dataset d
        JOIN core.sample_axis a
            ON a.dataset_revision_id = d.dataset_revision_id
        WHERE d.release_id = %s
          AND d.dataset_id = %s
          AND a.id = %s
        """,
        (
            release_id,
            dataset_id,
            axis_id,
        ),
    ).fetchone()

    require(
        context,
        "Release, dataset and axis do not belong to the same selection",
    )

    sample = conn.execute(
        """
        SELECT *
        FROM core.scan_sample
        WHERE axis_id = %s
          AND sample_no = %s
        """,
        (
            axis_id,
            sample_no,
        ),
    ).fetchone()

    require(
        sample,
        "Selected source sample does not exist",
    )

    logs = conn.execute(
        """
        SELECT
            l.*,
            b.status AS binding_status,
            b.axis_id,
            i.variant_code,
            i.algorithm_family,
            i.output_region
        FROM core.scan_log l
        LEFT JOIN core.log_axis_binding b
            ON b.log_id = l.id
        LEFT JOIN core.interpretation_set i
            ON i.id = l.interpretation_set_id
        WHERE l.dataset_revision_id = %s
          AND l.log_kind IN ('scalar', 'spectral', 'profile')
        ORDER BY
            l.log_kind,
            l.source_log_name,
            l.id
        """,
        (context["dataset_revision_id"],),
    ).fetchall()

    if log_ids is not None:
        wanted = {str(value) for value in log_ids}

        require(
            wanted <= {str(log["id"]) for log in logs},
            "Result log is outside selected dataset revision",
        )

        logs = [
            log
            for log in logs
            if str(log["id"]) in wanted
        ]

    images = conn.execute(
        """
        SELECT
            m.*,
            r.region_status,
            f.log_id,
            f.asset_id AS source_asset_id,
            ra.asset_id,
            ra.width_px,
            ra.height_px,
            ra.transform
        FROM core.image_sample_mapping m
        JOIN core.image_region r
            ON r.id = m.image_region_id
        JOIN core.image_frame f
            ON f.id = r.image_frame_id
        LEFT JOIN core.image_region_asset ra
            ON ra.region_id = r.id
           AND ra.representation_kind = 'native_lossless_png'
        WHERE m.axis_id = %s
          AND m.section_interval_id = %s
          AND %s BETWEEN m.sample_no_from AND m.sample_no_to
        ORDER BY
            f.log_id,
            r.region_ordinal,
            ra.asset_id
        """,
        (
            axis_id,
            sample["section_interval_id"],
            sample_no,
        ),
    ).fetchall()

    if image_log_id is not None:
        valid_image_log = conn.execute(
            """
            SELECT 1
            FROM core.scan_log
            WHERE id = %s
              AND dataset_revision_id = %s
              AND log_kind = 'image'
            """,
            (
                image_log_id,
                context["dataset_revision_id"],
            ),
        ).fetchone()

        require(
            valid_image_log,
            "Image log is outside selected dataset revision",
        )

        images = [
            image
            for image in images
            if str(image["log_id"]) == str(image_log_id)
        ]

    candidates = []

    for image in images:
        if image["asset_id"]:
            asset, path = local_asset(
                conn,
                image["asset_id"],
            )
        else:
            asset, path = None, None

        available = (
            path is not None
            and image["region_status"] == "reviewed"
            and image["mapping_status"] == "metadata_associated"
        )

        indicator = None

        if (
            available
            and image["indicator_method"] == "sample_index_linear"
            and image["indicator_version"] == 1
            and image["indicator_status"] == "approximate"
        ):
            require(
                image["indicator_parameters"] == INDICATOR,
                "Unknown index mapping contract",
            )

            indicator = marker(
                sample_no,
                image["sample_no_from"],
                image["sample_no_to"],
                image["width_px"],
            )

        candidates.append(
            {
                "image_region_id": image["image_region_id"],
                "image_asset_id": image["asset_id"],
                "image_log_id": image["log_id"],
                "source_image_asset_id": image["source_asset_id"],
                "status": (
                    "available"
                    if available
                    else "image_unavailable"
                ),
                "object_key": (
                    asset["object_key"]
                    if available
                    else None
                ),
                "width_px": image["width_px"],
                "height_px": image["height_px"],
                "sample_no_from": image["sample_no_from"],
                "sample_no_to": image["sample_no_to"],
                "indicator": indicator,
                "mapping_level": image["mapping_level"],
                "mapping_status": image["mapping_status"],
                "source_depth_direction": image[
                    "source_depth_direction"
                ],
                "error_px": image["error_px"],
                "error_depth_m": image["error_depth_m"],
            }
        )

    return {
        "release_id": str(release_id),
        "dataset_id": str(dataset_id),
        "dataset_revision_id": str(
            context["dataset_revision_id"]
        ),
        "axis_id": str(axis_id),
        "sample_no": sample_no,
        "md_m": sample["md_m"],
        "sample_count": context["sample_count"],
        "sample": sample,
        "results": [
            read_result(
                conn,
                log,
                axis_id,
                sample_no,
            )
            for log in logs
        ],
        "image_status": (
            "image_unavailable"
            if not candidates
            else (
                "selection_required"
                if len(candidates) > 1
                else candidates[0]["status"]
            )
        ),
        "images": candidates,
        "indicator_label": INDICATOR_LABEL,
    }

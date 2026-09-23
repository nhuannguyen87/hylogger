from app.database import get_connection


HOLE_QUERY = """
SELECT
    b.source_hole_id AS hole_id,
    COALESCE(br.project_name, dr.source_project_name, '') AS project,
    ST_Y(br.collar_geom) AS latitude,
    ST_X(br.collar_geom) AS longitude,
    sa.depth_min_m AS scan_from_m,
    sa.depth_max_m AS scan_to_m,
    COALESCE(dr.instrument_name, '') AS instrument
FROM core.active_release ar
JOIN core.release_dataset rd
    ON rd.release_id = ar.release_id
JOIN core.dataset_revision dr
    ON dr.id = rd.dataset_revision_id
JOIN core.borehole b
    ON b.id = dr.borehole_id
JOIN core.borehole_revision br
    ON br.id = dr.borehole_revision_id
JOIN core.sample_axis sa
    ON sa.dataset_revision_id = dr.id
WHERE ar.singleton
"""


def get_all_holes():
    with get_connection() as conn:
        return conn.execute(
            HOLE_QUERY + """
            ORDER BY b.source_hole_id
            """
        ).fetchall()


def get_hole_by_id(hole_id: str):
    with get_connection() as conn:
        return conn.execute(
            HOLE_QUERY + """
            AND b.source_hole_id = %s
            """,
            (hole_id,),
        ).fetchone()


def get_profile_by_hole_id(hole_id: str):
    # Real mineral/sample data will be added from the verified
    # ETL4 sample/media reader. Do not return prototype mock data.
    return None

from app.media_reader import read_sample


def get_sample_by_revision(
    revision_id: str,
    axis_id: str,
    sample_no: int,
    log_ids=None,
    image_log_id=None,
):
    with get_connection() as conn:
        context = conn.execute(
            """
            SELECT
                ar.release_id,
                rd.dataset_id
            FROM core.active_release ar
            JOIN core.release_dataset rd
                ON rd.release_id = ar.release_id
            WHERE ar.singleton
              AND rd.dataset_revision_id = %s
            """,
            (revision_id,),
        ).fetchone()

        if context is None:
            return None

        return read_sample(
            conn=conn,
            release_id=context["release_id"],
            dataset_id=context["dataset_id"],
            axis_id=axis_id,
            sample_no=sample_no,
            log_ids=log_ids,
            image_log_id=image_log_id,
        )

def get_image_asset_for_active_release(asset_id: str):
    from app.media_reader import (
        asset_backend,
        get_s3_store,
        local_asset,
    )

    with get_connection() as conn:
        asset = conn.execute(
            """
            SELECT DISTINCT
                a.id,
                a.media_type,
                a.byte_size,
                a.sha256
            FROM core.asset a
            JOIN core.image_region_asset ira
                ON ira.asset_id = a.id
            JOIN core.image_region ir
                ON ir.id = ira.region_id
            JOIN core.image_frame f
                ON f.id = ir.image_frame_id
            JOIN core.release_dataset rd
                ON rd.dataset_revision_id = f.dataset_revision_id
            JOIN core.active_release ar
                ON ar.release_id = rd.release_id
            WHERE ar.singleton
              AND a.id = %s
              AND a.media_type IN ('image/png', 'image/jpeg')
            """,
            (asset_id,),
        ).fetchone()

        if asset is None:
            return None

        if asset_backend() == "s3":
            store = get_s3_store()
            verified_asset = store.asset(
                conn,
                asset_id,
            )

            if verified_asset is None:
                raise ValueError("Image asset is unavailable")

            content = store.full(verified_asset)

            return {
                "id": str(asset["id"]),
                "media_type": asset["media_type"],
                "byte_size": asset["byte_size"],
                "sha256": asset["sha256"],
                "content": content,
            }

        verified_asset, local_path = local_asset(
            conn,
            asset_id,
        )

        if verified_asset is None or local_path is None:
            raise ValueError("Image asset is unavailable")

        return {
            "id": str(asset["id"]),
            "media_type": asset["media_type"],
            "byte_size": asset["byte_size"],
            "sha256": asset["sha256"],
            "path": local_path,
        }


def get_datasets_by_hole(hole_id: str):
    with get_connection() as conn:
        return conn.execute(
            """
            SELECT
                ar.release_id,
                rd.dataset_id,
                dr.id AS dataset_revision_id,
                dr.source_dataset_name,
                dr.instrument_name,
                sa.id AS axis_id,
                sa.sample_count,
                sa.depth_min_m,
                sa.depth_max_m,
                sa.coordinate_kind
            FROM core.active_release ar
            JOIN core.release_dataset rd
                ON rd.release_id = ar.release_id
            JOIN core.dataset_revision dr
                ON dr.id = rd.dataset_revision_id
            JOIN core.borehole b
                ON b.id = dr.borehole_id
            LEFT JOIN core.sample_axis sa
                ON sa.dataset_revision_id = dr.id
            WHERE ar.singleton
              AND b.source_hole_id = %s
            ORDER BY
                dr.source_dataset_name,
                dr.id
            """,
            (hole_id,),
        ).fetchall()


def get_logs_by_revision(revision_id: str):
    with get_connection() as conn:
        revision = conn.execute(
            """
            SELECT 1
            FROM core.active_release ar
            JOIN core.release_dataset rd
                ON rd.release_id = ar.release_id
            WHERE ar.singleton
              AND rd.dataset_revision_id = %s
            """,
            (revision_id,),
        ).fetchone()

        if revision is None:
            return None

        return conn.execute(
            """
            SELECT
                l.id AS log_id,
                l.source_log_id,
                l.source_log_name,
                l.log_kind,
                l.unit,
                l.metric_key,
                l.availability_status,
                l.omission_reason,
                l.interpretation_set_id,
                b.axis_id,
                b.status AS axis_binding_status,
                b.basis AS axis_binding_basis,
                i.output_region,
                i.variant_code,
                i.algorithm_family,
                i.source_set_code,
                s.region_code AS spectral_region,
                m.is_probability
            FROM core.scan_log l
            LEFT JOIN core.log_axis_binding b
                ON b.log_id = l.id
            LEFT JOIN core.interpretation_set i
                ON i.id = l.interpretation_set_id
            LEFT JOIN core.spectral_stream s
                ON s.id = l.spectral_stream_id
            LEFT JOIN core.metric_definition m
                ON m.dataset_revision_id = l.dataset_revision_id
               AND m.metric_key = l.metric_key
            WHERE l.dataset_revision_id = %s
            ORDER BY
                l.log_kind,
                l.source_log_name,
                l.source_log_id
            """,
            (revision_id,),
        ).fetchall()


def get_samples_by_revision(
    revision_id: str,
    axis_id: str,
    from_m=None,
    to_m=None,
    after_sample: int = -1,
    limit: int = 1000,
):
    with get_connection() as conn:
        valid_axis = conn.execute(
            """
            SELECT 1
            FROM core.active_release ar
            JOIN core.release_dataset rd
                ON rd.release_id = ar.release_id
            JOIN core.sample_axis sa
                ON sa.dataset_revision_id = rd.dataset_revision_id
            WHERE ar.singleton
              AND rd.dataset_revision_id = %s
              AND sa.id = %s
            """,
            (revision_id, axis_id),
        ).fetchone()

        if valid_axis is None:
            return None

        rows = conn.execute(
            """
            SELECT
                s.axis_id,
                s.sample_no,
                s.md_m,
                s.section_interval_id
            FROM core.scan_sample s
            WHERE s.axis_id = %s
              AND s.sample_no > %s
              AND (%s::float8 IS NULL OR s.md_m >= %s)
              AND (%s::float8 IS NULL OR s.md_m <= %s)
            ORDER BY s.sample_no
            LIMIT %s
            """,
            (
                axis_id,
                after_sample,
                from_m,
                from_m,
                to_m,
                to_m,
                limit + 1,
            ),
        ).fetchall()

        return {
            "items": rows[:limit],
            "next_after_sample": (
                rows[limit - 1]["sample_no"]
                if len(rows) > limit
                else None
            ),
        }


def get_intervals_by_revision(
    revision_id: str,
    axis_id: str,
    kind=None,
    offset: int = 0,
    limit: int = 200,
):
    with get_connection() as conn:
        valid_axis = conn.execute(
            """
            SELECT 1
            FROM core.active_release ar
            JOIN core.release_dataset rd
                ON rd.release_id = ar.release_id
            JOIN core.sample_axis sa
                ON sa.dataset_revision_id = rd.dataset_revision_id
            WHERE ar.singleton
              AND rd.dataset_revision_id = %s
              AND sa.id = %s
            """,
            (revision_id, axis_id),
        ).fetchone()

        if valid_axis is None:
            return None

        rows = conn.execute(
            """
            SELECT
                i.*
            FROM core.core_interval i
            WHERE i.axis_id = %s
              AND (%s::text IS NULL OR i.interval_kind = %s)
            ORDER BY
                i.interval_kind,
                i.ordinal
            OFFSET %s
            LIMIT %s
            """,
            (
                axis_id,
                kind,
                kind,
                offset,
                limit + 1,
            ),
        ).fetchall()

        return {
            "items": rows[:limit],
            "next_offset": (
                offset + limit
                if len(rows) > limit
                else None
            ),
        }


def get_issues_by_revision(revision_id: str):
    with get_connection() as conn:
        revision = conn.execute(
            """
            SELECT 1
            FROM core.active_release ar
            JOIN core.release_dataset rd
                ON rd.release_id = ar.release_id
            WHERE ar.singleton
              AND rd.dataset_revision_id = %s
            """,
            (revision_id,),
        ).fetchone()

        if revision is None:
            return None

        issues = conn.execute(
            """
            SELECT *
            FROM core.quality_issue
            WHERE dataset_revision_id = %s
            ORDER BY processing_step, code, id
            """,
            (revision_id,),
        ).fetchall()

        references = conn.execute(
            """
            SELECT *
            FROM core.source_reference
            WHERE dataset_revision_id = %s
            ORDER BY role
            """,
            (revision_id,),
        ).fetchall()

        return {
            "items": issues,
            "source_references": references,
        }


def get_nearby_holes(
    latitude: float,
    longitude: float,
    radius_km: float = 25.0,
    limit: int = 20,
):
    with get_connection() as conn:
        return conn.execute(
            """
            SELECT
                b.source_hole_id AS hole_id,
                COALESCE(br.project_name, dr.source_project_name, '') AS project,
                ST_Y(br.collar_geom) AS latitude,
                ST_X(br.collar_geom) AS longitude,
                sa.depth_min_m AS scan_from_m,
                sa.depth_max_m AS scan_to_m,
                COALESCE(dr.instrument_name, '') AS instrument,
                ST_Distance(
                    br.collar_geom::geography,
                    ST_SetSRID(
                        ST_MakePoint(%s, %s),
                        4326
                    )::geography
                ) / 1000.0 AS distance_km
            FROM core.active_release ar
            JOIN core.release_dataset rd
                ON rd.release_id = ar.release_id
            JOIN core.dataset_revision dr
                ON dr.id = rd.dataset_revision_id
            JOIN core.borehole b
                ON b.id = dr.borehole_id
            JOIN core.borehole_revision br
                ON br.id = dr.borehole_revision_id
            JOIN core.sample_axis sa
                ON sa.dataset_revision_id = dr.id
            WHERE ar.singleton
              AND br.collar_geom IS NOT NULL
              AND ST_DWithin(
                    br.collar_geom::geography,
                    ST_SetSRID(
                        ST_MakePoint(%s, %s),
                        4326
                    )::geography,
                    %s
              )
            ORDER BY distance_km
            LIMIT %s
            """,
            (
                longitude,
                latitude,
                longitude,
                latitude,
                radius_km * 1000.0,
                limit,
            ),
        ).fetchall()


def get_boreholes_v1(
    west=None,
    south=None,
    east=None,
    north=None,
):
    with get_connection() as conn:
        params = []
        bbox_sql = ""

        if None not in (west, south, east, north):
            bbox_sql = """
              AND br.collar_geom && ST_MakeEnvelope(
                    %s, %s, %s, %s, 4326
              )
            """
            params.extend([
                west,
                south,
                east,
                north,
            ])

        rows = conn.execute(
            """
            SELECT DISTINCT
                ar.release_id,
                b.source_hole_id AS hole_id,
                COALESCE(
                    br.project_name,
                    dr.source_project_name,
                    ''
                ) AS project,
                br.source_name,
                br.provider_code,
                br.custodian_name,
                br.operator_name,
                br.driller_name,
                br.reported_length_m,
                br.trajectory_status,
                br.orientation_missing_reason,
                COALESCE(
                    dr.instrument_name,
                    ''
                ) AS instrument,
                sa.depth_min_m AS scan_from_m,
                sa.depth_max_m AS scan_to_m,
                ST_AsGeoJSON(
                    br.collar_geom
                )::jsonb AS geometry
            FROM core.active_release ar
            JOIN core.release_dataset rd
                ON rd.release_id = ar.release_id
            JOIN core.dataset_revision dr
                ON dr.id = rd.dataset_revision_id
            JOIN core.borehole b
                ON b.id = dr.borehole_id
            JOIN core.borehole_revision br
                ON br.id = dr.borehole_revision_id
            JOIN core.sample_axis sa
                ON sa.dataset_revision_id = dr.id
            WHERE ar.singleton
            """
            + bbox_sql
            + """
            ORDER BY b.source_hole_id
            """,
            params,
        ).fetchall()

        return rows

from app.media_reader import local_asset, parquet_group


def get_scalar_log_values(
    revision_id: str,
    log_id: str,
    axis_id: str,
    from_m=None,
    to_m=None,
    after_sample: int = -1,
    limit: int = 1000,
):
    with get_connection() as conn:
        log = conn.execute(
            """
            SELECT
                l.id,
                l.source_log_name,
                l.metric_key,
                l.unit,
                l.availability_status,
                l.omission_reason,
                b.axis_id,
                b.status AS binding_status
            FROM core.scan_log l
            JOIN core.release_dataset rd
                ON rd.dataset_revision_id = l.dataset_revision_id
            JOIN core.active_release ar
                ON ar.release_id = rd.release_id
            LEFT JOIN core.log_axis_binding b
                ON b.log_id = l.id
            WHERE ar.singleton
              AND l.dataset_revision_id = %s
              AND l.id = %s
              AND l.log_kind = 'scalar'
            """,
            (revision_id, log_id),
        ).fetchone()

        if log is None:
            return None

        if log["availability_status"] != "payload_present":
            return {
                "log_id": str(log["id"]),
                "source_log_name": log["source_log_name"],
                "metric_key": log["metric_key"],
                "unit": log["unit"],
                "status": "metadata_only",
                "reason": log["omission_reason"],
                "items": [],
                "next_after_sample": None,
            }

        if (
            log["binding_status"] != "verified"
            or str(log["axis_id"]) != str(axis_id)
        ):
            return {
                "log_id": str(log["id"]),
                "source_log_name": log["source_log_name"],
                "metric_key": log["metric_key"],
                "unit": log["unit"],
                "status": "axis_binding_unavailable",
                "items": [],
                "next_after_sample": None,
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
              AND c.source_row_to_exclusive > %s
              AND (%s::float8 IS NULL OR c.coordinate_max >= %s)
              AND (%s::float8 IS NULL OR c.coordinate_min <= %s)
            ORDER BY c.source_row_from
            """,
            (
                log_id,
                axis_id,
                after_sample + 1,
                from_m,
                from_m,
                to_m,
                to_m,
            ),
        ).fetchall()

        items = []

        for chunk in chunks:
            asset, path = local_asset(
                conn,
                chunk["asset_id"],
            )

            if path is None:
                raise ValueError(
                    "Scalar asset is unavailable"
                )

            stat = path.stat()

            table = parquet_group(
                str(path),
                chunk["sha256"],
                stat.st_size,
                stat.st_mtime_ns,
                chunk["row_group"],
            )

            for row in table.to_pylist():
                sample_no = row["sample_no"]
                depth_m = row["depth_m"]

                if sample_no <= after_sample:
                    continue

                if (
                    from_m is not None
                    and depth_m < from_m
                ):
                    continue

                if (
                    to_m is not None
                    and depth_m > to_m
                ):
                    continue

                items.append(
                    {
                        "sample_no": sample_no,
                        "depth_m": depth_m,
                        "value_numeric": row["value_numeric"],
                        "value_text": row["value_text"],
                        "value_raw": row["value_raw"],
                        "status": (
                            "source_null"
                            if row["value_numeric"] is None
                            and row["value_text"] is None
                            else "available"
                        ),
                    }
                )

                if len(items) > limit:
                    break

            if len(items) > limit:
                break

        has_more = len(items) > limit
        returned = items[:limit]

        return {
            "log_id": str(log["id"]),
            "source_log_name": log["source_log_name"],
            "metric_key": log["metric_key"],
            "unit": log["unit"],
            "status": "available",
            "items": returned,
            "next_after_sample": (
                returned[-1]["sample_no"]
                if has_more and returned
                else None
            ),
        }


def get_profile_log_values(
    revision_id: str,
    log_id: str,
    axis_id: str,
    from_m=None,
    to_m=None,
    after_sample: int = -1,
    limit: int = 200,
):
    with get_connection() as conn:
        log = conn.execute(
            """
            SELECT
                l.id,
                l.source_log_name,
                l.unit,
                l.availability_status,
                l.omission_reason,
                l.physical_geometry_status,
                b.axis_id,
                b.status AS binding_status
            FROM core.scan_log l
            JOIN core.release_dataset rd
                ON rd.dataset_revision_id = l.dataset_revision_id
            JOIN core.active_release ar
                ON ar.release_id = rd.release_id
            LEFT JOIN core.log_axis_binding b
                ON b.log_id = l.id
            WHERE ar.singleton
              AND l.dataset_revision_id = %s
              AND l.id = %s
              AND l.log_kind = 'profile'
            """,
            (revision_id, log_id),
        ).fetchone()

        if log is None:
            return None

        if log["availability_status"] != "payload_present":
            return {
                "log_id": str(log["id"]),
                "source_log_name": log["source_log_name"],
                "status": "metadata_only",
                "reason": log["omission_reason"],
                "physical_geometry_status": log[
                    "physical_geometry_status"
                ],
                "items": [],
                "next_after_sample": None,
            }

        if (
            log["binding_status"] != "verified"
            or str(log["axis_id"]) != str(axis_id)
        ):
            return {
                "log_id": str(log["id"]),
                "source_log_name": log["source_log_name"],
                "status": "axis_binding_unavailable",
                "physical_geometry_status": log[
                    "physical_geometry_status"
                ],
                "items": [],
                "next_after_sample": None,
            }

        sample_rows = conn.execute(
            """
            SELECT
                sample_no,
                md_m
            FROM core.scan_sample
            WHERE axis_id = %s
              AND sample_no > %s
              AND (%s::float8 IS NULL OR md_m >= %s)
              AND (%s::float8 IS NULL OR md_m <= %s)
            ORDER BY sample_no
            LIMIT %s
            """,
            (
                axis_id,
                after_sample,
                from_m,
                from_m,
                to_m,
                to_m,
                limit + 1,
            ),
        ).fetchall()

        if not sample_rows:
            return {
                "log_id": str(log["id"]),
                "source_log_name": log["source_log_name"],
                "status": "available_uncalibrated_profile",
                "physical_geometry_status": log[
                    "physical_geometry_status"
                ],
                "items": [],
                "next_after_sample": None,
            }

        has_more = len(sample_rows) > limit
        selected = sample_rows[:limit]

        wanted = {
            row["sample_no"]: row["md_m"]
            for row in selected
        }

        lower = min(wanted)
        upper = max(wanted)

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
              AND c.source_row_to_exclusive > %s
              AND c.source_row_from <= %s
            ORDER BY c.source_row_from
            """,
            (
                log_id,
                axis_id,
                lower,
                upper,
            ),
        ).fetchall()

        items = []

        for chunk in chunks:
            asset, path = local_asset(
                conn,
                chunk["asset_id"],
            )

            if path is None:
                raise ValueError(
                    "Profile asset is unavailable"
                )

            stat = path.stat()

            table = parquet_group(
                str(path),
                chunk["sha256"],
                stat.st_size,
                stat.st_mtime_ns,
                chunk["row_group"],
            )

            for row in table.to_pylist():
                sample_no = row["sample_no"]

                if sample_no not in wanted:
                    continue

                values = row["values"]

                items.append(
                    {
                        "sample_no": sample_no,
                        "depth_m": wanted[sample_no],
                        "values": values,
                        "value_count": len(values),
                        "status": "available_uncalibrated_profile",
                    }
                )

        items.sort(
            key=lambda item: item["sample_no"]
        )

        return {
            "log_id": str(log["id"]),
            "source_log_name": log["source_log_name"],
            "unit": log["unit"],
            "status": "available_uncalibrated_profile",
            "physical_geometry_status": log[
                "physical_geometry_status"
            ],
            "items": items,
            "next_after_sample": (
                selected[-1]["sample_no"]
                if has_more
                else None
            ),
        }


def get_backend_health():
    from app.media_reader import ETL4_ROOT

    with get_connection() as conn:
        database = conn.execute(
            """
            SELECT
                current_database() AS database,
                current_user AS role
            """
        ).fetchone()

        release = conn.execute(
            """
            SELECT ar.release_id
            FROM core.active_release ar
            WHERE ar.singleton
            """
        ).fetchone()

    return {
        "database": database["database"],
        "database_role": database["role"],
        "release_id": (
            str(release["release_id"])
            if release is not None
            else None
        ),
        "asset_root_available": ETL4_ROOT.exists(),
    }

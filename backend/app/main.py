import math
from uuid import UUID

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response

from app.repository import (
    get_all_holes,
    get_hole_by_id,
    get_profile_by_hole_id,
    get_sample_by_revision,
    get_image_asset_for_active_release,
    get_datasets_by_hole,
    get_logs_by_revision,
    get_samples_by_revision,
    get_intervals_by_revision,
    get_issues_by_revision,
    get_nearby_holes,
    get_boreholes_v1,
    get_scalar_log_values,
    get_profile_log_values,
    get_backend_health,
)

from app.models import (
    Hole,
    MineralProfile,
    GeoJSONFeatureCollection,
)


app = FastAPI(
    title="HyLogger Explorer API",
    description="Backend API for the GSWA HyLogger visualisation platform",
    version="0.1.0",
)


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def root():
    return {
        "message": "HyLogger Explorer API is running"
    }


@app.get("/api/health")
def health_check():
    try:
        health = get_backend_health()

        status = (
            "ok"
            if health["release_id"] is not None
            and health["asset_root_available"]
            else "degraded"
        )

        return {
            "status": status,
            "service": "HyLogger Explorer API",
            **health,
        }

    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail="Backend dependency check failed",
        ) from exc


@app.get("/api/holes", response_model=list[Hole])
def get_holes():
    return get_all_holes()


@app.get(
    "/api/holes/geojson",
    response_model=GeoJSONFeatureCollection
)
def get_holes_geojson():
    features = []

    for hole in get_all_holes():
        features.append(
            {
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [
                        hole["longitude"],
                        hole["latitude"],
                    ],
                },
                "properties": {
                    "hole_id": hole["hole_id"],
                    "project": hole["project"],
                    "scan_from_m": hole["scan_from_m"],
                    "scan_to_m": hole["scan_to_m"],
                    "instrument": hole["instrument"],
                },
            }
        )

    return {
        "type": "FeatureCollection",
        "features": features,
    }


@app.get("/api/holes/{hole_id}", response_model=Hole)
def get_hole(hole_id: str):
    hole = get_hole_by_id(hole_id)

    if hole is None:
        raise HTTPException(
            status_code=404,
            detail="Drillhole not found"
        )

    return hole


@app.get(
    "/api/holes/{hole_id}/profile",
    response_model=list[MineralProfile]
)
def get_profile(
    hole_id: str,
    from_depth: float | None = None,
    to_depth: float | None = None,
):
    profile = get_profile_by_hole_id(hole_id)

    if profile is None:
        raise HTTPException(
            status_code=404,
            detail="Mineral profile not found"
        )

    if from_depth is not None:
        profile = [
            item for item in profile
            if item["to_depth_m"] >= from_depth
        ]

    if to_depth is not None:
        profile = [
            item for item in profile
            if item["from_depth_m"] <= to_depth
        ]

    return profile


@app.get("/v1/datasets/{revision_id}/samples/{sample_no}")
def get_dataset_sample(
    revision_id: UUID,
    sample_no: int,
    axis_id: UUID,
    log_ids: list[UUID] | None = Query(default=None),
    image_log_id: UUID | None = None,
):
    if sample_no < 0:
        raise HTTPException(
            status_code=422,
            detail="sample_no must be zero or greater",
        )

    try:
        result = get_sample_by_revision(
            revision_id=str(revision_id),
            axis_id=str(axis_id),
            sample_no=sample_no,
            log_ids=(
                [str(log_id) for log_id in log_ids]
                if log_ids is not None
                else None
            ),
            image_log_id=(
                str(image_log_id)
                if image_log_id is not None
                else None
            ),
        )

    except ValueError as exc:
        message = str(exc)

        not_found_errors = {
            "Release, dataset and axis do not belong to the same selection",
            "Selected source sample does not exist",
            "Result log is outside selected dataset revision",
            "Image log is outside selected dataset revision",
        }

        if message in not_found_errors:
            raise HTTPException(
                status_code=404,
                detail=message,
            ) from exc

        raise HTTPException(
            status_code=503,
            detail=message,
        ) from exc

    if result is None:
        raise HTTPException(
            status_code=404,
            detail="Dataset revision not found in active release",
        )

    return result



@app.get("/v1/image-assets/{asset_id}/content")
def get_image_asset_content(asset_id: UUID):
    try:
        asset = get_image_asset_for_active_release(str(asset_id))

    except ValueError as exc:
        raise HTTPException(
            status_code=503,
            detail=str(exc),
        ) from exc

    if asset is None:
        raise HTTPException(
            status_code=404,
            detail="Image asset not found in active release",
        )

    headers = {
        "ETag": f'"{asset["sha256"]}"',
    }

    if "content" in asset:
        return Response(
            content=asset["content"],
            media_type=asset["media_type"],
            headers=headers,
        )

    return FileResponse(
        path=asset["path"],
        media_type=asset["media_type"],
        headers=headers,
    )



@app.get("/v1/boreholes/{hole_id}/datasets")
def get_borehole_datasets(hole_id: str):
    rows = get_datasets_by_hole(hole_id)

    if not rows:
        raise HTTPException(
            status_code=404,
            detail="Borehole not found in active release",
        )

    return {
        "hole_id": hole_id,
        "items": rows,
    }



@app.get("/v1/datasets/{revision_id}/logs")
def get_dataset_logs(revision_id: UUID):
    rows = get_logs_by_revision(str(revision_id))

    if rows is None:
        raise HTTPException(
            status_code=404,
            detail="Dataset revision not found in active release",
        )

    return {
        "dataset_revision_id": str(revision_id),
        "items": rows,
    }



@app.get("/v1/datasets/{revision_id}/samples")
def get_dataset_samples(
    revision_id: UUID,
    axis_id: UUID,
    from_m: float | None = None,
    to_m: float | None = None,
    after_sample: int = Query(default=-1, ge=-1),
    limit: int = Query(default=1000, ge=1, le=5000),
):
    if (
        from_m is not None
        and to_m is not None
        and from_m > to_m
    ):
        raise HTTPException(
            status_code=422,
            detail="from_m cannot be greater than to_m",
        )

    result = get_samples_by_revision(
        revision_id=str(revision_id),
        axis_id=str(axis_id),
        from_m=from_m,
        to_m=to_m,
        after_sample=after_sample,
        limit=limit,
    )

    if result is None:
        raise HTTPException(
            status_code=404,
            detail="Dataset revision or axis not found in active release",
        )

    return {
        "dataset_revision_id": str(revision_id),
        "axis_id": str(axis_id),
        "coordinate_kind": "measured_depth_m",
        **result,
    }



@app.get("/v1/datasets/{revision_id}/intervals")
def get_dataset_intervals(
    revision_id: UUID,
    axis_id: UUID,
    kind: str | None = None,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=200, ge=1, le=1000),
):
    if kind not in (None, "tray", "section"):
        raise HTTPException(
            status_code=422,
            detail="kind must be tray or section",
        )

    result = get_intervals_by_revision(
        revision_id=str(revision_id),
        axis_id=str(axis_id),
        kind=kind,
        offset=offset,
        limit=limit,
    )

    if result is None:
        raise HTTPException(
            status_code=404,
            detail="Dataset revision or axis not found in active release",
        )

    return {
        "dataset_revision_id": str(revision_id),
        "axis_id": str(axis_id),
        "interval_bounds": "inclusive sample indices",
        **result,
    }



@app.get("/v1/datasets/{revision_id}/issues")
def get_dataset_issues(revision_id: UUID):
    result = get_issues_by_revision(str(revision_id))

    if result is None:
        raise HTTPException(
            status_code=404,
            detail="Dataset revision not found in active release",
        )

    return {
        "dataset_revision_id": str(revision_id),
        **result,
    }



@app.get("/v1/boreholes/nearby")
def get_nearby_boreholes(
    latitude: float,
    longitude: float,
    radius_km: float = Query(default=25.0, gt=0, le=500),
    limit: int = Query(default=20, ge=1, le=100),
):
    if not -90 <= latitude <= 90:
        raise HTTPException(
            status_code=422,
            detail="latitude must be between -90 and 90",
        )

    if not -180 <= longitude <= 180:
        raise HTTPException(
            status_code=422,
            detail="longitude must be between -180 and 180",
        )

    rows = get_nearby_holes(
        latitude=latitude,
        longitude=longitude,
        radius_km=radius_km,
        limit=limit,
    )

    return {
        "latitude": latitude,
        "longitude": longitude,
        "radius_km": radius_km,
        "items": rows,
    }



@app.get("/v1/boreholes")
def get_boreholes_catalogue(
    bbox: str | None = None,
):
    west = south = east = north = None

    if bbox is not None:
        try:
            west, south, east, north = map(
                float,
                bbox.split(","),
            )
        except (ValueError, TypeError):
            raise HTTPException(
                status_code=422,
                detail="bbox must be west,south,east,north",
            )

        values = [west, south, east, north]

        if (
            not all(math.isfinite(value) for value in values)
            or not -180 <= west <= east <= 180
            or not -90 <= south <= north <= 90
        ):
            raise HTTPException(
                status_code=422,
                detail="Invalid bounding box",
            )

    rows = get_boreholes_v1(
        west=west,
        south=south,
        east=east,
        north=north,
    )

    return {
        "items": rows,
    }



@app.get("/v1/datasets/{revision_id}/logs/{log_id}/values")
def get_dataset_scalar_values(
    revision_id: UUID,
    log_id: UUID,
    axis_id: UUID,
    from_m: float | None = None,
    to_m: float | None = None,
    after_sample: int = Query(default=-1, ge=-1),
    limit: int = Query(default=1000, ge=1, le=5000),
):
    if (
        from_m is not None
        and not math.isfinite(from_m)
    ):
        raise HTTPException(
            status_code=422,
            detail="from_m must be finite",
        )

    if (
        to_m is not None
        and not math.isfinite(to_m)
    ):
        raise HTTPException(
            status_code=422,
            detail="to_m must be finite",
        )

    if (
        from_m is not None
        and to_m is not None
        and from_m > to_m
    ):
        raise HTTPException(
            status_code=422,
            detail="from_m cannot be greater than to_m",
        )

    try:
        result = get_scalar_log_values(
            revision_id=str(revision_id),
            log_id=str(log_id),
            axis_id=str(axis_id),
            from_m=from_m,
            to_m=to_m,
            after_sample=after_sample,
            limit=limit,
        )

    except ValueError as exc:
        raise HTTPException(
            status_code=503,
            detail=str(exc),
        ) from exc

    if result is None:
        raise HTTPException(
            status_code=404,
            detail="Scalar log not found in active release",
        )

    return {
        "dataset_revision_id": str(revision_id),
        "axis_id": str(axis_id),
        **result,
    }



@app.get("/v1/datasets/{revision_id}/profile-logs/{log_id}/values")
def get_dataset_profile_values(
    revision_id: UUID,
    log_id: UUID,
    axis_id: UUID,
    from_m: float | None = None,
    to_m: float | None = None,
    after_sample: int = Query(default=-1, ge=-1),
    limit: int = Query(default=200, ge=1, le=1000),
):
    if (
        from_m is not None
        and not math.isfinite(from_m)
    ):
        raise HTTPException(
            status_code=422,
            detail="from_m must be finite",
        )

    if (
        to_m is not None
        and not math.isfinite(to_m)
    ):
        raise HTTPException(
            status_code=422,
            detail="to_m must be finite",
        )

    if (
        from_m is not None
        and to_m is not None
        and from_m > to_m
    ):
        raise HTTPException(
            status_code=422,
            detail="from_m cannot be greater than to_m",
        )

    try:
        result = get_profile_log_values(
            revision_id=str(revision_id),
            log_id=str(log_id),
            axis_id=str(axis_id),
            from_m=from_m,
            to_m=to_m,
            after_sample=after_sample,
            limit=limit,
        )

    except ValueError as exc:
        raise HTTPException(
            status_code=503,
            detail=str(exc),
        ) from exc

    if result is None:
        raise HTTPException(
            status_code=404,
            detail="Profile log not found in active release",
        )

    return {
        "dataset_revision_id": str(revision_id),
        "axis_id": str(axis_id),
        **result,
    }

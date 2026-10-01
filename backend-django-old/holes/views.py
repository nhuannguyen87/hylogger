"""
The API. Every endpoint is a plain function - read it top to bottom.

    GET /api/holes/                        list holes (for the map)
    GET /api/holes/<id>/                   one hole's details
    GET /api/holes/<id>/measurements/      the depth log
    GET /api/holes/<id>/trays/             real core-tray photos + per-tray minerals (few holes)
    GET /api/holes/<id>/anomalies/         only the flagged intervals
    GET /api/holes/<id>/trace/             3D line points for deck.gl
    GET /api/holes/<id>/nearby/?km=25      other holes within N km (PostGIS)
    GET /api/holes/<id>/spectral-sample/   real VSWIR/TIR spectrum: instant for 5 holes
                                            (database5553/) + 15 holes (data5553/), live
                                            from NVCL for every other one
    GET /api/holes/<id>/core-strip/        continuous core-photo + TSG strip (core_strip.py output)
    GET /api/mineral-logs/                 every hole's mineral log at once, merged into runs (map)
    GET /api/distance/?a=H001&b=H002       distance between two holes (PostGIS)
    GET /api/stats/                        counts for the header
"""

import json
import logging
import sys
import threading
from pathlib import Path

from django.conf import settings
from django.core.cache import cache
from django.db import connection
from django.db.models import Count, Max, Min, Q
from django.db.models.functions import Greatest
from django.shortcuts import get_object_or_404
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .etl4_bridge import fetch_spectral_sample as fetch_etl4_spectral_sample
from .geo import trace_points
from .nvcl_bridge import fetch_spectral_sample_cached, get_reader, own_datasets
from .tsg_bridge import fetch_spectral_sample as fetch_tsg_spectral_sample
from .models import CoreTray, Hole, Measurement
from .serializers import (
    CoreTraySerializer,
    HoleDetailSerializer,
    HoleListSerializer,
    MeasurementSerializer,
    MeasurementWithFeaturesSerializer,
)

# core_strip.py and nvcl_datasets.py live at the project root, not inside
# backend-django-old/ - same sys.path bootstrap nvcl_bridge.py already uses to reach
# root-level scripts.
sys.path.insert(0, str(settings.PROJECT_ROOT))
from core_strip import build_master_hole_strip  # noqa: E402
from nvcl_datasets import SAME_HOLE_KM, distance_km  # noqa: E402

logger = logging.getLogger(__name__)

# Core-photo strips built on first request, in a background thread (a hole's
# ~90 NVCL tray photos take about a minute). hole_id -> None while building,
# else why it failed - kept until the server restarts, so a hole NVCL has no
# photos for isn't retried on every click.
_strip_builds = {}
_strip_lock = threading.Lock()
_strip_slots = threading.BoundedSemaphore(2)  # each build already runs 8 NVCL downloads


def holes_with_depth():
    """
    Every hole, plus how deep it goes as far as we know:

        logged_from_m, logged_to_m   top and bottom of its measurements
        drawn_length_m               how far down to draw it

    borehole_length_m is the GSWA catalog's "Total (m)" - metres of core
    SCANNED (Depth to - Depth from), not the hole's depth. Logging often
    starts well below the collar (05KCD001: 142 m scanned, logged
    59.5-201.5 m), so a hole is drawn to that or its deepest logged metre,
    whichever is further down.

    The hole serializers read these, so fetch holes through here for them.
    """
    return Hole.objects.annotate(
        logged_from_m=Min("measurements__depth_from_m"),
        logged_to_m=Max("measurements__depth_to_m"),
    ).annotate(
        # Postgres' GREATEST skips NULLs, so a hole with no measurements is
        # still drawn to borehole_length_m (and vice versa)
        drawn_length_m=Greatest("borehole_length_m", "logged_to_m"),
    ).order_by("hole_id")  # Django drops Meta.ordering from GROUP BY queries


@api_view(["GET"])
def hole_list(request):
    """
    Optional query parameters:
        ?search=bindi     match on hole id or name
        ?limit=200        cap the number returned (default 1000)
        ?anomalies_only=1 only holes that have at least one flagged interval
    """
    holes = holes_with_depth()

    search = request.GET.get("search", "").strip()
    if search:
        holes = holes.filter(Q(hole_id__icontains=search) | Q(hole_name__icontains=search))

    if request.GET.get("anomalies_only") == "1":
        # a subquery, not a join on measurements - that would multiply the
        # rows holes_with_depth() aggregates over
        flagged = Measurement.objects.filter(is_anomaly=True).values("hole_id")
        holes = holes.filter(hole_id__in=flagged)

    limit = int(request.GET.get("limit", 1000))
    holes = holes[:limit]

    return Response(HoleListSerializer(holes, many=True).data)


@api_view(["GET"])
def hole_detail(request, hole_id):
    hole = get_object_or_404(
        holes_with_depth().annotate(
            measurement_count=Count("measurements", distinct=True),
            anomaly_count=Count("measurements", filter=Q(measurements__is_anomaly=True), distinct=True),
        ),
        pk=hole_id,
    )
    return Response(HoleDetailSerializer(hole).data)


@api_view(["GET"])
def hole_measurements(request, hole_id):
    """?with_features=1 also returns the raw band values."""
    get_object_or_404(Hole, pk=hole_id)
    rows = Measurement.objects.filter(hole_id=hole_id).order_by("depth_from_m")

    serializer_class = (
        MeasurementWithFeaturesSerializer
        if request.GET.get("with_features") == "1"
        else MeasurementSerializer
    )
    return Response(serializer_class(rows, many=True).data)


@api_view(["GET"])
def hole_trays(request, hole_id):
    """Real core-tray photos + per-tray mineral calls, where we have them.

    Empty for most holes - only loaded so far for holes whose HyLogger
    package included a processed mineral-by-tray table (see
    management/commands/load_core_trays.py). An empty list here is
    normal, not an error; the frontend treats it that way.
    """
    get_object_or_404(Hole, pk=hole_id)
    rows = CoreTray.objects.filter(hole_id=hole_id).order_by("depth_from_m")
    return Response(CoreTraySerializer(rows, many=True).data)


@api_view(["GET"])
def hole_anomalies(request, hole_id):
    rows = (
        Measurement.objects.filter(hole_id=hole_id, is_anomaly=True)
        .order_by("-anomaly_score")
    )
    return Response(MeasurementSerializer(rows, many=True).data)


@api_view(["GET"])
def hole_trace(request, hole_id):
    """
    Points down the hole for the 3D view, in metres from the collar.
    Each point also carries the mineral and anomaly score at that depth, so
    deck.gl can colour the line without a second request.

    Runs down to drawn_length_m, not borehole_length_m - see holes_with_depth().
    """
    hole = get_object_or_404(holes_with_depth(), pk=hole_id)
    step = float(request.GET.get("step_m", 5))

    points = trace_points(
        hole.inclination_deg, hole.azimuth_deg, hole.drawn_length_m or 0, step_m=step
    )

    # attach the measurement that covers each point's depth
    logs = list(Measurement.objects.filter(hole_id=hole_id).order_by("depth_from_m"))
    for point in points:
        match = next(
            (m for m in logs if m.depth_from_m <= point["depth_m"] < m.depth_to_m), None
        )
        point["mineral"] = match.mineral_1 if match else ""
        point["confidence"] = match.confidence if match else 0.0
        point["anomaly_score"] = (match.anomaly_score if match else None) or 0.0
        point["is_anomaly"] = bool(match.is_anomaly) if match else False

    return Response({
        "hole_id": hole.hole_id,
        "hole_name": hole.hole_name,
        "latitude": hole.latitude,
        "longitude": hole.longitude,
        "elevation_m": hole.elevation_m,
        "points": points,
    })


def merge_mineral_runs(min_confidence):
    """
    Every hole's measurements as runs of the same call, shallow to deep:
    consecutive, touching intervals with the same mineral_1 (or both
    uncertain) become one [from_m, to_m, group] - 49k one-metre intervals
    come out as ~11k runs. Uncertain means what StripLog.jsx hatches:
    quality_flag "missing" or confidence under min_confidence.
    """
    groups, index, holes = [], {}, {}
    rows = Measurement.objects.order_by("hole_id", "depth_from_m").values_list(
        "hole_id", "depth_from_m", "depth_to_m", "mineral_1", "confidence", "quality_flag"
    )
    for hole_id, top, bottom, mineral, confidence, quality in rows.iterator(chunk_size=10000):
        if quality == "missing" or (confidence or 0.0) < min_confidence:
            code = None
        else:
            if mineral not in index:
                index[mineral] = len(groups)
                groups.append(mineral)
            code = index[mineral]
        runs = holes.setdefault(hole_id, [])
        if runs and runs[-1][2] == code and abs(runs[-1][1] - top) < 1e-6:
            runs[-1][1] = round(bottom, 3)
        else:
            runs.append([round(top, 3), round(bottom, 3), code])
    return {"groups": groups, "holes": holes}


@api_view(["GET"])
def mineral_logs(request):
    """
    Every hole's mineral log in one response, for the map to colour each 3D
    core on first load instead of only the hole you click (whose
    /measurements/ the detail panel fetches).

        ?min_confidence=0.5   below this an interval counts as uncertain -
                              pass the frontend's CONFIDENCE_THRESHOLD, so the
                              map greys out the same intervals StripLog hatches

        {"groups": ["CHLORITE", ...],
         "holes": {"05GJD001": [[150.0, 163.0, 0], [163.0, 164.0, null], ...]}}

    A run is [from_m, to_m, index into "groups"], or null when uncertain.
    Holes with no measurements aren't listed. Cached until the measurements
    change - load_data replaces them, which moves their count and max id.
    """
    try:
        min_confidence = float(request.GET.get("min_confidence", 0.5))
    except ValueError:
        return Response({"detail": "min_confidence must be a number"}, status=400)

    version = Measurement.objects.aggregate(count=Count("id"), last=Max("id"))
    key = f"mineral-logs:{version['count']}:{version['last']}:{min_confidence}"
    payload = cache.get(key)
    if payload is None:
        payload = merge_mineral_runs(min_confidence)
        cache.set(key, payload, timeout=None)
    return Response(payload)


@api_view(["GET"])
def distance(request):
    """
    Straight-line distance across the earth's surface between two collars.
    PostGIS does the maths - don't reimplement this in JavaScript.
    """
    a_id = request.GET.get("a")
    b_id = request.GET.get("b")
    if not a_id or not b_id:
        return Response({"detail": "Pass ?a=HOLE_ID&b=HOLE_ID"}, status=400)

    a = get_object_or_404(holes_with_depth(), pk=a_id)
    b = get_object_or_404(holes_with_depth(), pk=b_id)

    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT ST_DistanceSphere(
                ST_SetSRID(ST_MakePoint(%s, %s), 4326),
                ST_SetSRID(ST_MakePoint(%s, %s), 4326)
            )
            """,
            [a.longitude, a.latitude, b.longitude, b.latitude],
        )
        metres = cursor.fetchone()[0]

    return Response({
        "hole_a": HoleListSerializer(a).data,
        "hole_b": HoleListSerializer(b).data,
        "distance_m": round(metres, 1),
        "distance_km": round(metres / 1000, 3),
    })


@api_view(["GET"])
def hole_nearby(request, hole_id):
    """?km=25 - other holes within that radius, nearest first."""
    hole = get_object_or_404(Hole, pk=hole_id)
    radius_km = float(request.GET.get("km", 25))

    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT hole_id, hole_name, latitude, longitude, borehole_length_m,
                   ST_DistanceSphere(
                       ST_SetSRID(ST_MakePoint(longitude, latitude), 4326),
                       ST_SetSRID(ST_MakePoint(%s, %s), 4326)
                   ) AS metres
            FROM holes
            WHERE hole_id <> %s
              -- ST_DWithin on geography is the bit that uses the spatial index
              AND ST_DWithin(
                      ST_SetSRID(ST_MakePoint(longitude, latitude), 4326)::geography,
                      ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography,
                      %s
                  )
            ORDER BY metres
            LIMIT 50
            """,
            [hole.longitude, hole.latitude, hole_id,
             hole.longitude, hole.latitude, radius_km * 1000],
        )
        columns = [c[0] for c in cursor.description]
        rows = [dict(zip(columns, row)) for row in cursor.fetchall()]

    for row in rows:
        row["distance_km"] = round(row.pop("metres") / 1000, 3)

    return Response(rows)


@api_view(["GET"])
def hole_spectral_sample(request, hole_id):
    """
    Real VSWIR/TIR spectrum + mineral call nearest ?depth_m=, tried in order:
      1. the 5 holes restored from database5553/ (instant, etl4_bridge.py)
      2. the 15 holes with a raw TSG package in data5553/ (instant, a local
         file read - tsg_bridge.py)
      3. every other hole: a live NVCL lookup (a few seconds the first time,
         then cached - nvcl_bridge.py)
    404 only if none of the three has anything - a hole with no spectral log
    at all, or NVCL unreachable - which the frontend treats as a normal,
    expected state.
    """
    depth_m = request.GET.get("depth_m")
    depth_m = float(depth_m) if depth_m is not None else None

    sample = fetch_etl4_spectral_sample(hole_id, depth_m)
    if sample is None:
        sample = fetch_tsg_spectral_sample(hole_id, depth_m)
    if sample is None:
        sample = fetch_spectral_sample_cached(hole_id, depth_m)
    if sample is None:
        return Response({"detail": "No spectral data available for this hole."}, status=404)
    return Response(sample)


def photos_from_elsewhere(hole, table):
    """
    Why a core-photo strip's photos aren't this hole's, or None if they are.

    One NVCL id can cover two different holes. "BH01" here is a 3.6 m
    Bunbury bore, but NVCL also files a Behemoth hole 1,250 km away under
    it (dataset "BH01_Behemoth"), and that hole's photos were once served
    as BH01's. So core_strip.py records the NVCL datasets its photos came
    from, and each must be this hole's: named after it, or with a TSG
    header collar within SAME_HOLE_KM of the holes table's collar.
    """
    sources = table.get("source_datasets")
    if not sources:
        return ("This core-photo strip doesn't record which NVCL datasets its photos came from, "
                "so it can't be matched to this hole - rebuild it with core_strip.py.")
    for source in sources:
        name = source.get("dataset_name")
        if name == hole.hole_id:
            continue
        collar = source.get("tsg_collar")
        km = distance_km(tuple(collar), (hole.latitude, hole.longitude)) if collar else None
        if km is None:
            return (f"These core photos are from NVCL dataset {name}, which isn't named {hole.hole_id} "
                    "and doesn't record its collar, so they can't be matched to this hole.")
        if km > SAME_HOLE_KM:
            return (f"These core photos are from NVCL dataset {name}, whose collar is {km:,.1f} km "
                    f"from {hole.hole_id}'s - a different hole, so they aren't shown here.")
    return None


@api_view(["GET"])
def hole_core_strip(request, hole_id):
    """
    Continuous core-photo strip, built by core_strip.py (repo root) from
    real GSWA/NVCL tray photos - see that file for how depth maps to pixels.

    Any hole works, not just pre-built ones: the first request starts a
    background build (build_core_strip - the hole's NVCL tray photos,
    streamed, ~1 min) and answers 202 {"status": "building"}; the frontend
    polls until the strip is on disk, then it's served from there on every
    later request. `core_strip.py --all` pre-builds the downloaded holes so
    they never wait. 404 for a hole that isn't in the holes table, one NVCL
    has no tray photos for, or one whose strip holds another hole's photos
    (photos_from_elsewhere) - the frontend treats all of those as a normal,
    expected state, same convention as spectral-sample above.
    """
    hole = get_object_or_404(Hole, pk=hole_id)
    strip_dir = Path(settings.DATA_DIR) / "media" / "core_strips" / hole_id
    table_path = strip_dir / "depth_pixel_table.json"
    if not table_path.is_file():
        with _strip_lock:
            if hole_id not in _strip_builds:
                _strip_builds[hole_id] = None
                threading.Thread(target=build_core_strip, args=(hole_id,), daemon=True).start()
            failed = _strip_builds[hole_id]
        if failed:
            return Response({"detail": failed}, status=404)
        return Response({"status": "building",
                         "detail": "Building this hole's core photo from its NVCL tray photos."}, status=202)

    with table_path.open(encoding="utf-8-sig") as handle:
        table = json.load(handle)

    elsewhere = photos_from_elsewhere(hole, table)
    if elsewhere:
        return Response({"detail": elsewhere}, status=404)

    base = f"{settings.MEDIA_URL}core_strips/{hole_id}/"
    sheets = [
        {
            "sheet_index": sheet["sheet_index"],
            "depth_from_m": sheet["depth_from_m"],
            "depth_to_m": sheet["depth_to_m"],
            "width_px": sheet["width_px"],
            "height_px": sheet["height_px"],
            "photo_url": base + sheet["file"],
            # null when NVCL has no TSA mineral log for this hole's photos
            "tsg_url": (base + tsg.name) if (tsg := strip_dir / f"tsg_sheet_{sheet['sheet_index']}.png").is_file() else None,
        }
        for sheet in table["sheets"]
    ]
    # Per-row breakpoints too (not just the per-sheet summary) - the 3D view
    # needs these to crop an exact depth-range slice out of a sheet image for
    # texturing a cylinder segment, the same depth<->pixel mapping
    # depth_lookup.py itself uses.
    rows = [
        {
            "sheet_index": row["sheet_index"],
            "depth_from_m": row["depth_from_m"],
            "depth_to_m": row["depth_to_m"],
            "y_from_px": row["y_from_px"],
            "y_to_px": row["y_to_px"],
        }
        for row in table["rows"]
    ]
    return Response({
        "hole_id": hole_id,
        "source_datasets": table["source_datasets"],
        "depth_min_m": table["depth_min_m"],
        "depth_max_m": table["depth_max_m"],
        "core_width_px": table["core_width_px"],
        "sheets": sheets,
        "rows": rows,
    })


def build_core_strip(hole_id):
    """Background thread for hole_core_strip: the hole's photo strip (and its
    mineral strip, where NVCL has a TSA log). A hole with no data/raw folder
    (catalog-only) gets its NVCL datasets looked up live."""
    try:
        with _strip_slots:
            datasets = None
            if not (Path(settings.DATA_DIR) / "raw" / hole_id / "datasets.json").is_file():
                found = own_datasets(get_reader(), hole_id)
                if found is None:
                    raise ValueError("NVCL has no datasets for this hole")
                datasets = found[1]
            build_master_hole_strip(hole_id, datasets=datasets, reader=get_reader())
        with _strip_lock:
            _strip_builds.pop(hole_id, None)
    except Exception as exc:  # noqa: BLE001 - any failure is "no photos", never a crash
        logger.warning("core strip for %s failed: %s", hole_id, exc)
        with _strip_lock:
            _strip_builds[hole_id] = f"No core photos available for this hole: {exc}"
    finally:
        connection.close()  # this thread's own DB connection (own_datasets reads Hole)


@api_view(["GET"])
def stats(request):
    return Response({
        "holes": Hole.objects.count(),
        "measurements": Measurement.objects.count(),
        "anomalies": Measurement.objects.filter(is_anomaly=True).count(),
        "low_confidence": Measurement.objects.filter(confidence__lt=0.5).count(),
    })

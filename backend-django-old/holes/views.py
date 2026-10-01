"""
The API. Every endpoint is a plain function - read it top to bottom.

    GET /api/holes/                        list holes (for the map)
    GET /api/holes/<id>/                   one hole's details
    GET /api/holes/<id>/measurements/      the depth log
    GET /api/holes/<id>/anomalies/         only the flagged intervals
    GET /api/holes/<id>/trace/             3D line points for deck.gl
    GET /api/holes/<id>/nearby/?km=25      other holes within N km (PostGIS)
    GET /api/distance/?a=H001&b=H002       distance between two holes (PostGIS)
    GET /api/stats/                        counts for the header
"""

from django.db import connection
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .geo import trace_points
from .models import Hole, Measurement
from .serializers import (
    HoleDetailSerializer,
    HoleListSerializer,
    MeasurementSerializer,
    MeasurementWithFeaturesSerializer,
)


@api_view(["GET"])
def hole_list(request):
    """
    Optional query parameters:
        ?search=bindi     match on hole id or name
        ?limit=200        cap the number returned (default 1000)
        ?anomalies_only=1 only holes that have at least one flagged interval
    """
    holes = Hole.objects.all()

    search = request.GET.get("search", "").strip()
    if search:
        holes = holes.filter(Q(hole_id__icontains=search) | Q(hole_name__icontains=search))

    if request.GET.get("anomalies_only") == "1":
        holes = holes.filter(measurements__is_anomaly=True).distinct()

    limit = int(request.GET.get("limit", 1000))
    holes = holes[:limit]

    return Response(HoleListSerializer(holes, many=True).data)


@api_view(["GET"])
def hole_detail(request, hole_id):
    hole = get_object_or_404(
        Hole.objects.annotate(
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
    """
    hole = get_object_or_404(Hole, pk=hole_id)
    step = float(request.GET.get("step_m", 5))

    points = trace_points(
        hole.inclination_deg, hole.azimuth_deg, hole.borehole_length_m or 0, step_m=step
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

    a = get_object_or_404(Hole, pk=a_id)
    b = get_object_or_404(Hole, pk=b_id)

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
def stats(request):
    return Response({
        "holes": Hole.objects.count(),
        "measurements": Measurement.objects.count(),
        "anomalies": Measurement.objects.filter(is_anomaly=True).count(),
        "low_confidence": Measurement.objects.filter(confidence__lt=0.5).count(),
    })

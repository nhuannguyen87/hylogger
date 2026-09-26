"""
Serializers decide the exact JSON shape the website receives.
If the frontend needs a new field, add it here first.
"""

from rest_framework import serializers

from .models import Hole, Measurement


class HoleListSerializer(serializers.ModelSerializer):
    """Small payload - used for the map, where we send hundreds of holes at once."""

    class Meta:
        model = Hole
        fields = ["hole_id", "hole_name", "latitude", "longitude", "borehole_length_m"]


class HoleDetailSerializer(serializers.ModelSerializer):
    measurement_count = serializers.IntegerField(read_only=True)
    anomaly_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = Hole
        fields = [
            "hole_id", "hole_name", "latitude", "longitude",
            "easting", "northing", "elevation_m", "borehole_length_m",
            "inclination_deg", "azimuth_deg",
            "measurement_count", "anomaly_count",
        ]


class MeasurementSerializer(serializers.ModelSerializer):
    class Meta:
        model = Measurement
        fields = [
            "sample_no", "depth_from_m", "depth_to_m",
            "mineral_1", "mineral_1_pct", "mineral_2", "mineral_2_pct",
            "confidence", "quality_flag",
            "anomaly_score", "is_anomaly",
        ]


class MeasurementWithFeaturesSerializer(MeasurementSerializer):
    """Same as above plus the raw band values - handy when debugging the model."""

    class Meta(MeasurementSerializer.Meta):
        fields = MeasurementSerializer.Meta.fields + ["features"]

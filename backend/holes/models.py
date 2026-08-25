"""
Two tables, on purpose. Keep it small until you need more.

    Hole         one row per drill hole (where it is, how deep, which way it points)
    Measurement  one row per depth interval down that hole

If you later add core photos or lab chemistry, add a third model rather than
piling more columns onto Measurement.
"""

from django.db import models


class Hole(models.Model):
    # hole_id is the real-world key (e.g. "H001"), so we use it as the primary key.
    hole_id = models.CharField(max_length=64, primary_key=True)
    hole_name = models.CharField(max_length=255, blank=True)

    latitude = models.FloatField()
    longitude = models.FloatField()
    easting = models.FloatField(null=True, blank=True)
    northing = models.FloatField(null=True, blank=True)

    elevation_m = models.FloatField(null=True, blank=True)
    borehole_length_m = models.FloatField(null=True, blank=True)

    # -90 = straight down, 0 = horizontal. Azimuth is degrees clockwise from north.
    inclination_deg = models.FloatField(default=-90.0)
    azimuth_deg = models.FloatField(default=0.0)

    class Meta:
        db_table = "holes"
        ordering = ["hole_id"]

    def __str__(self):
        return f"{self.hole_id} ({self.hole_name})"


class Measurement(models.Model):
    QUALITY_CHOICES = [
        ("ok", "ok"),
        ("low_signal", "low signal"),
        ("missing", "missing"),
    ]

    hole = models.ForeignKey(Hole, on_delete=models.CASCADE, related_name="measurements")
    sample_no = models.IntegerField(null=True, blank=True)

    depth_from_m = models.FloatField()
    depth_to_m = models.FloatField()

    mineral_1 = models.CharField(max_length=64, blank=True)
    mineral_1_pct = models.FloatField(null=True, blank=True)
    mineral_2 = models.CharField(max_length=64, blank=True)
    mineral_2_pct = models.FloatField(null=True, blank=True)

    # 0..1. Anything under CONFIDENCE_THRESHOLD in the frontend renders as greyed out.
    confidence = models.FloatField(default=0.0)
    quality_flag = models.CharField(max_length=32, choices=QUALITY_CHOICES, default="ok")

    # Every band_* column from your CSV lands here as {"band_2200": 0.71, ...}.
    # Using JSON means you can change how many bands you have without a migration.
    features = models.JSONField(default=dict, blank=True)

    # Filled in by: python manage.py detect_anomalies
    anomaly_score = models.FloatField(null=True, blank=True)  # 0..1, higher = odder
    is_anomaly = models.BooleanField(default=False)

    class Meta:
        db_table = "measurements"
        ordering = ["hole_id", "depth_from_m"]
        indexes = [
            models.Index(fields=["hole", "depth_from_m"]),
            models.Index(fields=["is_anomaly"]),
        ]

    def __str__(self):
        return f"{self.hole_id} {self.depth_from_m}-{self.depth_to_m} m"

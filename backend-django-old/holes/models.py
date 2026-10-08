"""
Three tables.

    Hole         one row per drill hole (where it is, how deep, which way it points)
    Measurement  one row per depth interval down that hole
    CoreTray     one row per physical core tray - a photo plus its own
                 mineral call, at tray granularity rather than Measurement's
                 per-metre bins. Real photo + real mineral data only exists
                 for a handful of holes so far (see load_core_trays), so most
                 holes simply have zero CoreTray rows.
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
    # The GSWA HyLogger catalog's "Total (m)" (= NVCL's boreholeLength_m):
    # metres of core SCANNED, i.e. Depth to - Depth from - not how deep the
    # hole goes. Logging often starts below the collar, so measurements can
    # run deeper than this. Draw holes to views.holes_with_depth()'s
    # drawn_length_m instead.
    borehole_length_m = models.FloatField(null=True, blank=True)

    # -90 = straight down, 0 = horizontal. Azimuth is degrees clockwise from north.
    inclination_deg = models.FloatField(default=-90.0)
    azimuth_deg = models.FloatField(default=0.0)

    # From WAMEX/WAPIMS in the source catalog. Shown as a badge in the UI,
    # never hidden - hiding rows would misrepresent the dataset.
    confidential = models.BooleanField(default=False)

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

    # Plain-English reason(s) the confidence isn't higher (e.g. "weak signal;
    # the readings within this metre disagree with each other"), from
    # files/extract.py's real per-check scoring. Blank when confidence is
    # high. This is what makes a low-confidence flag explainable rather than
    # just a colour - shown next to the number, not instead of it.
    why = models.TextField(blank=True)

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


class CoreTray(models.Model):
    hole = models.ForeignKey(Hole, on_delete=models.CASCADE, related_name="trays")
    tray_no = models.IntegerField()

    depth_from_m = models.FloatField()
    depth_to_m = models.FloatField()

    # Path under MEDIA_ROOT, e.g. "tray_images/KD1/KD1_0001.jpg".
    image = models.CharField(max_length=255, blank=True)

    # Two independent scans read the same tray: SWIR/VNIR (reflected light,
    # ~450-2500nm - good for clays/micas/carbonates) and TIR (thermal
    # infrared, ~6-14um - good for anhydrous silicates like quartz/feldspar
    # that SWIR/VNIR is largely blind to). Real per-tray dominant mineral +
    # TSG's internal mixture weight + what share of the tray's valid points
    # agreed, for each.
    swir_vnir_mineral = models.CharField(max_length=64, blank=True)
    swir_vnir_group = models.CharField(max_length=64, blank=True)
    swir_vnir_wt_pct = models.FloatField(null=True, blank=True)
    swir_vnir_pct_of_tray = models.FloatField(null=True, blank=True)

    tir_mineral = models.CharField(max_length=64, blank=True)
    tir_group = models.CharField(max_length=64, blank=True)
    tir_wt_pct = models.FloatField(null=True, blank=True)
    tir_pct_of_tray = models.FloatField(null=True, blank=True)

    class Meta:
        db_table = "core_trays"
        ordering = ["hole_id", "depth_from_m"]

    def __str__(self):
        return f"{self.hole_id} tray {self.tray_no}"

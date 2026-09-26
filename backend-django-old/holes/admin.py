"""Browse the data at http://localhost:8000/admin/ (create a login with
`python manage.py createsuperuser`). Handy for spot-checking your ETL output."""
from django.contrib import admin

from .models import Hole, Measurement


@admin.register(Hole)
class HoleAdmin(admin.ModelAdmin):
    list_display = ("hole_id", "hole_name", "latitude", "longitude", "borehole_length_m")
    search_fields = ("hole_id", "hole_name")


@admin.register(Measurement)
class MeasurementAdmin(admin.ModelAdmin):
    list_display = ("hole", "depth_from_m", "depth_to_m", "mineral_1", "confidence",
                    "quality_flag", "anomaly_score", "is_anomaly")
    list_filter = ("quality_flag", "is_anomaly")
    search_fields = ("hole__hole_id",)

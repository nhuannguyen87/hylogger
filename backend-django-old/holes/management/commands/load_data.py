"""
Load data/holes.csv and data/measurements.csv into Postgres.

    python manage.py load_data
    python manage.py load_data --dir /path/to/other/csvs
    python manage.py load_data --replace     # wipe the tables first

This is the seam between your existing etl.py and the website. If etl.py starts
producing extra columns, the only file you need to touch is this one.
"""

import csv
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction

from holes.models import Hole, Measurement

BATCH_SIZE = 5000


def to_float(value, default=None):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


class Command(BaseCommand):
    help = "Load holes.csv and measurements.csv into the database."

    def add_arguments(self, parser):
        parser.add_argument("--dir", default=str(settings.DATA_DIR),
                            help="Folder containing holes.csv and measurements.csv")
        parser.add_argument("--replace", action="store_true",
                            help="Delete existing rows before loading")

    def handle(self, *args, **options):
        data_dir = Path(options["dir"])
        holes_csv = data_dir / "holes.csv"
        measurements_csv = data_dir / "measurements.csv"

        if not holes_csv.exists():
            raise SystemExit(
                f"Can't find {holes_csv}.\n"
                "Run `python data/make_sample_data.py` from the project root first, "
                "or point --dir at your own CSVs."
            )

        if options["replace"]:
            self.stdout.write("Clearing existing rows...")
            Measurement.objects.all().delete()
            Hole.objects.all().delete()

        self.load_holes(holes_csv)
        if measurements_csv.exists():
            self.load_measurements(measurements_csv)
        else:
            self.stdout.write(self.style.WARNING(f"No {measurements_csv} - skipping."))

        self.stdout.write(self.style.SUCCESS(
            f"Done. {Hole.objects.count()} holes, {Measurement.objects.count()} measurements."
        ))

    @transaction.atomic
    def load_holes(self, path):
        holes = []
        with open(path, newline="") as handle:
            for row in csv.DictReader(handle):
                holes.append(Hole(
                    hole_id=row["hole_id"].strip(),
                    hole_name=row.get("hole_name", "").strip(),
                    latitude=to_float(row.get("latitude")),
                    longitude=to_float(row.get("longitude")),
                    easting=to_float(row.get("easting")),
                    northing=to_float(row.get("northing")),
                    elevation_m=to_float(row.get("elevation_m")),
                    borehole_length_m=to_float(row.get("borehole_length_m")),
                    inclination_deg=to_float(row.get("inclination_deg"), -90.0),
                    azimuth_deg=to_float(row.get("azimuth_deg"), 0.0),
                ))
        Hole.objects.bulk_create(
            holes, batch_size=BATCH_SIZE,
            update_conflicts=True,
            update_fields=["hole_name", "latitude", "longitude", "easting", "northing",
                           "elevation_m", "borehole_length_m", "inclination_deg", "azimuth_deg"],
            unique_fields=["hole_id"],
        )
        self.stdout.write(f"Loaded {len(holes)} holes.")

    def load_measurements(self, path):
        known_holes = set(Hole.objects.values_list("hole_id", flat=True))
        Measurement.objects.all().delete()

        buffer, total, skipped = [], 0, 0
        with open(path, newline="") as handle:
            reader = csv.DictReader(handle)
            # any column named band_* becomes a model feature
            band_columns = [c for c in reader.fieldnames if c.startswith("band_")]

            for row in reader:
                hole_id = row["hole_id"].strip()
                if hole_id not in known_holes:
                    skipped += 1
                    continue

                features = {}
                for column in band_columns:
                    value = to_float(row.get(column))
                    if value is not None:
                        features[column] = value

                buffer.append(Measurement(
                    hole_id=hole_id,
                    sample_no=int(to_float(row.get("sample_no"), 0)),
                    depth_from_m=to_float(row.get("depth_from_m"), 0.0),
                    depth_to_m=to_float(row.get("depth_to_m"), 0.0),
                    mineral_1=(row.get("mineral_1") or "").strip(),
                    mineral_1_pct=to_float(row.get("mineral_1_pct")),
                    mineral_2=(row.get("mineral_2") or "").strip(),
                    mineral_2_pct=to_float(row.get("mineral_2_pct")),
                    confidence=to_float(row.get("confidence"), 0.0),
                    quality_flag=(row.get("quality_flag") or "ok").strip(),
                    features=features,
                ))

                if len(buffer) >= BATCH_SIZE:
                    Measurement.objects.bulk_create(buffer)
                    total += len(buffer)
                    buffer = []

        if buffer:
            Measurement.objects.bulk_create(buffer)
            total += len(buffer)

        self.stdout.write(f"Loaded {total} measurements ({len(band_columns)} band columns).")
        if skipped:
            self.stdout.write(self.style.WARNING(
                f"Skipped {skipped} rows whose hole_id isn't in holes.csv."
            ))

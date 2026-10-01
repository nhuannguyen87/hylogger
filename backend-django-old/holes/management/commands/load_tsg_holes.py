"""
Add the 15 data5553/ TSG holes to the main catalog if they're missing from
it - these are hand-collected exploration holes that never went through the
public NVCL catalog load_catalog_holes.py covers, so without this they have
no row to show the "full spectral data" badge on at all.

    python manage.py load_tsg_holes

Never touches a hole already in the table. Collar position, depth and
orientation come from data5553/hylogger_ver1/collars.xlsx (the same
spreadsheet holes_3d.py reads); the spectra themselves are never imported
into Postgres - opening one of these holes reads its real spectrum straight
from the TSG package on demand (see tsg_bridge.py).
"""

import pandas as pd
from django.conf import settings
from django.core.management.base import BaseCommand

from holes.models import Hole
from holes.tsg_bridge import ALLOWED

COLLARS_PATH = settings.TSG_DATA_DIR / "hylogger_ver1" / "collars.xlsx"


class Command(BaseCommand):
    help = "Add any of the 15 data5553/ TSG holes missing from the main catalog."

    def add_arguments(self, parser):
        parser.add_argument("--source", default=str(COLLARS_PATH))

    def handle(self, *args, **options):
        try:
            catalog = pd.read_excel(options["source"])
        except FileNotFoundError:
            raise SystemExit(
                f"Can't find {options['source']}. This is optional - the site "
                "works fine without it, just without these 15 holes."
            )

        known_ids = set(Hole.objects.values_list("hole_id", flat=True))
        name_column = "Well Name and Info"
        added = 0

        for hole_id in ALLOWED:
            if hole_id in known_ids:
                continue
            matches = catalog[catalog[name_column].astype(str).str.strip().str.upper() == hole_id.upper()]
            if matches.empty:
                self.stdout.write(self.style.WARNING(f"{hole_id}: not in {COLLARS_PATH.name}, skipping"))
                continue
            row = matches.iloc[0]

            Hole.objects.create(
                hole_id=hole_id,
                hole_name=hole_id,
                latitude=row["Latitude (degrees)"],
                longitude=row["Longitude (degrees)"],
                elevation_m=_clean(row.get("Elevation")),
                borehole_length_m=_clean(row.get("Total (m)")),
                inclination_deg=_clean(row.get("Inclination (dip)"), -90.0),
                azimuth_deg=_clean(row.get("Azimuth"), 0.0),
                confidential=False,
            )
            added += 1
            self.stdout.write(f"Added {hole_id}")

        self.stdout.write(self.style.SUCCESS(f"Done. {added} holes added, {len(ALLOWED)} checked."))


def _clean(value, default=None):
    """NaN (missing in the spreadsheet) -> default, not NaN - a Postgres
    float column would otherwise reject it."""
    if value is None or pd.isna(value):
        return default
    return float(value)

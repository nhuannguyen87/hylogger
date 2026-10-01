"""
Patch in the rest of the WA drill-hole catalog (~2,263 holes total) so they
show up on the map/list, without running the full per-hole ETL pipeline for
all of them - that's a real download per hole (see reload-data.sh) and would
take days for this many. This only adds position/name/depth/orientation,
already prepared by database5553/wa-drillhole-map/prepare_data.py from the
GSWA HyLogger and NVCL catalog spreadsheets.

    python manage.py load_catalog_holes

Never touches a hole already in the table - the ~200 already loaded through
the real ETL pipeline (data/holes.csv) keep their full measurement data.
Catalog-only holes just show "no depth log yet" until a real pipeline run
covers them - same pattern as load_etl4_holes.py for the 5 fully-detailed
holes. Deeper data isn't downloaded here at all: opening one of these holes
fetches its real spectrum live from NVCL on demand (see nvcl_bridge.py),
not upfront - a patch, not a bulk download.
"""

import json

from django.conf import settings
from django.core.management.base import BaseCommand

from holes.models import Hole


class Command(BaseCommand):
    help = "Add any catalog holes (database5553/wa-drillhole-map/public/holes.geojson) missing from the main table."

    def add_arguments(self, parser):
        parser.add_argument("--source", default=str(settings.CATALOG_GEOJSON_PATH))

    def handle(self, *args, **options):
        path = options["source"]
        try:
            with open(path, encoding="utf-8") as handle:
                geojson = json.load(handle)
        except FileNotFoundError:
            raise SystemExit(
                f"Can't find {path}. This is optional - the site works fine without "
                "it, just with the smaller ETL-only hole set."
            )

        known_ids = set(Hole.objects.values_list("hole_id", flat=True))
        seen_this_run = set()
        holes = []

        for feature in geojson["features"]:
            props = feature["properties"]
            if feature["geometry"]["type"] != "Point" or props.get("feature_role") != "collar":
                continue
            hole_id = (props.get("hole") or "").strip()
            if not hole_id or hole_id in known_ids or hole_id in seen_this_run:
                continue
            seen_this_run.add(hole_id)

            longitude, latitude = feature["geometry"]["coordinates"]
            holes.append(Hole(
                hole_id=hole_id,
                hole_name=hole_id,
                latitude=latitude,
                longitude=longitude,
                elevation_m=props.get("elevation_m"),
                borehole_length_m=props.get("total_depth_m"),
                inclination_deg=props.get("dip") if props.get("dip") is not None else -90.0,
                azimuth_deg=props.get("azimuth") if props.get("azimuth") is not None else 0.0,
                confidential=props.get("nvcl_status") == "CONFIDENTIAL",
            ))

        Hole.objects.bulk_create(holes, batch_size=1000)
        self.stdout.write(self.style.SUCCESS(
            f"Added {len(holes)} catalog holes. {Hole.objects.count()} holes total."
        ))

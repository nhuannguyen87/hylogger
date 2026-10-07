"""
Load real core-tray photos + per-tray mineral calls for whichever holes have
them, from the earlier hand-processed exploration in ../data5553/.

    python manage.py load_core_trays

Only holes whose <data5553>/<hole_id>/<hole_id>_mineral_by_tray.csv exists
get loaded - most holes have no CoreTray rows at all, and that's expected
(hole_trays() returns an empty list for them, not an error). Copies the
referenced tray JPGs into MEDIA_ROOT so Django can serve them.
"""

import csv
import shutil
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from holes.models import CoreTray, Hole

HOLES_WITH_TRAY_DATA = ["KD1", "GDD005"]


def to_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class Command(BaseCommand):
    help = "Load real core-tray photos + per-tray mineral data from data5553/."

    def add_arguments(self, parser):
        parser.add_argument(
            "--source",
            default=str(Path(settings.PROJECT_ROOT).parent / "data5553"),
            help="Folder containing <hole_id>/<hole_id>_mineral_by_tray.csv + photos",
        )

    def handle(self, *args, **options):
        source_root = Path(options["source"])
        total_trays = 0

        for hole_id in HOLES_WITH_TRAY_DATA:
            hole_dir = source_root / hole_id
            csv_path = hole_dir / f"{hole_id}_mineral_by_tray.csv"
            if not csv_path.is_file():
                self.stdout.write(self.style.WARNING(f"{hole_id}: no {csv_path.name}, skipping"))
                continue
            if not Hole.objects.filter(pk=hole_id).exists():
                self.stdout.write(self.style.WARNING(f"{hole_id}: not in holes table yet, skipping"))
                continue

            image_dir = Path(settings.MEDIA_ROOT) / "tray_images" / hole_id
            image_dir.mkdir(parents=True, exist_ok=True)

            CoreTray.objects.filter(hole_id=hole_id).delete()
            trays = []
            with csv_path.open(newline="", encoding="utf-8-sig") as handle:
                for row in csv.DictReader(handle):
                    photo = row.get("Photo", "").strip()
                    image_rel = ""
                    if photo:
                        source_photo = hole_dir / photo
                        if source_photo.is_file():
                            shutil.copy2(source_photo, image_dir / photo)
                            image_rel = f"tray_images/{hole_id}/{photo}"
                        else:
                            self.stdout.write(self.style.WARNING(f"{hole_id}: missing photo {photo}"))

                    trays.append(CoreTray(
                        hole_id=hole_id,
                        tray_no=int(float(row["Tray"])),
                        depth_from_m=to_float(row["Depth_from_m"]) or 0.0,
                        depth_to_m=to_float(row["Depth_to_m"]) or 0.0,
                        image=image_rel,
                        swir_vnir_mineral=row.get("SWIR_VNIR_dominant_mineral", "").strip(),
                        swir_vnir_group=row.get("SWIR_VNIR_group", "").strip(),
                        swir_vnir_wt_pct=to_float(row.get("SWIR_VNIR_avg_wt_pct")),
                        swir_vnir_pct_of_tray=to_float(row.get("SWIR_VNIR_pct_of_tray")),
                        tir_mineral=row.get("TIR_dominant_mineral", "").strip(),
                        tir_group=row.get("TIR_group", "").strip(),
                        tir_wt_pct=to_float(row.get("TIR_avg_wt_pct")),
                        tir_pct_of_tray=to_float(row.get("TIR_pct_of_tray")),
                    ))
            CoreTray.objects.bulk_create(trays)
            total_trays += len(trays)
            self.stdout.write(self.style.SUCCESS(f"{hole_id}: {len(trays)} trays loaded"))

        self.stdout.write(self.style.SUCCESS(f"Done. {total_trays} core trays total."))

"""
Add the 5 ETL4 holes (database5553/) to the main catalog if they're missing
from it - only 05KCD001 came in through the regular GSWA/NVCL download, so
without this, 4 of the 5 holes with a full restored spectrum have no row to
show the "full spectral data" badge on at all.

    python manage.py load_etl4_holes

Never touches a hole that's already in the table (05KCD001, from the real
pipeline, keeps its real inclination/azimuth) - this only fills in the ones
missing entirely, with what ETL4's own borehole record has: collar position
and length, but not orientation, which is withheld for these 5 for
confidentiality, same as any other confidential hole in this table.
"""

from django.conf import settings
from django.core.management.base import BaseCommand

from holes.etl4_bridge import ALLOWED, get_connection
from holes.models import Hole


class Command(BaseCommand):
    help = "Add any of the 5 ETL4 holes missing from the main catalog (metadata only, no orientation)."

    def handle(self, *args, **options):
        try:
            with get_connection() as conn:
                rows = conn.execute(
                    """
                    SELECT DISTINCT b.source_hole_id, br.reported_length_m,
                           ST_X(br.collar_geom) AS longitude, ST_Y(br.collar_geom) AS latitude
                    FROM core.release_dataset rd
                    JOIN core.dataset_revision d ON d.id = rd.dataset_revision_id
                    JOIN core.borehole b ON b.id = d.borehole_id
                    JOIN core.borehole_revision br ON br.id = d.borehole_revision_id
                    WHERE rd.release_id = %s AND b.source_hole_id = ANY(%s)
                    """,
                    (settings.ETL4_RELEASE_ID, list(ALLOWED)),
                ).fetchall()
        except Exception as exc:  # connection refused, etl4_core not restored, etc.
            raise SystemExit(f"Can't reach etl4_core: {exc}")

        added = 0
        for row in rows:
            _, created = Hole.objects.get_or_create(
                hole_id=row["source_hole_id"],
                defaults=dict(
                    hole_name=row["source_hole_id"],
                    latitude=row["latitude"],
                    longitude=row["longitude"],
                    borehole_length_m=row["reported_length_m"],
                    confidential=True,  # orientation withheld_for_confidentiality for all 5
                ),
            )
            if created:
                added += 1
                self.stdout.write(f"Added {row['source_hole_id']}")

        self.stdout.write(self.style.SUCCESS(f"Done. {added} holes added, {len(rows)} checked."))

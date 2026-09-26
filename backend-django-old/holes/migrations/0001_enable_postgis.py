"""
Turns on PostGIS and adds a spatial index on the hole collars.

We store latitude/longitude as ordinary numbers and call PostGIS functions
(ST_DistanceSphere, ST_DWithin) directly in SQL. That gives us real spatial
queries without needing GDAL installed on your Mac, which is the usual reason
GeoDjango setups fall over on macOS.
"""
from django.db import migrations


class Migration(migrations.Migration):
    initial = True
    dependencies = []

    operations = [
        migrations.RunSQL(
            sql="CREATE EXTENSION IF NOT EXISTS postgis;",
            reverse_sql="DROP EXTENSION IF EXISTS postgis;",
        ),
    ]

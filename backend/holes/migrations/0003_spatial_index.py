"""
A spatial index so 'holes within N km' stays fast as the dataset grows.

The expression here must match the one in holes/views.py exactly, otherwise
Postgres won't use the index.
"""
from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("holes", "0002_initial")]

    operations = [
        migrations.RunSQL(
            sql="""
                CREATE INDEX IF NOT EXISTS holes_collar_gix
                ON holes
                USING GIST ((ST_SetSRID(ST_MakePoint(longitude, latitude), 4326)::geography));
            """,
            reverse_sql="DROP INDEX IF EXISTS holes_collar_gix;",
        ),
    ]

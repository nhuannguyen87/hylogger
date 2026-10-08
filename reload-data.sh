#!/usr/bin/env bash
# Run this after your etl.py produces new CSVs in data/.
# Reloads the database and retrains the anomaly model.
# No --replace: holes are upserted, so holes added by load_catalog_holes /
# load_tsg_holes / load_etl4_holes (and their core trays) survive a reload.
set -e
cd "$(dirname "$0")"
source .venv/bin/activate
cd backend-django-old
python manage.py load_data
python manage.py detect_anomalies

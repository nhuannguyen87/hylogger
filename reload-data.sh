#!/usr/bin/env bash
# Run this after your etl.py produces new CSVs in data/.
# Reloads the database and retrains the anomaly model.
set -e
cd "$(dirname "$0")"
source .venv/bin/activate
cd backend
python manage.py load_data --replace
python manage.py detect_anomalies

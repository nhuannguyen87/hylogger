#!/usr/bin/env bash
# Starts the Django API on http://localhost:8000
set -e
cd "$(dirname "$0")"
source .venv/bin/activate
docker compose up -d          # make sure the database is awake
cd backend
python manage.py runserver 8000

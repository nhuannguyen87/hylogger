#!/usr/bin/env bash
# LEGACY: starts the old Django prototype API (backend-django-old/) on
# http://localhost:8000. The website no longer uses it - it runs on the FastAPI
# backend (./run-backend.sh, same port). README.md, "Legacy", has its setup.
set -e
cd "$(dirname "$0")"
source .venv/bin/activate
docker compose up -d          # make sure the database is awake
cd backend-django-old
python manage.py runserver 8000

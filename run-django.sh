#!/usr/bin/env bash
# Starts the Django prototype API (backend-django-old/) on http://localhost:8000.
# The website's map colours, core photos and 3D view use it until the FastAPI
# backend (./run-backend.sh, same port) covers those endpoints.
set -e
cd "$(dirname "$0")"
source .venv/bin/activate
docker compose up -d          # make sure the database is awake
cd backend-django-old
python manage.py runserver 8000

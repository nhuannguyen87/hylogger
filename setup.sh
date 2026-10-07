#!/usr/bin/env bash
# Sets everything up from scratch. Safe to run again if something goes wrong.
#
#   ./setup.sh
#
# What it does:
#   1. starts Postgres+PostGIS in Docker
#   2. makes a Python virtual environment and installs the backend
#   3. creates the database tables
#   4. generates sample data and loads it
#   5. trains the anomaly model
#   6. installs the frontend packages

set -e  # stop at the first error

cd "$(dirname "$0")"
ROOT="$(pwd)"

say() { printf "\n\033[1;36m==> %s\033[0m\n" "$1"; }
fail() { printf "\n\033[1;31mx %s\033[0m\n" "$1"; exit 1; }

# --- check the tools we need ------------------------------------------------
command -v docker >/dev/null || fail "Docker isn't installed. Get Docker Desktop from docker.com, start it, then run this again."
docker info >/dev/null 2>&1 || fail "Docker is installed but not running. Open Docker Desktop and wait for the whale icon to settle."
command -v python3 >/dev/null || fail "python3 not found. Install it with: brew install python@3.12"
command -v node >/dev/null || fail "Node.js not found. Install it with: brew install node"

[ -f .env ] || cp .env.example .env

# --- 1. database ------------------------------------------------------------
say "Starting Postgres + PostGIS"
docker compose up -d

printf "waiting for the database"
for _ in $(seq 1 40); do
  if docker compose exec -T db pg_isready -U hylogger -d hylogger >/dev/null 2>&1; then
    printf " ready\n"; break
  fi
  printf "."; sleep 1
done

# --- 2. backend -------------------------------------------------------------
say "Setting up the Python environment"
if [ ! -d .venv ]; then
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install --quiet --upgrade pip
pip install --quiet -r backend/requirements.txt

# --- 3. tables --------------------------------------------------------------
say "Creating database tables"
(cd backend && python manage.py migrate)

# --- 4. data ----------------------------------------------------------------
if [ ! -f data/holes.csv ]; then
  say "Generating sample data (swap in your real CSVs later)"
  python data/make_sample_data.py
fi

say "Loading data into Postgres"
(cd backend && python manage.py load_data --replace)

# --- 5. model ---------------------------------------------------------------
say "Training the anomaly model"
(cd backend && python manage.py detect_anomalies)

# --- 6. frontend ------------------------------------------------------------
say "Installing frontend packages (this one takes a couple of minutes)"
(cd frontend && npm install --no-audit --no-fund)

cat <<DONE

  Setup finished.

  Now open two terminal tabs in $ROOT

    tab 1:   ./run-backend.sh      ->  http://localhost:8000/api/holes/
    tab 2:   ./run-frontend.sh     ->  http://localhost:3000

  Read README.md next. CUSTOMISE.md covers the changes you're most likely to make.

DONE

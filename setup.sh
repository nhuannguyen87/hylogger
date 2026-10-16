#!/usr/bin/env bash
# One-time setup for the site. Safe to run again if something goes wrong.
#
#   ./setup.sh
#
# What it does:
#   1. makes backend/.venv and installs the FastAPI backend (backend/requirements.txt)
#   2. creates backend/.env from backend/.env.example, for you to fill in
#   3. installs the frontend packages
#
# The old Django prototype (backend-django-old/) is no longer part of this -
# README.md, "Legacy", has its steps.

set -e  # stop at the first error

cd "$(dirname "$0")"
ROOT="$(pwd)"

say() { printf "\n\033[1;36m==> %s\033[0m\n" "$1"; }
fail() { printf "\n\033[1;31mx %s\033[0m\n" "$1"; exit 1; }

# --- check the tools we need ------------------------------------------------
# backend/requirements.txt pins numpy 2.5, which needs Python 3.12 or newer.
PYTHON=""
for candidate in python3.13 python3.12 python3; do
  if command -v "$candidate" >/dev/null &&
     "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 12))' 2>/dev/null; then
    PYTHON="$candidate"; break
  fi
done
[ -n "$PYTHON" ] || fail "Python 3.12+ not found. Install it with: brew install python@3.12"
command -v node >/dev/null || fail "Node.js not found. Install it with: brew install node"

# --- 1. backend -------------------------------------------------------------
say "Setting up the FastAPI backend (backend/.venv, $("$PYTHON" --version))"
[ -d backend/.venv ] || "$PYTHON" -m venv backend/.venv
backend/.venv/bin/python -m pip install --quiet --upgrade pip
backend/.venv/bin/python -m pip install --quiet -r backend/requirements.txt

# --- 2. backend settings ----------------------------------------------------
if [ ! -f backend/.env ]; then
  cp backend/.env.example backend/.env
  say "Created backend/.env - fill in the ETL4 database and asset settings before ./run-backend.sh"
fi

# --- 3. frontend ------------------------------------------------------------
say "Installing frontend packages (this one takes a couple of minutes)"
(cd frontend && npm install --no-audit --no-fund)

cat <<DONE

  Setup finished.

  Now open two terminal tabs in $ROOT

    tab 1:   ./run-backend.sh      ->  http://localhost:8000/docs
    tab 2:   ./run-frontend.sh     ->  http://localhost:3000

  Or skip tab 1 and point the site at an API that is already running:
  NEXT_PUBLIC_API_BASE=https://... in frontend/.env.local

  Read README.md next. CUSTOMISE.md covers the changes you're most likely to make.

DONE

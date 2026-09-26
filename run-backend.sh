#!/usr/bin/env bash
# Starts the FastAPI backend on http://localhost:8000
set -e

cd "$(dirname "$0")/backend"

if [ -x ".venv/bin/python" ]; then
  PYTHON=".venv/bin/python"
elif [ -x ".venv/Scripts/python.exe" ]; then
  PYTHON=".venv/Scripts/python.exe"
else
  PYTHON="python"
fi

"$PYTHON" -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000

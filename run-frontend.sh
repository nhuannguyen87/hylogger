#!/usr/bin/env bash
# Starts the Next.js site on http://localhost:3000
set -e
cd "$(dirname "$0")/frontend"
npm run dev

#!/usr/bin/env bash
# First-time data download + model training. Run once (takes 1-3 hours).
set -e
cd "$(dirname "$0")"
source .venv/bin/activate
python -m src.scripts.bootstrap

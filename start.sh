#!/usr/bin/env bash
# Daily launcher for Mac / Linux. Run each trading morning.
cd "$(dirname "$0")"
source .venv/bin/activate
python -m src.run

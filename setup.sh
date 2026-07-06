#!/usr/bin/env bash
# One-time setup for Mac / Linux. Run once after downloading the project.
set -e
cd "$(dirname "$0")"

echo "==> Creating Python virtual environment (.venv)"
python3 -m venv .venv
source .venv/bin/activate

echo "==> Installing Python packages (this takes a few minutes)"
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt

if [ ! -f config.yaml ]; then
  cp config.example.yaml config.yaml
  echo "==> Created config.yaml — OPEN IT and paste your Fyers app_id + secret_key."
fi

echo ""
echo "Setup done. Next:"
echo "  1. Put your Fyers keys in config.yaml"
echo "  2. Run:  ./bootstrap.sh    (first-time data download + model training)"
echo "  3. Every morning after that:  ./start.sh"

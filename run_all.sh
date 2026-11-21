#!/usr/bin/env bash
# Rebuild everything from the seed (about 40 seconds).
set -euo pipefail
cd "$(dirname "$0")"
python src/simulate.py data
python src/build_warehouse.py
python src/analysis.py
python src/dashboard.py
python -m pytest -q tests

#!/usr/bin/env bash
set -euo pipefail
python -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/python -m unittest discover -s tests -v

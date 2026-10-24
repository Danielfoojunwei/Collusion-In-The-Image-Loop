#!/usr/bin/env python3
"""Export a validated run using a consistent SQLite backup."""
import argparse
from pathlib import Path
from visual_collusion.cli import export_run

parser = argparse.ArgumentParser()
parser.add_argument("run", type=Path)
parser.add_argument("destination", type=Path)
args = parser.parse_args()
print(export_run(args.run, args.destination))

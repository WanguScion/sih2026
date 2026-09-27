#!/usr/bin/env python3
"""Stand-in for the real main.py, used only to smoke-test the Express app's
process-spawning and output-reading logic without needing torch/SegFormer."""
import argparse
import json
import os
import sys
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--input-path")
parser.add_argument("--output-path")
args = parser.parse_args()

input_path = args.input_path or os.environ.get("INPUT_PATH")
output_path = args.output_path or os.environ.get("OUTPUT_PATH")

print(f"fake_pipeline: input_path={input_path}", file=sys.stdout)
print(f"fake_pipeline: output_path={output_path}", file=sys.stdout)

survey = Path(input_path) / "survey.tif"
dsm = Path(input_path) / "dsm.tif"
if not survey.exists() or not dsm.exists():
    print(f"fake_pipeline: missing survey.tif or dsm.tif in {input_path}", file=sys.stderr)
    sys.exit(1)

out_dir = Path(output_path)
out_dir.mkdir(parents=True, exist_ok=True)
out_file = out_dir / "roofs_3d.geojson" if out_dir.is_dir() or not str(output_path).endswith(".geojson") else out_dir

geojson = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "properties": {"plane_id": 0, "building_id": 0, "slope_deg": 32.1, "aspect_deg": 180, "area_xy": 45.2},
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[500010, 4499990, 3.0], [500020, 4499990, 3.0],
                                  [500020, 4500000, 6.0], [500010, 4500000, 6.0],
                                  [500010, 4499990, 3.0]]],
            },
        },
        {
            "type": "Feature",
            "properties": {"plane_id": 1, "building_id": 0, "slope_deg": 32.4, "aspect_deg": 0, "area_xy": 45.0},
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[500010, 4500000, 6.0], [500020, 4500000, 6.0],
                                  [500020, 4500010, 3.0], [500010, 4500010, 3.0],
                                  [500010, 4500000, 6.0]]],
            },
        },
    ],
}

target = Path(output_path)
if str(target).endswith(".geojson"):
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "w") as f:
        json.dump(geojson, f)
else:
    target.mkdir(parents=True, exist_ok=True)
    with open(target / "roofs_3d.geojson", "w") as f:
        json.dump(geojson, f)

print("fake_pipeline: done")
sys.exit(0)

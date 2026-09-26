#!/usr/bin/env python3
"""
clip_gsm.py
-----------
Standalone CLI: clip a raster (e.g. GSM signal-strength data, or any other
GeoTIFF) to the boundary of a vector polygon layer — typically the GeoJSON
produced by main.py's segmentation pipeline, but works with any properly
georeferenced vector file.

Example:
    python clip_gsm.py \\
        --raster gsm_signal_strength.tif \\
        --polygon landcover.geojson \\
        --output gsm_clipped.tif

Only clip to specific classes from the segmentation output:
    python clip_gsm.py \\
        --raster gsm_signal_strength.tif \\
        --polygon landcover.geojson \\
        --class-filter class_a,class_b \\
        --output gsm_clipped.tif
"""

from __future__ import annotations

import argparse
import logging
import sys

import geopandas as gpd

from geoseg import clip_raster_by_vector

log = logging.getLogger("geoseg.clip_gsm")


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Clip a raster (e.g. GSM data) to a vector polygon boundary.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--raster", "-r", required=True,
                    help="Path to the raster to clip (e.g. GSM/signal-strength GeoTIFF)")
    p.add_argument("--polygon", "-p", required=True,
                    help="Path to the clip vector (.geojson/.gpkg/.shp), e.g. main.py's output")
    p.add_argument("--output", "-o", required=True, help="Path to write the clipped raster")
    p.add_argument("--class-filter", default=None,
                    help="Comma-separated 'class' values to keep from the polygon layer "
                         "before clipping (default: use all polygons in the file)")
    p.add_argument("--all-touched", action="store_true",
                    help="Include pixels merely touched by the polygon boundary, "
                         "not just pixels whose center falls inside it")
    p.add_argument("--no-crop", action="store_true",
                    help="Keep the raster's full original extent instead of cropping "
                         "to the polygon's bounding box (outside pixels become nodata)")
    p.add_argument("--invert", action="store_true",
                    help="Clip to everything OUTSIDE the polygon instead of inside it")
    p.add_argument("--nodata", type=float, default=None,
                    help="Value for pixels outside the clip area (default: source nodata or 0)")
    p.add_argument("-v", "--verbose", action="store_true")
    return p


def main() -> None:
    args = build_arg_parser().parse_args()
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    gdf = gpd.read_file(args.polygon)

    if args.class_filter:
        wanted = {c.strip() for c in args.class_filter.split(",")}
        if "class" not in gdf.columns:
            log.error("--class-filter given but the polygon file has no 'class' column")
            sys.exit(1)
        gdf = gdf[gdf["class"].isin(wanted)]
        if gdf.empty:
            log.error("No polygons matched --class-filter %s", wanted)
            sys.exit(1)
        log.info("Filtered to %d polygon(s) in class(es) %s", len(gdf), wanted)

    try:
        out_path = clip_raster_by_vector(
            raster_path=args.raster,
            vector=gdf,
            out_path=args.output,
            all_touched=args.all_touched,
            crop=not args.no_crop,
            nodata=args.nodata,
            invert=args.invert,
        )
    except Exception as exc:
        logging.error("Failed: %s", exc)
        sys.exit(1)

    print(f"Done. Clipped raster written to: {out_path}")


if __name__ == "__main__":
    main()

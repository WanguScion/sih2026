#!/usr/bin/env python3
"""
GeoTIFF -> SegFormer -> 3D GIS vector data (buildings + reconstructed roofs)
=============================================================================

The full pipeline, in one script:

  1. Read a georeferenced orthophoto GeoTIFF (validated as properly
     georeferenced — real CRS + affine transform, as expected from a
     GNSS/CORS-corrected survey product).
  2. Tile it (streamed from disk, memory-bounded) and run SegFormer
     semantic segmentation per tile, keeping only building/structure
     classes by default.
  3. Stitch tile predictions into one full-resolution class-label raster
     and vectorize it into building footprint polygons in real-world
     coordinates.
  4. For each building footprint: clip a DSM to it, turn the clipped DSM
     into an (x, y, z) point cloud, fit planar roof facets with iterative
     RANSAC, and build a 3D mesh (triangulated roof + extruded walls).
  5. Combine every building's mesh and fitted roof facets into the final
     outputs: one 3D mesh file and one 3D (x, y, z) GIS vector file.

By default this just runs against `survey.tif` (the orthophoto) and
`dsm.tif` (the elevation raster) in the current directory — no flags
required:

    python main.py

Override any of the hardcoded defaults as needed:

    python main.py --input my_ortho.tif --dsm my_dsm.tif --output-dir results/
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import tempfile
from pathlib import Path

import geopandas as gpd
import pandas as pd
import trimesh

from geoseg import (
    read_geotiff,
    iter_tiles,
    LabelMosaic,
    reproject_geotiff,
    SegFormerSegmenter,
    raster_to_vector,
    save_vector,
    clip_raster_by_vector,
    raster_to_point_cloud,
    fit_roof_planes,
    build_roof_mesh,
    planes_to_3d_gdf,
    save_3d_vector,
)

log = logging.getLogger("geoseg.main")

# Hardcoded defaults: with no flags at all, `python main.py` looks for
# these two files in the current directory and writes results into ./output/.
DEFAULT_INPUT = "survey.tif"
DEFAULT_DSM = "dsm.tif"
DEFAULT_OUTPUT_DIR = "output"

default_model = str(Path(__file__).resolve().parent / "models" / "segformer_b0_flair_one")
def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="GeoTIFF + DSM -> SegFormer building masks -> RANSAC roof planes "
                    "-> 3D mesh -> 3D GIS vector data, end to end.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--input-path", required=True,
                    help="Directory containing survey.tif and dsm.tif")
    p.add_argument("--output-path", required=True,
                    help="Directory to write all outputs into")

    seg_group = p.add_argument_group("segmentation")
    seg_group.add_argument("--model", default=default_model,
                            help="HF Hub model id or local path to a SegFormer checkpoint")
    seg_group.add_argument("--device", default=None, help="'cuda' or 'cpu' (auto-detected if omitted)")
    seg_group.add_argument("--tile-size", type=int, default=512, help="Tile size in pixels")
    seg_group.add_argument("--overlap", type=int, default=32, help="Tile overlap in pixels")
    seg_group.add_argument("--target-crs", default=None,
                            help="Reproject input to this CRS first, e.g. EPSG:3857 "
                                 "(default: keep source CRS untouched)")
    seg_group.add_argument("--ignore-labels", default="0",
                            help="Comma-separated class ids to exclude from output when "
                                 "--all-classes is set. Ignored in the default building-only mode.")
    seg_group.add_argument("--all-classes", action="store_true",
                            help="Disable building-only filtering and keep every class the "
                                 "model predicts (use --ignore-labels to drop specific ones)")
    seg_group.add_argument("--building-keywords", default=None,
                            help="Comma-separated substrings (case-insensitive) matched against "
                                 "class names to find building classes, e.g. 'building,house'. "
                                 "Defaults to a built-in list covering common ADE20K + "
                                 "remote-sensing label sets.")
    seg_group.add_argument("--class-names", default=None,
                            help="Optional path to a JSON file mapping {\"id\": \"name\"} to "
                                 "override the model's built-in label map")
    seg_group.add_argument("--simplify", type=float, default=None,
                            help="Douglas-Peucker simplification tolerance in CRS units")
    seg_group.add_argument("--min-area", type=float, default=None,
                            help="Drop building polygons smaller than this area in CRS units^2")

    clip_group = p.add_argument_group("DSM clipping (per building)")
    clip_group.add_argument("--clip-all-touched", action="store_true",
                             help="Include DSM pixels merely touched by a footprint's boundary")

    ransac_group = p.add_argument_group("RANSAC roof-plane fitting")
    ransac_group.add_argument("--min-points-per-plane", type=int, default=25,
                               help="Minimum DSM points required to accept a roof facet")
    ransac_group.add_argument("--residual-threshold", type=float, default=0.25,
                               help="Max vertical distance (z units, e.g. meters) from a "
                                    "fitted plane for a point to count as an inlier")
    ransac_group.add_argument("--max-planes", type=int, default=8,
                               help="Max roof facets to extract per building")
    ransac_group.add_argument("--max-points", type=int, default=20000,
                               help="Subsample a building's point cloud down to this many "
                                    "points if it has more (keeps RANSAC fast)")

    mesh_group = p.add_argument_group("mesh building")
    mesh_group.add_argument("--base-elevation", type=float, default=None,
                             help="Elevation to extrude walls down to. Default: each "
                                  "building's own minimum elevation *within its clipped "
                                  "footprint* — since the DSM clip excludes surrounding "
                                  "ground, this is usually close to roof level, not true "
                                  "ground level. Pass an explicit value (e.g. from a DTM, "
                                  "or a known site datum) for walls that reach the ground.")
    mesh_group.add_argument("--max-edge-factor", type=float, default=3.0,
                             help="Drop Delaunay triangles whose longest edge exceeds this "
                                  "factor times the median edge length (removes boundary artifacts)")

    p.add_argument("-v", "--verbose", action="store_true")
    return p


def _segment_buildings(args: argparse.Namespace) -> gpd.GeoDataFrame:
    """Stages 1-3: orthophoto -> SegFormer -> stitched label raster ->
    building-only footprint polygons."""
    input_path = args.input
    tmp_reprojected = None

    if not Path(input_path).exists():
        raise ValueError(
            f"Input orthophoto '{input_path}' not found. Pass --input, or place a "
            f"file named '{DEFAULT_INPUT}' in the current directory."
        )

    meta = read_geotiff(input_path)
    log.info("Input raster: %dx%d, %d band(s), CRS=%s", meta.width, meta.height,
              meta.count, meta.crs)

    if args.target_crs and str(meta.crs) != args.target_crs:
        tmp_reprojected = tempfile.NamedTemporaryFile(suffix=".tif", delete=False)
        log.info("Reprojecting input to %s ...", args.target_crs)
        input_path = reproject_geotiff(input_path, args.target_crs, tmp_reprojected.name)
        meta = read_geotiff(input_path)

    segmenter = SegFormerSegmenter(model_name_or_path=args.model, device=args.device)
    log.info("Model classes: %s", segmenter.class_names)

    mosaic = LabelMosaic(height=meta.height, width=meta.width, fill_value=-1)
    n_tiles = 0
    for tile in iter_tiles(input_path, tile_size=args.tile_size, overlap=args.overlap):
        pred = segmenter.predict_tile(tile.array)
        mosaic.add(pred, tile.row_off, tile.col_off)
        n_tiles += 1
    log.info("Processed %d tiles", n_tiles)

    class_names = dict(segmenter.id2label)
    if args.class_names:
        with open(args.class_names) as f:
            override = json.load(f)
        class_names.update({int(k): v for k, v in override.items()})

    if args.all_classes:
        ignore_labels = {int(x) for x in args.ignore_labels.split(",") if x.strip() != ""}
    else:
        keywords = tuple(k.strip() for k in args.building_keywords.split(",")) if args.building_keywords else None
        keep_ids = segmenter.building_class_ids(keywords)
        if not keep_ids:
            raise ValueError(
                f"No classes in model '{args.model}' matched the building keywords "
                f"({keywords or 'defaults'}). Pass --building-keywords with substrings "
                f"matching this model's own label names (see the class list logged above "
                f"with -v), or pass --all-classes to disable building-only filtering."
            )
        log.info("Building-only mode: keeping class ids %s (%s)",
                  sorted(keep_ids), [class_names[i] for i in sorted(keep_ids)])
        ignore_labels = {cid for cid in class_names if cid not in keep_ids}
    ignore_labels.add(-1)  # never-written pixels (shouldn't occur, but be safe)

    gdf = raster_to_vector(
        mosaic.labels,
        transform=meta.transform,
        crs=meta.crs,
        class_names=class_names,
        ignore_labels=ignore_labels,
        simplify_tolerance=args.simplify,
        min_area=args.min_area,
    )
    log.info("Vectorized into %d building footprint(s)", len(gdf))

    if tmp_reprojected:
        Path(tmp_reprojected.name).unlink(missing_ok=True)

    return gdf


def _reconstruct_building(dsm_path: str, geometry, building_id: int, crs, args, tmp_dir: Path):
    """Stage 4, one building: clip DSM -> point cloud -> RANSAC planes -> mesh + 3D facets."""
    single_gdf = gpd.GeoDataFrame({"class": ["building"]}, geometry=[geometry], crs=crs)
    clipped_path = tmp_dir / f"dsm_clip_{building_id}.tif"
    clip_raster_by_vector(
        raster_path=dsm_path,
        vector=single_gdf,
        out_path=str(clipped_path),
        all_touched=args.clip_all_touched,
    )

    points, point_crs = raster_to_point_cloud(str(clipped_path), max_points=args.max_points)

    planes = fit_roof_planes(
        points,
        min_points_per_plane=args.min_points_per_plane,
        residual_threshold=args.residual_threshold,
        max_planes=args.max_planes,
    )

    base_elevation = args.base_elevation if args.base_elevation is not None else float(points[:, 2].min())
    mesh = build_roof_mesh(
        planes, footprint_polygon=geometry, base_elevation=base_elevation,
        max_edge_factor=args.max_edge_factor,
    )

    gdf3d = planes_to_3d_gdf(planes, crs=point_crs)
    gdf3d["building_id"] = building_id
    return mesh, gdf3d


def run(args: argparse.Namespace) -> dict:
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Stages 1-3: orthophoto -> building footprint polygons
    gdf = _segment_buildings(args)
    buildings_path = out_dir / "buildings.geojson"
    save_vector(gdf, str(buildings_path))
    log.info("Wrote building footprints: %s", buildings_path)

    footprints = gdf[gdf.geometry.notnull() & ~gdf.geometry.is_empty].reset_index(drop=True)
    if footprints.empty:
        raise ValueError(
            "No building polygons were detected in the orthophoto — nothing to "
            "reconstruct in 3D. See buildings.geojson (empty) for the segmentation result."
        )

    # Stage 4: DSM required from here on
    if not Path(args.dsm).exists():
        raise ValueError(
            f"DSM file '{args.dsm}' not found. Building footprints were still written to "
            f"{buildings_path}. Pass --dsm, or place a file named '{DEFAULT_DSM}' in the "
            f"current directory, to also generate the 3D mesh and 3D vector output."
        )

    log.info("Reconstructing 3D roof geometry for %d building(s)...", len(footprints))
    meshes, gdf_parts = [], []
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        for building_id, row in footprints.iterrows():
            try:
                mesh, gdf3d = _reconstruct_building(
                    args.dsm, row.geometry, building_id, footprints.crs, args, tmp_dir
                )
            except ValueError as exc:
                log.warning("Skipping building %d: %s", building_id, exc)
                continue
            meshes.append(mesh)
            gdf_parts.append(gdf3d)
            log.info("Building %d: %d roof facet(s) reconstructed", building_id, len(gdf3d))

    if not meshes:
        raise ValueError(
            "No buildings could be reconstructed in 3D — see warnings above (common causes: "
            "the DSM doesn't overlap the detected footprints, or --residual-threshold / "
            "--min-points-per-plane are too strict for this DSM's resolution/noise). "
            f"Building footprints are still available at {buildings_path}."
        )

    # Stage 5: final outputs — one combined mesh, one combined 3D vector file
    combined_mesh = trimesh.util.concatenate(meshes)
    mesh_path = out_dir / "roofs.obj"
    combined_mesh.export(str(mesh_path))
    log.info("Wrote 3D mesh: %s (%d vertices, %d faces, %d building(s))",
              mesh_path, len(combined_mesh.vertices), len(combined_mesh.faces), len(meshes))

    combined_gdf = gpd.GeoDataFrame(pd.concat(gdf_parts, ignore_index=True), crs=footprints.crs)
    vector3d_path = out_dir / "roofs_3d.geojson"
    save_3d_vector(combined_gdf, str(vector3d_path))

    return {
        "buildings": str(buildings_path),
        "mesh": str(mesh_path),
        "vector_3d": str(vector3d_path),
    }


def main() -> None:
    args = build_arg_parser().parse_args()
    args.input = str(Path(args.input_path) / DEFAULT_INPUT)
    args.dsm = str(Path(args.input_path) / DEFAULT_DSM)
    args.output_dir = args.output_path
    try:
        outputs = run(args)
    except Exception as exc:  # surface a clean error instead of a traceback wall
        logging.error("Failed: %s", exc)
        sys.exit(1)
    print("Done.")
    print(f"  Building footprints (2D): {outputs['buildings']}")
    print(f"  3D mesh:                  {outputs['mesh']}")
    print(f"  3D GIS vector data:       {outputs['vector_3d']}")


if __name__ == "__main__":
    main()

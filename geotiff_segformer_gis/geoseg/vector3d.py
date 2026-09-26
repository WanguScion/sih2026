"""
vector3d.py
-----------
Turns fitted RoofPlane facets into proper 3D GIS vector data: a
GeoDataFrame of Polygon Z geometries (one per roof facet), each vertex
carrying a real elevation from the fitted plane, saved to GeoJSON or
GeoPackage (both support Z-enabled geometries; Shapefile does not
reliably, so it's not offered here).
"""

from __future__ import annotations

import logging
from typing import List

import geopandas as gpd
import numpy as np
from shapely.geometry import MultiPoint, Polygon

from .roof_fitting import RoofPlane

log = logging.getLogger(__name__)


def planes_to_3d_gdf(planes: List[RoofPlane], crs) -> gpd.GeoDataFrame:
    """
    Build a 3D GeoDataFrame with one feature per roof facet: its 2D convex
    hull footprint (in x, y), lifted to real elevation (z) via the fitted
    plane equation at each hull vertex, plus descriptive attributes
    (slope, aspect, point support, area).
    """
    records = []
    for plane in planes:
        pts2d = plane.inlier_points[:, :2]
        if len(pts2d) < 3:
            log.warning("Facet %d has < 3 points, skipping in 3D vector output", plane.plane_id)
            continue

        hull = MultiPoint(pts2d).convex_hull
        if hull.geom_type != "Polygon":
            log.warning("Facet %d's points are degenerate (collinear), skipping", plane.plane_id)
            continue

        xs, ys = hull.exterior.coords.xy
        xs = np.asarray(xs)
        ys = np.asarray(ys)
        zs = plane.z_at(xs, ys)
        poly3d = Polygon(list(zip(xs, ys, zs)))

        records.append({
            "plane_id": plane.plane_id,
            "n_points": plane.n_inliers,
            "slope_deg": round(plane.slope_deg, 2),
            "aspect_deg": round(plane.aspect_deg, 2),
            "area_xy": round(plane.area_xy, 3),
            "normal_x": round(float(plane.normal[0]), 4),
            "normal_y": round(float(plane.normal[1]), 4),
            "normal_z": round(float(plane.normal[2]), 4),
            "geometry": poly3d,
        })

    if not records:
        raise ValueError(
            "No roof facet had enough non-degenerate points to build a 3D polygon."
        )

    return gpd.GeoDataFrame(records, geometry="geometry", crs=crs)


def save_3d_vector(gdf: gpd.GeoDataFrame, out_path: str) -> str:
    """Save a Z-enabled GeoDataFrame to GeoJSON or GeoPackage (both
    preserve the z coordinate; ESRI Shapefile does not reliably, so it's
    intentionally not supported here)."""
    ext = out_path.lower().rsplit(".", 1)[-1]
    driver = {"geojson": "GeoJSON", "json": "GeoJSON", "gpkg": "GPKG"}.get(ext)
    if driver is None:
        raise ValueError(
            f"Unsupported 3D output extension '.{ext}'. Use .geojson or .gpkg "
            f"(Shapefile doesn't reliably preserve Z coordinates)."
        )
    gdf.to_file(out_path, driver=driver)
    log.info("Wrote %d 3D facet(s) to %s (%s)", len(gdf), out_path, driver)
    return out_path

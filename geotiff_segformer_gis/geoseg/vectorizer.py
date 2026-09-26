"""
vectorizer.py
-------------
Converts a full-resolution class-label raster (aligned to the source
GeoTIFF's pixel grid) into proper GIS vector polygons, keeping the
original CRS and real-world coordinates intact throughout.
"""

from __future__ import annotations

import logging
from typing import Dict, Optional

import geopandas as gpd
import numpy as np
from rasterio.features import shapes
from rasterio.transform import Affine
from shapely.geometry import shape

log = logging.getLogger(__name__)


def raster_to_vector(
    label_array: np.ndarray,
    transform: Affine,
    crs,
    class_names: Optional[Dict[int, str]] = None,
    ignore_labels: Optional[set] = None,
    simplify_tolerance: Optional[float] = None,
    min_area: Optional[float] = None,
) -> gpd.GeoDataFrame:
    """
    label_array: (H, W) int array of predicted class ids, in the SAME
        pixel grid as the source GeoTIFF (i.e. produced by the tiler +
        LabelMosaic, so transform still applies unchanged).
    transform: the source GeoTIFF's affine transform.
    crs: the source GeoTIFF's CRS (or a user-specified target CRS).
    class_names: optional {id: name} mapping for a readable 'class' column.
    ignore_labels: label ids to drop entirely (e.g. {0} for background).
    simplify_tolerance: optional Douglas-Peucker tolerance in CRS units
        (e.g. meters for a projected CRS) to smooth polygon edges.
    min_area: drop polygons smaller than this area (in CRS units^2).
    """
    ignore_labels = ignore_labels or set()
    label_array = label_array.astype(np.int32)

    mask = ~np.isin(label_array, list(ignore_labels)) if ignore_labels else None

    records = []
    for geom, value in shapes(label_array, mask=mask, transform=transform):
        cls_id = int(value)
        records.append({"class_id": cls_id, "geometry": shape(geom)})

    if not records:
        log.warning("No polygons produced — check ignore_labels / model output.")
        return gpd.GeoDataFrame(columns=["class_id", "geometry"], geometry="geometry", crs=crs)

    gdf = gpd.GeoDataFrame(records, geometry="geometry", crs=crs)

    if class_names:
        gdf["class"] = gdf["class_id"].map(class_names).fillna("unknown")

    if simplify_tolerance:
        gdf["geometry"] = gdf.geometry.simplify(simplify_tolerance, preserve_topology=True)

    if min_area:
        gdf = gdf[gdf.geometry.area >= min_area].reset_index(drop=True)

    # Merge adjacent polygons of the same class into multipolygons for a
    # cleaner layer (dissolve), keeping per-class attributes.
    group_cols = ["class_id", "class"] if "class" in gdf.columns else ["class_id"]
    gdf = gdf.dissolve(by=group_cols, as_index=False)

    gdf["area"] = gdf.geometry.area
    gdf["perimeter"] = gdf.geometry.length

    return gdf


def save_vector(gdf: gpd.GeoDataFrame, out_path: str) -> str:
    """Save to GeoJSON / GeoPackage / Shapefile based on file extension."""
    ext = out_path.lower().rsplit(".", 1)[-1]
    driver = {
        "geojson": "GeoJSON",
        "json": "GeoJSON",
        "gpkg": "GPKG",
        "shp": "ESRI Shapefile",
    }.get(ext)

    if driver is None:
        raise ValueError(
            f"Unsupported output extension '.{ext}'. Use .geojson, .gpkg, or .shp"
        )

    gdf.to_file(out_path, driver=driver)
    log.info("Wrote %d features to %s (%s)", len(gdf), out_path, driver)
    return out_path

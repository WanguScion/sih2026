"""
clipper.py
----------
Clips a raster (e.g. a GSM / signal-strength raster, a DEM, or any other
GeoTIFF) to the boundary of a vector polygon layer — typically the
GeoJSON/GPKG produced by `vectorizer.raster_to_vector()`.

Handles CRS mismatches automatically (reprojects the vector to the
raster's CRS before masking, never the other way around, since clipping
should happen in the raster's native grid to avoid resampling it).
"""

from __future__ import annotations

import logging
from typing import Optional, Union

import geopandas as gpd
import rasterio
from rasterio.mask import mask as rio_mask

log = logging.getLogger(__name__)


def clip_raster_by_vector(
    raster_path: str,
    vector: Union[str, gpd.GeoDataFrame],
    out_path: str,
    all_touched: bool = False,
    crop: bool = True,
    nodata: Optional[float] = None,
    invert: bool = False,
) -> str:
    """
    Clip `raster_path` to the union of geometries in `vector`
    (a GeoDataFrame, or a path to any vector file rasterio/geopandas can
    read: .geojson, .gpkg, .shp).

    raster_path: path to the input raster to be clipped (e.g. GSM data).
    vector: GeoDataFrame or path to a vector file (e.g. the segmentation
        output GeoJSON). If it has multiple polygons/classes, ALL of them
        are used as the clip boundary (their union) unless you pre-filter
        the GeoDataFrame yourself before calling this.
    out_path: where to write the clipped raster (GeoTIFF).
    all_touched: if True, include pixels touched by the polygon boundary,
        not just pixels whose center falls inside it (useful for thin
        polygons / narrow corridors).
    crop: if True (default), the output raster is cropped to the polygon's
        bounding box; if False, output keeps the original raster's full
        extent with pixels outside the polygon set to `nodata`.
    nodata: value to assign to pixels outside the polygon. Defaults to
        the source raster's existing nodata value, or 0 if none is set.
    invert: if True, mask OUT the polygon area instead of keeping it
        (i.e. clip to everything EXCEPT the polygon).

    Returns out_path.
    """
    if isinstance(vector, str):
        gdf = gpd.read_file(vector)
    else:
        gdf = vector

    if gdf.empty:
        raise ValueError("Clip vector has no geometries — nothing to clip to.")

    with rasterio.open(raster_path) as src:
        raster_crs = src.crs
        if raster_crs is None:
            raise ValueError(
                f"'{raster_path}' has no CRS — cannot align it with the clip "
                f"polygon. Assign a CRS to the raster first."
            )

        if gdf.crs is None:
            raise ValueError(
                "Clip vector has no CRS. The GeoJSON produced by this "
                "pipeline always carries a CRS — if you're passing a "
                "different file, make sure it's properly georeferenced."
            )

        if gdf.crs != raster_crs:
            log.info("Reprojecting clip vector from %s to raster CRS %s", gdf.crs, raster_crs)
            gdf = gdf.to_crs(raster_crs)

        geometries = [geom.__geo_interface__ for geom in gdf.geometry if geom is not None and not geom.is_empty]
        if not geometries:
            raise ValueError("Clip vector has no valid (non-empty) geometries.")

        fill_value = nodata if nodata is not None else (src.nodata if src.nodata is not None else 0)

        try:
            out_image, out_transform = rio_mask(
                src,
                geometries,
                crop=crop,
                all_touched=all_touched,
                nodata=fill_value,
                invert=invert,
            )
        except ValueError as exc:
            # rasterio raises ValueError when the shapes don't overlap the raster at all
            raise ValueError(
                f"Clip polygon does not overlap '{raster_path}' at all — check "
                f"that both datasets cover the same real-world area."
            ) from exc

        out_meta = src.meta.copy()
        out_meta.update(
            {
                "height": out_image.shape[1],
                "width": out_image.shape[2],
                "transform": out_transform,
                "nodata": fill_value,
            }
        )

    with rasterio.open(out_path, "w", **out_meta) as dst:
        dst.write(out_image)

    log.info(
        "Clipped '%s' -> '%s' (%d bands, %dx%d px)",
        raster_path, out_path, out_image.shape[0], out_image.shape[2], out_image.shape[1],
    )
    return out_path

"""
pointcloud.py
-------------
Converts a DSM (Digital Surface Model) raster — typically already clipped
to a single building's footprint via `clipper.clip_raster_by_vector()` —
into an (N, 3) array of real-world (x, y, z) coordinates, ready for RANSAC
plane fitting.
"""

from __future__ import annotations

import logging
from typing import Optional, Tuple

import numpy as np
import rasterio
from rasterio.crs import CRS

log = logging.getLogger(__name__)


def raster_to_point_cloud(
    raster_path: str,
    band: int = 1,
    z_scale: float = 1.0,
    max_points: Optional[int] = None,
    random_state: int = 0,
) -> Tuple[np.ndarray, CRS]:
    """
    Read a single-band elevation raster and return every valid pixel as a
    real-world (x, y, z) point, where x/y come from the pixel center in
    the raster's CRS (via its affine transform) and z is the pixel's
    elevation value.

    band: which band to read as elevation (DSMs are normally single-band).
    z_scale: multiply elevation values by this (e.g. if the DSM stores
        elevation in different units than x/y, such as feet vs meters).
    max_points: if set, randomly subsample down to this many points
        (useful for very large clipped areas to keep RANSAC fast; a
        building-sized clip usually doesn't need this).

    Returns (points, crs) where points is (N, 3) float64: columns x, y, z.
    Raises ValueError if the raster has no valid (non-nodata, finite) pixels.
    """
    with rasterio.open(raster_path) as src:
        if src.crs is None:
            raise ValueError(
                f"'{raster_path}' has no CRS — cannot produce real-world (x, y, z) "
                f"coordinates from it."
            )
        arr = src.read(band).astype(np.float64)
        nodata = src.nodata
        transform = src.transform
        crs = src.crs

    valid = np.isfinite(arr)
    if nodata is not None:
        valid &= arr != nodata

    rows, cols = np.where(valid)
    if rows.size == 0:
        raise ValueError(
            f"'{raster_path}' has no valid elevation pixels (all nodata/NaN). "
            f"Check that the DSM actually overlaps the clip area."
        )

    xs, ys = rasterio.transform.xy(transform, rows, cols, offset="center")
    xs = np.asarray(xs, dtype=np.float64)
    ys = np.asarray(ys, dtype=np.float64)
    zs = arr[rows, cols] * z_scale

    points = np.column_stack([xs, ys, zs])

    if max_points is not None and len(points) > max_points:
        rng = np.random.default_rng(random_state)
        idx = rng.choice(len(points), size=max_points, replace=False)
        points = points[idx]
        log.info("Subsampled point cloud from %d to %d points", rows.size, max_points)

    log.info("Extracted %d points from '%s' (z range %.2f - %.2f)",
              len(points), raster_path, points[:, 2].min(), points[:, 2].max())
    return points, crs

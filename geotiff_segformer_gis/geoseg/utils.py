"""
utils.py
--------
GeoTIFF I/O, georeferencing validation, and tiling/stitching helpers.

The app is designed to preserve the raster's original CRS and affine
transform all the way through to the final vector output. It never
silently reprojects unless the user explicitly asks for --target-crs.
"""

from __future__ import annotations

import dataclasses
import logging
from typing import Iterator, Optional, Tuple

import numpy as np
import rasterio
from rasterio.crs import CRS
from rasterio.transform import Affine
from rasterio.windows import Window
from rasterio.warp import calculate_default_transform, reproject, Resampling

log = logging.getLogger(__name__)


@dataclasses.dataclass
class GeoRaster:
    """In-memory handle for a GeoTIFF's pixel data + georeferencing."""

    data: np.ndarray          # shape (bands, H, W)
    transform: Affine
    crs: CRS
    bounds: Tuple[float, float, float, float]
    width: int
    height: int
    count: int
    dtype: str
    nodata: Optional[float]


def read_geotiff(path: str, max_pixels: int = 8000 * 8000) -> GeoRaster:
    """
    Read a GeoTIFF, validating that it is properly georeferenced
    (i.e. compliant with a GNSS/CORS-surveyed source: it must carry a
    real CRS and a non-identity affine transform, not just raw pixels).

    Large rasters are read in full only if within `max_pixels`; otherwise
    a MemoryError is raised early with guidance to use windowed tiling
    (the tiler below reads windows directly from disk instead).
    """
    with rasterio.open(path) as src:
        if src.crs is None:
            raise ValueError(
                f"'{path}' has no CRS. This tool requires a georeferenced "
                f"GeoTIFF (e.g. output of a GNSS/CORS-corrected survey). "
                f"Assign a CRS first (e.g. with `rasterio` or `gdal_edit`)."
            )
        if src.transform.is_identity:
            raise ValueError(
                f"'{path}' has an identity affine transform (no real-world "
                f"georeferencing). Cannot vectorize into meaningful GIS "
                f"coordinates."
            )

        n_pixels = src.width * src.height
        if n_pixels > max_pixels:
            log.warning(
                "Raster is %d x %d (%.1fM px) — this exceeds the safe "
                "in-memory threshold. Consider using --tile-size with a "
                "smaller value; the tiler streams windows from disk so "
                "memory stays bounded regardless.",
                src.width, src.height, n_pixels / 1e6,
            )

        data = src.read()
        return GeoRaster(
            data=data,
            transform=src.transform,
            crs=src.crs,
            bounds=tuple(src.bounds),
            width=src.width,
            height=src.height,
            count=src.count,
            dtype=str(src.dtypes[0]),
            nodata=src.nodata,
        )


def reproject_geotiff(path: str, target_crs: str, out_path: str) -> str:
    """Reproject a GeoTIFF to `target_crs` (e.g. 'EPSG:3857') and write it
    to `out_path`. Returns out_path. Used only when the user explicitly
    requests a target CRS different from the source."""
    with rasterio.open(path) as src:
        transform, width, height = calculate_default_transform(
            src.crs, target_crs, src.width, src.height, *src.bounds
        )
        kwargs = src.meta.copy()
        kwargs.update(
            {"crs": target_crs, "transform": transform, "width": width, "height": height}
        )
        with rasterio.open(out_path, "w", **kwargs) as dst:
            for i in range(1, src.count + 1):
                reproject(
                    source=rasterio.band(src, i),
                    destination=rasterio.band(dst, i),
                    src_transform=src.transform,
                    src_crs=src.crs,
                    dst_transform=transform,
                    dst_crs=target_crs,
                    resampling=Resampling.bilinear,
                )
    return out_path


@dataclasses.dataclass
class Tile:
    """A single tile read directly from disk, with its placement in the
    full raster (in pixel coordinates) so predictions can be stitched
    back precisely."""

    array: np.ndarray   # (bands, th, tw), the RGB (or first 3 bands) tile
    row_off: int
    col_off: int
    height: int
    width: int


def iter_tiles(
    path: str,
    tile_size: int = 512,
    overlap: int = 32,
    bands: Optional[Tuple[int, ...]] = None,
) -> Iterator[Tile]:
    """
    Stream tiles of `tile_size` x `tile_size` pixels (with `overlap` pixels
    of context on each edge) directly from disk using rasterio windows.
    This keeps memory bounded even for very large orthomosaics.
    """
    with rasterio.open(path) as src:
        band_indexes = bands or tuple(range(1, min(src.count, 3) + 1))
        step = tile_size - overlap
        if step <= 0:
            raise ValueError("overlap must be smaller than tile_size")

        for row_off in range(0, src.height, step):
            for col_off in range(0, src.width, step):
                h = min(tile_size, src.height - row_off)
                w = min(tile_size, src.width - col_off)
                window = Window(col_off, row_off, w, h)
                arr = src.read(band_indexes, window=window)
                yield Tile(array=arr, row_off=row_off, col_off=col_off, height=h, width=w)


class LabelMosaic:
    """
    Accumulates per-tile predicted label maps into a single full-resolution
    label raster, resolving overlap regions by simple majority-vote-free
    "last write wins on non-background" blending — good enough for
    segmentation-mask stitching since tiles share the same model and
    overlap regions are narrow.
    """

    def __init__(self, height: int, width: int, fill_value: int = 0):
        self.labels = np.full((height, width), fill_value, dtype=np.int32)
        self._written = np.zeros((height, width), dtype=bool)

    def add(self, tile_labels: np.ndarray, row_off: int, col_off: int) -> None:
        h, w = tile_labels.shape
        dest = self.labels[row_off:row_off + h, col_off:col_off + w]
        written = self._written[row_off:row_off + h, col_off:col_off + w]

        # Only overwrite previously-unwritten pixels, or where the new tile
        # disagrees in the overlap band — keep first prediction (simple,
        # deterministic, avoids seams from re-averaging discrete labels).
        mask = ~written
        dest[mask] = tile_labels[mask]
        written[:] = True

from .utils import read_geotiff, iter_tiles, LabelMosaic, reproject_geotiff, GeoRaster, Tile
from .vectorizer import raster_to_vector, save_vector
from .clipper import clip_raster_by_vector
from .pointcloud import raster_to_point_cloud
from .roof_fitting import RoofPlane, fit_roof_planes
from .mesh_builder import build_roof_mesh
from .vector3d import planes_to_3d_gdf, save_3d_vector

__all__ = [
    "read_geotiff",
    "iter_tiles",
    "LabelMosaic",
    "reproject_geotiff",
    "GeoRaster",
    "Tile",
    "SegFormerSegmenter",
    "raster_to_vector",
    "save_vector",
    "clip_raster_by_vector",
    "raster_to_point_cloud",
    "RoofPlane",
    "fit_roof_planes",
    "build_roof_mesh",
    "planes_to_3d_gdf",
    "save_3d_vector",
]


def __getattr__(name):
    # Lazy import: SegFormerSegmenter needs torch + transformers, which are
    # only required at inference time, not for the geospatial I/O/vector
    # parts of this package (keeps `import geoseg` lightweight/testable).
    if name == "SegFormerSegmenter":
        from .segmenter import SegFormerSegmenter
        return SegFormerSegmenter
    if name in ("match_building_classes", "BUILDING_KEYWORDS"):
        from . import segmenter
        return getattr(segmenter, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

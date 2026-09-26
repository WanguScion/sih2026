"""
mesh_builder.py
----------------
Turns a set of fitted RoofPlane facets plus the building's 2D footprint
polygon (from the SegFormer building mask) into a single watertight-ish
3D triangle mesh: a triangulated roof surface per facet, closed off with
extruded wall triangles down to a base elevation.
"""

from __future__ import annotations

import logging
from typing import List, Optional

import numpy as np
import trimesh
from scipy.spatial import Delaunay
from shapely.geometry import Polygon, MultiPolygon

from .roof_fitting import RoofPlane

log = logging.getLogger(__name__)


def _triangulate_plane(plane: RoofPlane, max_edge_factor: float = 3.0):
    """Delaunay-triangulate one facet's inlier points in (x, y), evaluate
    z from the fitted plane (a clean geometric surface rather than noisy
    raw DSM z), and drop long/thin boundary artifact triangles."""
    pts2d = plane.inlier_points[:, :2]
    if len(pts2d) < 3:
        return np.empty((0, 3)), np.empty((0, 3), dtype=int)

    try:
        tri = Delaunay(pts2d)
    except Exception as exc:
        log.warning("Delaunay triangulation failed for facet %d: %s", plane.plane_id, exc)
        return np.empty((0, 3)), np.empty((0, 3), dtype=int)

    simplices = tri.simplices
    edge_lengths = np.concatenate([
        np.linalg.norm(pts2d[simplices[:, 0]] - pts2d[simplices[:, 1]], axis=1),
        np.linalg.norm(pts2d[simplices[:, 1]] - pts2d[simplices[:, 2]], axis=1),
        np.linalg.norm(pts2d[simplices[:, 2]] - pts2d[simplices[:, 0]], axis=1),
    ])
    median_edge = np.median(edge_lengths) if len(edge_lengths) else 1.0
    max_edge = max(median_edge * max_edge_factor, 1e-6)

    max_per_tri = np.maximum.reduce([
        np.linalg.norm(pts2d[simplices[:, 0]] - pts2d[simplices[:, 1]], axis=1),
        np.linalg.norm(pts2d[simplices[:, 1]] - pts2d[simplices[:, 2]], axis=1),
        np.linalg.norm(pts2d[simplices[:, 2]] - pts2d[simplices[:, 0]], axis=1),
    ])
    keep = simplices[max_per_tri <= max_edge]

    z = plane.z_at(pts2d[:, 0], pts2d[:, 1])
    verts3d = np.column_stack([pts2d, z])
    return verts3d, keep


def _nearest_plane_z(x: float, y: float, planes: List[RoofPlane]) -> float:
    """Estimate roof height at an arbitrary (x, y) — used for wall-top
    vertices along the footprint boundary — by evaluating the plane whose
    inlier points are closest to that location."""
    best_dist = None
    best_plane = None
    for plane in planes:
        d = np.min(np.hypot(plane.inlier_points[:, 0] - x, plane.inlier_points[:, 1] - y))
        if best_dist is None or d < best_dist:
            best_dist = d
            best_plane = plane
    return float(best_plane.z_at(np.array([x]), np.array([y]))[0])


def _build_walls(planes: List[RoofPlane], polygon: Polygon, base_elevation: float):
    coords = list(polygon.exterior.coords)
    top_pts = [(x, y, _nearest_plane_z(x, y, planes)) for x, y in coords]
    bottom_pts = [(x, y, base_elevation) for x, y, _ in top_pts]

    vertices, faces = [], []
    n = len(top_pts) - 1  # exterior.coords repeats the first point at the end
    for i in range(n):
        t0, t1 = top_pts[i], top_pts[i + 1]
        b0, b1 = bottom_pts[i], bottom_pts[i + 1]
        base_idx = len(vertices)
        vertices.extend([t0, t1, b1, b0])
        faces.append([base_idx, base_idx + 1, base_idx + 2])
        faces.append([base_idx, base_idx + 2, base_idx + 3])

    if not faces:
        return np.empty((0, 3)), np.empty((0, 3), dtype=int)
    return np.array(vertices), np.array(faces)


def build_roof_mesh(
    planes: List[RoofPlane],
    footprint_polygon: Optional[Polygon] = None,
    base_elevation: Optional[float] = None,
    max_edge_factor: float = 3.0,
) -> trimesh.Trimesh:
    """
    planes: RoofPlane facets from `roof_fitting.fit_roof_planes()`.
    footprint_polygon: the building's 2D footprint (shapely Polygon or
        MultiPolygon — from the SegFormer building mask). When given
        together with `base_elevation`, walls are extruded from the roof
        edge down to that elevation to close the mesh into a solid.
    base_elevation: elevation (in the same CRS z-units as the DSM) to
        extrude walls down to — e.g. ground level, or the DSM's minimum
        clipped elevation. Required (together with footprint_polygon) to
        produce a closed solid; if omitted, only the roof surface is
        returned (an open shell).

    Returns a trimesh.Trimesh with real-world (x, y, z) vertices.
    """
    roof_vertices, roof_faces = [], []
    offset = 0
    for plane in planes:
        verts, faces = _triangulate_plane(plane, max_edge_factor=max_edge_factor)
        if len(faces) == 0:
            log.warning("Facet %d produced no valid triangles (too few/degenerate points)",
                        plane.plane_id)
            continue
        roof_vertices.append(verts)
        roof_faces.append(faces + offset)
        offset += len(verts)

    if not roof_vertices:
        raise ValueError(
            "No triangles could be generated from any roof facet — check that "
            "planes have enough well-distributed inlier points."
        )

    mesh = trimesh.Trimesh(
        vertices=np.vstack(roof_vertices), faces=np.vstack(roof_faces), process=True
    )

    if footprint_polygon is not None and base_elevation is not None:
        polys = list(footprint_polygon.geoms) if isinstance(footprint_polygon, MultiPolygon) \
            else [footprint_polygon]
        wall_meshes = []
        for poly in polys:
            w_verts, w_faces = _build_walls(planes, poly, base_elevation)
            if len(w_faces):
                wall_meshes.append(trimesh.Trimesh(vertices=w_verts, faces=w_faces, process=True))
        if wall_meshes:
            mesh = trimesh.util.concatenate([mesh] + wall_meshes)

    log.info("Built mesh: %d vertices, %d faces", len(mesh.vertices), len(mesh.faces))
    return mesh

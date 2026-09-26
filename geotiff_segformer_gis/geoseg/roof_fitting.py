"""
roof_fitting.py
----------------
Fits planar roof facets to a building's DSM point cloud using iterative
RANSAC: fit the best-supported plane, set aside its inliers as one roof
facet, then repeat on the leftover points to find additional facets (flat
roofs -> 1 plane; gable/hip roofs -> multiple planes).

Each plane is expressed as z = a*x + b*y + c (valid for any roof facet
that isn't vertical, which covers the overwhelming majority of real
rooftops — flat, shed, gable, hip, gambrel, etc).
"""

from __future__ import annotations

import dataclasses
import logging
from typing import List, Optional

import numpy as np
from sklearn.linear_model import LinearRegression, RANSACRegressor

log = logging.getLogger(__name__)


@dataclasses.dataclass
class RoofPlane:
    """One planar roof facet: z = a*x + b*y + c, plus the DSM points that
    support it and some descriptive geometry (slope/aspect)."""

    plane_id: int
    a: float
    b: float
    c: float
    inlier_points: np.ndarray   # (N, 3) — the (x, y, z) points belonging to this facet
    normal: np.ndarray          # unit normal vector (nx, ny, nz), nz > 0
    slope_deg: float            # 0 = flat, 90 = vertical
    aspect_deg: float           # compass direction the facet faces downhill, 0=N/360, 90=E

    def z_at(self, x: np.ndarray, y: np.ndarray) -> np.ndarray:
        """Evaluate the fitted plane at arbitrary (x, y) — used to build
        mesh/footprint vertices that weren't necessarily original DSM points."""
        return self.a * x + self.b * y + self.c

    @property
    def n_inliers(self) -> int:
        return len(self.inlier_points)

    @property
    def area_xy(self) -> float:
        """Rough planar footprint area of this facet's inlier points via
        their 2D convex hull (used for reporting / sanity checks)."""
        from scipy.spatial import ConvexHull
        if self.n_inliers < 3:
            return 0.0
        try:
            hull = ConvexHull(self.inlier_points[:, :2])
            return float(hull.volume)  # 'volume' of a 2D hull == its area
        except Exception:
            return 0.0


def fit_roof_planes(
    points: np.ndarray,
    min_points_per_plane: int = 25,
    residual_threshold: float = 0.25,
    max_planes: int = 8,
    max_trials: int = 300,
    random_state: int = 0,
) -> List[RoofPlane]:
    """
    points: (N, 3) array of (x, y, z) DSM points for one building
        (typically the output of `pointcloud.raster_to_point_cloud()`
        on a DSM already clipped to that building's footprint).
    min_points_per_plane: stop once no remaining plane has at least this
        many inlier points — prevents fitting noise as a "facet".
    residual_threshold: max vertical distance (in z units, e.g. meters)
        from the fitted plane for a point to count as an inlier. Tighter
        = cleaner facets but may split a slightly noisy real facet into
        two; looser = risk of merging genuinely different facets.
    max_planes: hard cap on the number of facets to extract (most
        residential roofs are 1-6 facets; raise this for complex roofs).

    Returns a list of RoofPlane, ordered by extraction order (largest/
    best-supported plane first, since RANSAC is re-run on the full
    remaining point set each iteration).
    """
    if len(points) < min_points_per_plane:
        raise ValueError(
            f"Only {len(points)} DSM points available, need at least "
            f"{min_points_per_plane} to fit even one roof plane. Check the "
            f"clip area / DSM resolution."
        )

    remaining = points.copy()
    planes: List[RoofPlane] = []
    plane_id = 0

    while len(remaining) >= min_points_per_plane and plane_id < max_planes:
        xy = remaining[:, :2]
        z = remaining[:, 2]

        # Center xy for numerical stability (UTM-style coordinates are
        # large, e.g. ~500000) — this only shifts the intercept, not the
        # fitted slope, so we un-shift it back below.
        xy_mean = xy.mean(axis=0)
        xy_centered = xy - xy_mean

        ransac = RANSACRegressor(
            estimator=LinearRegression(),
            residual_threshold=residual_threshold,
            max_trials=max_trials,
            min_samples=3,
            random_state=random_state,
        )
        try:
            ransac.fit(xy_centered, z)
        except ValueError as exc:
            log.info("RANSAC could not fit another plane (%s); stopping at %d facet(s)",
                      exc, len(planes))
            break

        inlier_mask = ransac.inlier_mask_
        n_inliers = int(inlier_mask.sum())
        if n_inliers < min_points_per_plane:
            log.info("Best remaining plane only has %d inliers (< %d); stopping at %d facet(s)",
                      n_inliers, min_points_per_plane, len(planes))
            break

        a, b = ransac.estimator_.coef_
        c_centered = ransac.estimator_.intercept_
        c = float(c_centered - a * xy_mean[0] - b * xy_mean[1])  # back to real-world coords

        normal = np.array([-a, -b, 1.0])
        normal = normal / np.linalg.norm(normal)
        slope_deg = float(np.degrees(np.arccos(np.clip(abs(normal[2]), -1, 1))))
        aspect_deg = float(np.degrees(np.arctan2(normal[0], normal[1])) % 360)

        planes.append(RoofPlane(
            plane_id=plane_id,
            a=float(a), b=float(b), c=c,
            inlier_points=remaining[inlier_mask].copy(),
            normal=normal,
            slope_deg=slope_deg,
            aspect_deg=aspect_deg,
        ))
        log.info("Facet %d: %d inliers, slope=%.1f deg, aspect=%.0f deg",
                  plane_id, n_inliers, slope_deg, aspect_deg)

        remaining = remaining[~inlier_mask]
        plane_id += 1

    if not planes:
        raise ValueError(
            "No roof planes could be fit — try a looser --residual-threshold "
            "or a lower --min-points-per-plane."
        )
    return planes

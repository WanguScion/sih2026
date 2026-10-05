# GeoTIFF → SegFormer → 3D GIS Vector Data (Buildings + Reconstructed Roofs)

One script, `main.py`, that takes a georeferenced orthophoto and a DSM
(Digital Surface Model), and produces final 3D GIS vector data of
reconstructed building roofs:

```bash
python main.py
```

With no flags at all, this looks for `survey.tif` (orthophoto) and
`dsm.tif` (DSM) in the current directory and writes everything to
`./output/`. Override any of it — `--input`, `--dsm`, `--output-dir`, etc.
— see [Usage](#usage) below.

## Pipeline

```
survey.tif (orthophoto: CRS + affine transform)
   │  validate georeferencing
   ▼
Windowed tiling (streamed from disk, memory-bounded)
   │  per tile
   ▼
SegFormer inference (HuggingFace transformers)
   │  argmax logits → per-pixel class id
   │  building-only filter (default): every class except
   │  building/house/skyscraper/... is dropped
   ▼
Stitch tiles → full-resolution class-label raster
   ▼
rasterio.features.shapes → building footprint polygons
   (real-world coordinates, same CRS as the input)
   ▼
output/buildings.geojson  ← 2D footprints
   │
   ▼  for each building footprint:
Clip dsm.tif to that footprint (geoseg/clipper.py)
   ▼
Clipped DSM → (x, y, z) point cloud (geoseg/pointcloud.py)
   ▼
Iterative RANSAC → planar roof facets (geoseg/roof_fitting.py)
   ▼
Triangulated roof + extruded walls → mesh (geoseg/mesh_builder.py)
   ▼
Fitted facets → 3D (x, y, z) polygons (geoseg/vector3d.py)
   │
   ▼  combine every building
output/roofs.obj        ← final 3D mesh
output/roofs_3d.geojson     ← final 3D (x, y, z) GIS vector data
```

Everything from "SegFormer inference" through the final outputs runs in
one `python main.py` call — segmentation, DSM clipping, RANSAC plane
fitting, mesh building, and 3D vector export are all stages of the same
script (`geoseg/` holds the individual building blocks each stage calls
into; `main.py` orchestrates all of them).

## Inputs / outputs at a glance

| | Path (default) | What it is |
|---|---|---|
| **input** | `survey.tif` | Georeferenced orthophoto (RGB), e.g. from a GNSS/CORS-corrected drone or satellite survey |
| **input** | `dsm.tif` | Digital Surface Model — an elevation raster that includes rooftops (not bare-earth-only like a DTM/DEM) |
| output | `output/buildings.geojson` | 2D building footprint polygons from SegFormer (no Z) |
| output | `output/roofs.obj` | Combined 3D mesh (roof surfaces + walls) for every reconstructed building |
| output | `output/roofs_3d.geojson` | **Final 3D GIS vector data**: one `PolygonZ` feature per roof facet, real (x, y, z) coordinates, with `slope_deg` / `aspect_deg` / `building_id` attributes |

The orthophoto and DSM need to cover overlapping ground. They don't need
to share a CRS with each other — the DSM clip step reprojects the clip
polygon to the DSM's CRS automatically — but they do need to be
georeferenced in *some* real CRS each.

## Install

```bash
pip install -r requirements.txt
```

Requires `rasterio`, `geopandas`, `shapely`, `scipy`, `scikit-learn`,
`trimesh`, `torch`, and `transformers`. GDAL system libraries are
required by `rasterio` (usually installed as a rasterio wheel dependency;
on some Linux distros you may need `apt install gdal-bin libgdal-dev`
first).

## Usage

Simplest case — `survey.tif` and `dsm.tif` in the current directory:

```bash
python main.py
```

Everything else is an optional override:

```bash
python main.py \
  --input my_ortho.tif \
  --dsm my_dsm.tif \
  --output-dir results/ \
  --model nvidia/segformer-b0-finetuned-ade-512-512 \
  --tile-size 512 --overlap 32 \
  --residual-threshold 0.2 --max-planes 6 \
  -v
```

### Key arguments

| Flag | Purpose |
|---|---|
| `--input` | Orthophoto path (default `survey.tif`) |
| `--dsm` | DSM path (default `dsm.tif`) |
| `--output-dir` | Where `buildings.geojson` / `roofs.obj` / `roofs_3d.geojson` are written (default `output/`) |
| `--model` | Any SegFormer checkpoint: a Hub id or local fine-tuned checkpoint dir |
| `--tile-size` / `--overlap` | Controls memory use and edge-seam quality |
| `--target-crs` | Reproject the orthophoto before segmentation, e.g. `EPSG:3857` |
| `--all-classes` / `--building-keywords` | Segmentation class filtering — see below |
| `--residual-threshold` | RANSAC: max vertical distance (meters) from a plane for a point to count as an inlier |
| `--min-points-per-plane` / `--max-planes` | RANSAC: stop conditions for facet extraction |
| `--base-elevation` | Wall extrusion floor — see the caveat below |

Run `python main.py --help` for the full list (simplification/min-area for
footprints, point-cloud subsampling, mesh triangle filtering, etc).

## Building-only polygon masks

By default, only **building/structure classes** are vectorized —
everything else the model predicts (roads, vegetation, sky, cars, ...) is
dropped before polygons are generated. This is implemented in
`geoseg/segmenter.py`'s `match_building_classes()`: it matches a model's
own `id2label` names against a keyword list (`building`, `house`,
`skyscraper`, `hovel`, `hut`, `roof` by default), so it works both for
general scene-segmentation checkpoints (ADE20K-style, where "building" is
one of many classes) and for binary/few-class checkpoints fine-tuned
specifically for building extraction.

- Disable this and keep every class: `--all-classes` (falls back to
  `--ignore-labels` to drop specific ids instead).
- Point it at a model whose building class isn't matched by the default
  keywords: `--building-keywords structure,roof_polygon` (run with `-v`
  first to see the model's actual class list in the logs).

## Why "GNSS/CORS-compliant" matters here

GNSS/CORS-corrected surveys produce imagery with centimeter-to-meter
accurate georeferencing. This tool treats that as a hard requirement, not
an assumption:

- `read_geotiff()` **fails fast** if the orthophoto has no CRS or an
  identity affine transform, rather than silently producing meaningless
  "pixel-space" polygons.
- The source affine transform is carried through unchanged from
  segmentation through vectorization, so `buildings.geojson` coordinates
  are in true map units.
- DSM clipping reprojects the *clip polygon* to the DSM's CRS if they
  differ — never the DSM itself — so elevation values are never resampled
  just to align coordinate systems.
- Reprojecting the orthophoto, if requested, happens once, explicitly, at
  the start (`--target-crs`), using `rasterio.warp` — never implicitly.

## Choosing a model

The default (`nvidia/segformer-b0-finetuned-ade-512-512`) is a general
**ADE20K scene-segmentation** checkpoint — good for a smoke test, but for
real building extraction you'll want a checkpoint actually fine-tuned for
that: e.g. a SegFormer fine-tuned on **LoveDA**, **Potsdam/Vaihingen
(ISPRS)**, **DeepGlobe Land Cover**, **Massachusetts Buildings**, or your
own labeled orthomosaic tiles. Point `--model` at that Hub id or local
checkpoint directory — the rest of the pipeline is model-agnostic.

## Roof reconstruction details

For each building footprint, `main.py`:

1. **Clips the DSM** to that one building's footprint
   (`geoseg/clipper.py`).
2. **Converts the clipped DSM into an (x, y, z) point cloud**
   (`geoseg/pointcloud.py`): every valid pixel becomes one real-world
   point.
3. **Fits planar roof facets with iterative RANSAC**
   (`geoseg/roof_fitting.py`): repeatedly fits the best-supported plane
   `z = a·x + b·y + c` to the remaining points, sets its inliers aside as
   one facet, and repeats on the leftover points — so flat roofs yield
   one facet, gable/hip roofs yield several. Each facet reports its slope
   and compass aspect.
4. **Builds a 3D mesh** (`geoseg/mesh_builder.py`): Delaunay-triangulates
   each facet's points in (x, y) with z from the *fitted plane* (a clean
   geometric surface, not noisy raw DSM z), then extrudes walls from the
   footprint boundary down to a base elevation.
5. **Converts the fitted facets into 3D vector data**
   (`geoseg/vector3d.py`): each facet becomes a `Polygon Z` feature (its
   2D convex hull lifted to real elevation via the plane equation) with
   `slope_deg`, `aspect_deg`, point-support and area attributes.

All buildings are combined into one output mesh and one output vector
file — each facet's `building_id` attribute ties it back to its source
building.

### Caveat: `--base-elevation` and wall height

The DSM clip for each building only contains points **inside that
building's footprint** — it doesn't include surrounding ground. So the
default base elevation (each building's own minimum *clipped* DSM value)
is usually close to roof level, not true ground level, and walls will be
short. For walls that actually reach the ground, pass an explicit
`--base-elevation` (e.g. from a separate DTM/bare-earth model, or a known
site datum).

### Caveat: plane-fitting assumptions

Plane fitting assumes each facet is well-approximated by
`z = a·x + b·y + c`, which holds for the overwhelming majority of real
roof shapes (flat, shed, gable, hip, gambrel) but not literally vertical
surfaces (e.g. a mansard's steep lower slope, or dormers) — those may be
missed or folded into a neighboring facet. DSM resolution and noise also
set a practical floor on `--residual-threshold`: coarse or noisy DSMs
need a looser threshold, which in turn makes small architectural details
(chimneys, vents) more likely to be absorbed into the main facets rather
than detected separately.

## Clipping other rasters (e.g. GSM data)

`clip_gsm.py` is a separate, standalone CLI for clipping *any* raster to
*any* vector polygon boundary — useful for restricting some other dataset
(a GSM signal-strength raster, a DEM, etc.) to the buildings this
pipeline just found, or to any other polygon layer. It isn't part of
`main.py`'s pipeline since it's a general-purpose utility, not specific
to roof reconstruction:

```bash
python clip_gsm.py \
  --raster gsm_signal_strength.tif \
  --polygon output/buildings.geojson \
  --output gsm_clipped.tif
```

Restrict to specific classes first with `--class-filter`; see
`python clip_gsm.py --help` for the rest (`--all-touched`, `--invert`,
`--no-crop`, `--nodata`).

## Tests

```bash
python -m pytest tests/ -v
```

- `test_pipeline_no_model.py` — the core geospatial pipeline (tiling,
  stitching, vectorization, CRS/coordinate correctness) against a
  synthetic GeoTIFF, using a stand-in classifier.
- `test_clipper.py` — raster clipping: correct cropped extent, automatic
  CRS reprojection, error handling for non-overlapping geometry.
- `test_roof_reconstruction.py` — the building-class keyword matcher, and
  the DSM → point cloud → RANSAC → mesh → 3D vector chain against a
  synthetic gable-roof DSM (verifies RANSAC recovers exactly the 2
  expected facets at the correct ~45° slope, correct mesh height range,
  and Z-coordinate round-tripping through GeoJSON).
- `test_main_pipeline.py` — **integration test of the merged `main.py`
  pipeline itself**: calls `main.run()` end to end against a synthetic
  orthophoto + DSM (with a stand-in segmenter swapped in for
  `SegFormerSegmenter`, since real inference needs model weights), and
  verifies all three real output files (`buildings.geojson`, `roofs.obj`,
  `roofs_3d.geojson`) are produced with correct content.

None of the tests cover the SegFormer inference path itself, which needs
real model weights and network access to Hugging Face Hub (or a local
checkpoint).

## Project layout

```
main.py                        Full pipeline: orthophoto+DSM -> 3D GIS vector data
clip_gsm.py                    Standalone CLI to clip any raster to any vector polygon
geoseg/
  utils.py                     GeoTIFF I/O, CRS validation, tiling, stitching
  segmenter.py                 SegFormer model wrapper + building-class keyword matching
  vectorizer.py                Raster label array -> GeoDataFrame -> file
  clipper.py                   Clip a raster to a vector polygon boundary
  pointcloud.py                DSM raster -> (x, y, z) point cloud
  roof_fitting.py              Iterative RANSAC multi-plane roof facet fitting
  mesh_builder.py               Roof facets + footprint -> 3D triangle mesh
  vector3d.py                  Roof facets -> 3D (x, y, z) GIS vector data
tests/
  test_pipeline_no_model.py    Core geospatial pipeline correctness test
  test_clipper.py              Raster clipping correctness test
  test_roof_reconstruction.py  Building-class matching + RANSAC/mesh/3D-vector test
  test_main_pipeline.py        Integration test of the full merged main.py
requirements.txt
```

## Other notes & limitations

- Non-RGB/multispectral orthophotos: only the first 1–3 bands are fed to
  SegFormer (it expects RGB-like input).
- Overlap-region stitching for the segmentation raster uses "first write
  wins" (deterministic, seam-minimizing for discrete labels), not
  logit-blending — increase `--overlap` if seams are visible.
- `--tile-size` should match (or be a clean multiple of) the model's
  native input resolution for best accuracy.
- The 3D mesh's roof/wall vertices are welded per-facet by `trimesh`'s
  `process=True` but not globally across facet boundaries (ridge lines),
  so the mesh is visually seamless but not guaranteed strictly
  watertight — fine for visualization and most downstream GIS/BIM
  workflows, but re-mesh/repair first if you need a strictly manifold
  solid (e.g. for volume calculations).

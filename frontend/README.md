# Cesium 3D City Viewer (SIH 2026 / PS 26011)

A React 19 + CesiumJS single-page application that renders extruded 3D buildings within a 500m × 500m × 500m local coordinate space, provides interactive building picking with camera fly-to, and inspects ULPIN cadastral land parcel records.

---

## Architecture & Repositories

- **Backend**: Located at `sih2026/geotiff_segformer_gis/` (server at `server.py`). Serves `GET http://localhost:3000/` for 3D City GeoJSON and `GET http://localhost:3000/query/:ulpin` for parcel details.
- **Frontend**: Located at `sih2026/frontend/` (mirrored at `/home/abhayverma/city-cesium-viewer`). Built with React 19, Cesium 1.133+, and Vite 7.

---

## Getting Started

### 1. Start the Backend API Server

```bash
cd /home/abhayverma/sih2026/geotiff_segformer_gis
python3 server.py
```

The backend server listens on `http://localhost:3000`:
- `GET /` → City GeoJSON (FeatureCollection of extruded building polygons)
- `GET /query/:ulpin` → Comprehensive cadastral & legal parcel record
- `GET /health` → Backend health check

### 2. Start the Frontend Application

```bash
cd /home/abhayverma/sih2026/frontend
npm run dev
```

Open the app in your browser at `http://localhost:5173`.

---

## Features

- **Local Cartesian Reference Frame**: Maps local metres (0–500m) into real Cesium world space using an East-North-Up fixed frame anchor without treating coordinates as lat/lon.
- **Immediate Ground Plane**: Renders a 500m × 500m ground entity before the API request completes.
- **Independent Building Primitives**: Each feature is rendered as its own synchronous `Cesium.Primitive` with `PerInstanceColorAppearance` for reliable selection and camera focus.
- **Multi-Ring & MultiPolygon Support**: Seamlessly renders complex building footprints, including inner courtyards/holes and multi-structure polygons.
- **Click-to-Fly & Inspect**: Left-clicking any building smoothly flies the camera to the building's bounding sphere and displays the cadastral parcel JSON in a frosted-glass slide-in side panel.

# Cesium 3D City Viewer

## Run

```bash
npm install
npm run dev
```

The frontend expects:

```text
GET http://localhost:3000/
GET http://localhost:3000/query/:ulpin
```

Change them in `.env` if required.

## Expected city response

The loader accepts a GeoJSON `FeatureCollection`, a single GeoJSON `Feature`, or an array of features.

A building can look like:

```json
{
  "type": "Feature",
  "id": "building-1",
  "properties": {
    "ulpin": "123456",
    "height": 35
  },
  "geometry": {
    "type": "Polygon",
    "coordinates": [
      [
        [50, 50, 0],
        [100, 50, 0],
        [100, 100, 0],
        [50, 100, 0],
        [50, 50, 0]
      ]
    ]
  }
}
```

Coordinates are treated as local metres:

- X: 0–500
- Y: 0–500
- Z: 0–500

The renderer maps this local coordinate system into a small Cesium reference frame. It does not interpret the coordinates as latitude/longitude.

For 2D building footprints, use `properties.height`. If no height is supplied, the default is 20m.

For 3D coordinates, the renderer derives the building base/top from Z values when possible.

`ulpin`, `ULPIN`, `ulpin_id`, `id`, and GeoJSON `feature.id` are supported as building identifiers.

## Notes

The city ground is created before the API request for buildings is processed. Each building is rendered as an independent Cesium primitive so it can be picked and focused separately.

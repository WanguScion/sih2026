# geotiff-pipeline-api

Express API that:

1. Accepts a GeoTIFF orthophoto + a DSM (both `.tif`/`.tiff`, both required) at an upload endpoint.
2. Saves them as `survey.tif` and `dsm.tif` under `INPUT_PATH`.
3. Runs the Python pipeline at `MAIN_PIPELINE_PATH` as a subprocess.
4. Reads the resulting 3D vector GeoJSON from `OUTPUT_PATH`.
5. Generates a ULPIN from that GeoJSON via the ULPIN generator util (currently a stub — see below).
6. Logs the ULPIN (as primary key), the raw GeoJSON, and a derived PostGIS geometry into Postgres via Prisma, using raw SQL for every PostGIS-touching query.

## Environment variables

Copy `.env.example` to `.env` and fill in:

| Variable | Required | Meaning |
|---|---|---|
| `INPUT_PATH` | yes | Directory the two uploads are saved into, as `survey.tif` and `dsm.tif` |
| `OUTPUT_PATH` | yes | Where the pipeline's 3D GeoJSON result is read from — a direct `.geojson` file path, **or** a directory the pipeline writes into (both supported; see note below on which file gets picked when there's more than one) |
| `MAIN_PIPELINE_PATH` | yes | Path to the Python pipeline entry point |
| `DATABASE_URL` | yes | Postgres connection string (with PostGIS available), used by Prisma |
| `PORT` | no (default `3000`) | HTTP port |
| `PYTHON_EXECUTABLE` | no (default `python3`) | Interpreter used to run `MAIN_PIPELINE_PATH` |
| `PIPELINE_TIMEOUT_MS` | no (default `600000`) | Kill the pipeline subprocess if it runs longer than this |
| `MAX_UPLOAD_MB` | no (default `500`) | Max size per uploaded file |
| `UPLOAD_TMP_DIR` | no | Where uploads first land before being moved into `INPUT_PATH`; defaults to a folder under the OS temp dir |
| `CORS_ORIGIN` | no (default `*`) | Allowed browser origin for CORS. Set this to your frontend's actual origin (e.g. `https://app.example.com`) in production instead of leaving it wide open |

## Install & run

```bash
npm install
npx prisma generate
npx prisma migrate deploy   # or `migrate dev` in local dev, applies prisma/migrations/20240101000000_init
npm start                   # or `npm run dev` for nodemon
```

`prisma migrate` runs `CREATE EXTENSION IF NOT EXISTS postgis;` as part of the initial migration — this needs the DB user to have that privilege the first time (or PostGIS already enabled on the server/instance).

## API

### `POST /api/pipeline/process`

`multipart/form-data` with two required file fields:

- `geotiff` — the orthophoto, `.tif`/`.tiff`
- `dsm` — the DSM, `.tif`/`.tiff`

```bash
curl -X POST http://localhost:3000/api/pipeline/process \
  -F "geotiff=@survey.tif" \
  -F "dsm=@dsm.tif"
```

Response (`201`):
```json
{
  "ulpin": "…generated ULPIN…",
  "outputPath": "/abs/path/to/roofs_3d.geojson",
  "pipelineStdout": "…captured stdout from the python process…"
}
```

Missing/wrong-type files → `400`. Pipeline process failure → `502` (with `stderr`/`stdout` attached). Since the ULPIN is now the row's primary key, a run cannot be logged to the database at all until it has produced a real ULPIN — until `generateULPIN` is implemented, every otherwise-successful run stops with a `500` ("ULPIN generation is not yet implemented") right before the point where it would have logged to Postgres, and nothing is persisted. There is currently no separate failure log for pipeline errors; a failed run returns its `502` and is not written to the database (there's no ULPIN to key it by).

### `GET /api/pipeline/runs/:ulpin`

Returns the logged run for that ULPIN: `ulpin`, `geojson` (raw), `geom` (as GeoJSON, via `ST_AsGeoJSON`), `inputPath`, `outputPath`, `status`, `createdAt`.

### `GET /api/pipeline/query/:ulpin`

Identical to `GET /api/pipeline/runs/:ulpin` above (same handler) — a second path for frontend code that expects a `/query/:ulpin`-style lookup.

```bash
curl http://localhost:3000/api/pipeline/query/<ulpin>
```

Response (`200`):
```json
{
  "ulpin": "…",
  "geojson": { "type": "FeatureCollection", "features": [...] },
  "geom": "{\"type\":\"GeometryCollection\",\"geometries\":[...]}",
  "inputPath": "/abs/path/to/data/input",
  "outputPath": "/abs/path/to/roofs_3d.geojson",
  "status": "SUCCESS",
  "createdAt": "2026-09-27T04:30:00.000Z"
}
```

`geom` is returned as a GeoJSON string (from `ST_AsGeoJSON`) — parse it client-side if you need it as an object. No ULPIN found → `404`.

### `GET /api/pipeline/fetchall`

Returns every logged run, newest first.

```bash
curl http://localhost:3000/api/pipeline/fetchall
curl "http://localhost:3000/api/pipeline/fetchall?limit=20"
```

Optional `?limit=` (default `100`, clamped to `1`–`500`; a missing or non-numeric value falls back to the default rather than erroring).

Response (`200`):
```json
{
  "count": 2,
  "limit": 100,
  "runs": [
    { "ulpin": "…", "geojson": {...}, "geom": "…", "inputPath": "…", "outputPath": "…", "status": "SUCCESS", "createdAt": "…" },
    { "ulpin": "…", "geojson": {...}, "geom": "…", "inputPath": "…", "outputPath": "…", "status": "SUCCESS", "createdAt": "…" }
  ]
}
```

### `GET /health`

Liveness check, no DB dependency.

## Troubleshooting: ".tif upload isn't being accepted"

A few distinct problems all look like "the API won't take my file" from the frontend. Check in this order:

1. **CORS (the most common one for a browser frontend).** If the frontend runs on a different origin than this API (almost always true in dev — e.g. a Vite/React dev server on `:5173` calling an API on `:3000`), the browser sends a preflight `OPTIONS` request first. If that preflight fails, the browser never even attempts the actual upload — and depending on your dev tools, this can look like nothing happened rather than a clear error. This app now sends CORS headers (`cors` middleware, controlled by `CORS_ORIGIN`); if you're on an older copy of this project without it, that's very likely the cause. Check your browser's Network tab for a failed/red `OPTIONS` request to `/api/pipeline/process` to confirm.

2. **Field name mismatch.** The endpoint requires the multipart fields to be named **exactly** `geotiff` and `dsm`. A file sent under any other field name (`file`, `orthophoto`, `image`, etc.) is rejected by Multer before the controller even runs. This now returns a specific message — `Unexpected field "X". This endpoint expects multipart form fields named exactly "geotiff" and "dsm".` — distinct from the wrong-extension error, so check the actual response body for which one you're getting.

3. **Not actually sending `multipart/form-data`.** If the frontend sends the raw file as the request body (instead of wrapping it in a `FormData` object), or manually sets a `Content-Type` header instead of letting the browser/HTTP client set the multipart boundary itself, Multer won't parse any files at all — `req.files` will simply be empty, and you'll get `400 "Both files are required..."` even though you did attach a file. **Never set `Content-Type` manually** when sending `FormData` — let `fetch`/`axios`/the browser set it (it needs to include the multipart boundary string).

4. **File too large.** Default limit is 500MB per file (`MAX_UPLOAD_MB`). A file over that returns a `400` with Multer's `LIMIT_FILE_SIZE` code. Raise `MAX_UPLOAD_MB` if your GeoTIFFs are genuinely bigger than that.

5. **Wrong extension.** Only `.tif`/`.tiff` (case-insensitive) pass the filter — this is the one case that actually means what it says.

If none of these match what you're seeing, check the exact HTTP status code and response body (not just "it didn't work") — every rejection path above returns a distinct message precisely so this is diagnosable from the response alone.

## How MAIN_PIPELINE_PATH is invoked

The Python process is spawned with **both** a CLI-flag contract and an environment-variable contract, so whichever the target script reads works:

```
{PYTHON_EXECUTABLE} {MAIN_PIPELINE_PATH} --input-path {INPUT_PATH} --output-path {OUTPUT_PATH}
```

with `INPUT_PATH` and `OUTPUT_PATH` also injected into the subprocess's environment. If your Python entry point instead expects e.g. `--input`/`--dsm`/`--output-dir` flags (as the `geotiff_segformer_gis` project's `main.py` does), point `MAIN_PIPELINE_PATH` at a thin wrapper script that translates `--input-path`/`--output-path` (or the env vars) into that script's actual flags, rather than changing this app's contract per pipeline.

### Which file gets picked when OUTPUT_PATH is a directory

The pipeline can write more than one `.geojson` file into `OUTPUT_PATH` — e.g. `geotiff_segformer_gis`'s `main.py` writes both `buildings.geojson` (2D footprints, **no Z**) and `roofs_3d.geojson` (the actual 3D result, **with Z**) into the same output directory. When `OUTPUT_PATH` is a directory, this app picks whichever `.geojson` file has `3d` (case-insensitive) in its name; if none matches, it falls back to the first `.geojson` file found, in whatever order `fs.readdir` returns (not guaranteed to be alphabetical or creation-order).

If your pipeline's 3D output file doesn't have `3d` in its name, either rename it, or point `OUTPUT_PATH` directly at that file instead of at its containing directory — the direct-file form is always unambiguous.

## ULPIN generation

`src/utils/ULPINgeneratorUtil.js` is **intentionally left empty** for now, per the current requirements — it's a genuinely empty file (0 bytes), not a stub with a placeholder function. The controller checks for it defensively:

```js
if (typeof ulpinUtil.generateULPIN !== 'function') {
  throw new HttpError(500, 'ULPIN generation is not yet implemented.');
}
```

Since the ULPIN is now the primary key of `pipeline_runs`, there is no way to log a run without one — so until this file exports a real `generateULPIN(context)` function, every otherwise-successful pipeline run ends in that `500` instead of a database write. Implement it as:

```js
module.exports = {
  async generateULPIN({ geojson, inputPath, outputPath }) {
    return 'the-actual-ulpin';
  },
};
```

No other file needs to change when it's implemented — `context` already carries the parsed GeoJSON and both file paths.

## Database schema

One table now (see `prisma/schema.prisma` and `prisma/migrations/20240101000000_init/migration.sql`):

- **`pipeline_runs`** — `ulpin` (**primary key**, `TEXT`), `geojson` (the full raw pipeline result, `JSONB`), `geom` (a derived, spatially-indexed PostGIS `geometry(GeometryCollectionZ, 4326)` — every feature's geometry collected into one via `ST_Collect`), `inputPath`, `outputPath`, `status`, `createdAt`.

The previous per-facet `roof_facets` table has been removed — everything for a run now lives in one row, keyed by its ULPIN.

### Why raw queries

`geom` is declared in `schema.prisma` with `Unsupported("geometry(GeometryCollectionZ, 4326)")`, Prisma's own escape hatch for column types it can't model — Prisma Client **cannot** read or write that field through its normal generated API at all. So both the insert and the lookup go through `$executeRaw`/`$queryRaw` in `src/services/pipelineRecord.service.js`. The insert derives `geom` straight from the stored GeoJSON in the same statement:

```sql
SELECT ST_SetSRID(ST_Collect(ST_GeomFromGeoJSON(feature->'geometry')), 4326)
FROM jsonb_array_elements(:geojson::jsonb->'features') AS feature
```

and the lookup reads it back with `ST_AsGeoJSON(geom)`. All raw queries use Prisma's tagged-template form, which parameterizes values safely — no manual string concatenation into SQL anywhere.

## What's verified vs. what isn't (read this before assuming it all works)

I do not have a live Postgres/PostGIS instance or a working `prisma generate` in the sandbox this was built in (`binaries.prisma.sh`, which the Prisma CLI needs to download its query engine, isn't reachable from there). So:

**Verified by actually running it, end to end, against a stand-in Python script** (`test-fixtures/fake_pipeline.py` — safe to delete, it's only for this kind of local smoke test):
- Server boots, `/health` responds.
- `POST /api/pipeline/process` correctly rejects: both files missing, one file missing, wrong file extension — all `400` with clear messages.
- A valid upload is correctly moved to `INPUT_PATH/survey.tif` and `INPUT_PATH/dsm.tif`.
- The Python subprocess is spawned with the right args/env and its stdout/stderr are captured.
- The resulting GeoJSON at `OUTPUT_PATH` is correctly located and parsed — including, specifically, correctly preferring a `*3d*.geojson` file over a `buildings.geojson` (2D, no Z) sitting in the same output directory, and confirmed the resolved file's coordinates retain their Z value (`[x, y, z]`, 3 elements) all the way through to what gets handed to the raw SQL insert.
- With the ULPIN util left empty: the flow correctly stops with `500 "ULPIN generation is not yet implemented"` right before any database write — confirmed no row is written to Postgres in this state.
- With a temporary stub `generateULPIN()` swapped in: execution correctly reaches the raw-SQL insert construction (the `$executeRaw` call itself), failing only at Prisma's query-engine resolution — i.e. everything on the Node side of the DB boundary is confirmed correct.
- `GET /api/pipeline/query/:ulpin` and `GET /api/pipeline/fetchall` (including `?limit=` parsing/clamping for non-numeric and out-of-range values) route correctly and reach the same raw-SQL query-construction boundary before failing only at Prisma's query-engine resolution.
- CORS preflight (`OPTIONS`) now correctly returns `204` with `Access-Control-Allow-Origin`/`-Methods` headers, and real requests carry the `Access-Control-Allow-Origin` header — confirmed via `curl` with an `Origin` header set, simulating a cross-origin browser request.
- The field-name-mismatch vs. wrong-extension error messages are confirmed distinct: sending files under unrecognized field names returns the "Unexpected field" message; sending a non-`.tif` file under the correct field names returns the "only .tif/.tiff files are accepted" message.

**Not verified here** (needs a real Postgres+PostGIS instance and `npx prisma generate` to actually run, which needs internet access this sandbox didn't have):
- The `pipeline_runs` raw INSERT actually executing against real PostGIS.
- `ST_Collect`/`ST_GeomFromGeoJSON` correctly producing a valid `GeometryCollectionZ` from the real pipeline's multi-facet output.
- `GET /api/pipeline/runs/:ulpin` and its `ST_AsGeoJSON` round-trip.

Before relying on this, run it once yourself against a real Postgres+PostGIS database (`docker run postgis/postgis` is the fastest way to get one locally) and a real upload, and check `geom` actually populated correctly (e.g. `SELECT ST_AsText(geom) FROM pipeline_runs;`).

## Known dependency advisory

`npm audit` reports 4 high-severity advisories, all transitive through `prisma`'s own CLI tooling (`@prisma/config` → `mysql2`/`deepmerge-ts`, used for Prisma's multi-database-provider config support, not for anything this app uses since it only targets PostgreSQL). `prisma` is a devDependency — only `@prisma/client` ships in what actually runs in production. `npm audit fix --force` would downgrade `prisma` to `6.19.3`, which I did not do since 7.10.0 is the current stable release from the registry; worth revisiting if a patched release lands.

## Project layout

```
src/
  server.js                    Entry point
  app.js                       Express app wiring
  config/env.js                Env var loading/validation (cached singleton)
  routes/pipeline.routes.js    POST /process, GET /runs/:ulpin, GET /query/:ulpin, GET /fetchall
  controllers/pipeline.controller.js   Orchestrates the whole flow
  services/
    pipelineRunner.service.js  Spawns the Python subprocess
    pipelineRecord.service.js  Raw-SQL PostGIS reads/writes
    prismaClient.js            Lazy PrismaClient singleton
  middleware/
    upload.middleware.js       Multer config (.tif/.tiff only, both fields required)
    errorHandler.js            Central error -> JSON response mapping
  utils/
    ULPINgeneratorUtil.js      Intentionally unimplemented stub
prisma/
  schema.prisma
  migrations/20240101000000_init/migration.sql   Hand-written (see note above)
test-fixtures/
  fake_pipeline.py             Stand-in Python script for local smoke testing
```

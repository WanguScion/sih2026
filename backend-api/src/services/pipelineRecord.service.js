const { getPrismaClient } = require('./prismaClient');

function normalizeGeoJson(geojson) {
  if (geojson && geojson.type === 'FeatureCollection') return geojson;
  if (geojson && geojson.type === 'Feature') return { type: 'FeatureCollection', features: [geojson] };
  return { type: 'FeatureCollection', features: [] };
}

async function createPipelineRun({ ulpin, geojson, inputPath, outputPath, status }) {
  const prisma = getPrismaClient();
  const normalized = normalizeGeoJson(geojson);
  const geojsonStr = JSON.stringify(normalized);

  await prisma.$executeRaw`
    INSERT INTO pipeline_runs (ulpin, geojson, geom, "inputPath", "outputPath", status, "createdAt")
    VALUES (
      ${ulpin},
      ${geojsonStr}::jsonb,
      (
        SELECT ST_SetSRID(ST_Collect(ST_GeomFromGeoJSON(feature->'geometry')), 4326)
        FROM jsonb_array_elements(${geojsonStr}::jsonb->'features') AS feature
      ),
      ${inputPath},
      ${outputPath},
      ${status},
      NOW()
    )
  `;

  return ulpin;
}

async function getPipelineRun(ulpin) {
  const prisma = getPrismaClient();

  const rows = await prisma.$queryRaw`
    SELECT ulpin, geojson, ST_AsGeoJSON(geom) AS geom, "inputPath", "outputPath", status, "createdAt"
    FROM pipeline_runs
    WHERE ulpin = ${ulpin}
  `;

  return rows[0] ?? null;
}

async function getAllPipelineRuns(limit) {
  const prisma = getPrismaClient();

  return prisma.$queryRaw`
    SELECT ulpin, geojson, ST_AsGeoJSON(geom) AS geom, "inputPath", "outputPath", status, "createdAt"
    FROM pipeline_runs
    ORDER BY "createdAt" DESC
    LIMIT ${limit}
  `;
}

module.exports = { createPipelineRun, getPipelineRun, getAllPipelineRuns };

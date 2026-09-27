CREATE EXTENSION IF NOT EXISTS postgis;

CREATE TABLE "pipeline_runs" (
    "ulpin" TEXT NOT NULL,
    "geojson" JSONB NOT NULL,
    "geom" geometry(GeometryCollectionZ, 4326) NOT NULL,
    "inputPath" TEXT NOT NULL,
    "outputPath" TEXT NOT NULL,
    "status" TEXT NOT NULL,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "pipeline_runs_pkey" PRIMARY KEY ("ulpin")
);

CREATE INDEX "pipeline_runs_geom_idx" ON "pipeline_runs" USING GIST ("geom");

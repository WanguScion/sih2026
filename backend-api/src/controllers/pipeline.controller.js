const fs = require('fs/promises');
const path = require('path');

const { loadEnv } = require('../config/env');
const { runPipeline } = require('../services/pipelineRunner.service');
const { createPipelineRun, getPipelineRun, getAllPipelineRuns } = require('../services/pipelineRecord.service');
const ulpinUtil = require('../utils/ULPINgeneratorUtil');
const { HttpError } = require('../middleware/errorHandler');

async function placeUploadedFile(tempPath, finalPath) {
  try {
    await fs.rename(tempPath, finalPath);
  } catch (err) {
    if (err.code !== 'EXDEV') throw err;
    await fs.copyFile(tempPath, finalPath);
    await fs.unlink(tempPath);
  }
}

async function readResultGeoJson(outputPath) {
  let resolvedPath = outputPath;

  const stat = await fs.stat(outputPath).catch(() => null);
  if (!stat) {
    throw new HttpError(500, `Pipeline reported success but OUTPUT_PATH does not exist: ${outputPath}`);
  }

  if (stat.isDirectory()) {
    const entries = await fs.readdir(outputPath);
    const geojsonFile = entries.find((f) => f.toLowerCase().endsWith('.geojson'));
    if (!geojsonFile) {
      throw new HttpError(500, `No .geojson file found in OUTPUT_PATH directory: ${outputPath}`);
    }
    resolvedPath = path.join(outputPath, geojsonFile);
  }

  const raw = await fs.readFile(resolvedPath, 'utf-8');
  let parsed;
  try {
    parsed = JSON.parse(raw);
  } catch (err) {
    throw new HttpError(500, `Pipeline output at ${resolvedPath} is not valid JSON: ${err.message}`);
  }

  return { geojson: parsed, resolvedPath };
}

async function processPipeline(req, res) {
  const { inputPath, outputPath, mainPipelinePath, pythonExecutable, pipelineTimeoutMs } = loadEnv();

  const geotiffFile = req.files?.geotiff?.[0];
  const dsmFile = req.files?.dsm?.[0];

  if (!geotiffFile || !dsmFile) {
    throw new HttpError(
      400,
      'Both files are required: "geotiff" (the GeoTIFF orthophoto) and "dsm" (the DSM), each as .tif/.tiff.'
    );
  }

  await fs.mkdir(inputPath, { recursive: true });
  const surveyDest = path.join(inputPath, 'survey.tif');
  const dsmDest = path.join(inputPath, 'dsm.tif');

  await placeUploadedFile(geotiffFile.path, surveyDest);
  await placeUploadedFile(dsmFile.path, dsmDest);

  let pipelineResult;
  try {
    pipelineResult = await runPipeline({
      pythonExecutable,
      scriptPath: mainPipelinePath,
      inputPath,
      outputPath,
      timeoutMs: pipelineTimeoutMs,
    });
  } catch (err) {
    throw new HttpError(502, 'Pipeline execution failed', {
      message: err.message,
      stderr: err.stderr,
      stdout: err.stdout,
    });
  }

  const { geojson, resolvedPath } = await readResultGeoJson(outputPath);

  if (typeof ulpinUtil.generateULPIN !== 'function') {
    throw new HttpError(500, 'ULPIN generation is not yet implemented.');
  }

  const ulpin = await ulpinUtil.generateULPIN({ geojson, inputPath, outputPath: resolvedPath });

  if (!ulpin) {
    throw new HttpError(500, 'ULPIN generator did not return a ULPIN.');
  }

  await createPipelineRun({
    ulpin,
    geojson,
    inputPath,
    outputPath: resolvedPath,
    status: 'SUCCESS',
  });

  res.status(201).json({
    ulpin,
    outputPath: resolvedPath,
    pipelineStdout: pipelineResult.stdout,
  });
}

async function getRun(req, res) {
  const run = await getPipelineRun(req.params.ulpin);
  if (!run) {
    throw new HttpError(404, `No pipeline run found with ULPIN ${req.params.ulpin}`);
  }
  res.json(run);
}

async function getAllRuns(req, res) {
  const requestedLimit = parseInt(req.query.limit, 10);
  const limit = Number.isFinite(requestedLimit) ? Math.min(Math.max(requestedLimit, 1), 500) : 100;

  const runs = await getAllPipelineRuns(limit);
  res.json({ count: runs.length, limit, runs });
}

module.exports = { processPipeline, getRun, getAllRuns };

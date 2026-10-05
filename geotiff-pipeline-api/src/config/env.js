const path = require('path');

require('dotenv').config();

const REQUIRED_VARS = ['INPUT_PATH', 'OUTPUT_PATH', 'MAIN_PIPELINE_PATH', 'DATABASE_URL'];

let cachedConfig = null;

function loadEnv() {
  if (cachedConfig) return cachedConfig;

  const missing = REQUIRED_VARS.filter((key) => !process.env[key]);
  if (missing.length > 0) {
    throw new Error(
      `Missing required environment variable(s): ${missing.join(', ')}. ` +
        'Copy .env.example to .env and fill these in.'
    );
  }

  cachedConfig = {
    port: parseInt(process.env.PORT, 10) || 3000,
    inputPath: path.resolve(process.env.INPUT_PATH),
    outputPath: path.resolve(process.env.OUTPUT_PATH),
    mainPipelinePath: path.resolve(process.env.MAIN_PIPELINE_PATH),
    databaseUrl: process.env.DATABASE_URL,
    pythonExecutable: process.env.PYTHON_EXECUTABLE || 'python3',
    pipelineTimeoutMs: parseInt(process.env.PIPELINE_TIMEOUT_MS, 10) || 10 * 60 * 1000,
    maxUploadMb: parseInt(process.env.MAX_UPLOAD_MB, 10) || 500,
    uploadTmpDir: process.env.UPLOAD_TMP_DIR
      ? path.resolve(process.env.UPLOAD_TMP_DIR)
      : path.join(require('os').tmpdir(), 'geotiff-pipeline-uploads'),
  };

  return cachedConfig;
}

module.exports = { loadEnv };

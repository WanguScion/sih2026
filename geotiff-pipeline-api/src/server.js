const { loadEnv } = require('./config/env');
const { createApp } = require('./app');

const config = loadEnv();
const app = createApp();

app.listen(config.port, () => {
  console.log(`geotiff-pipeline-api listening on port ${config.port}`);
  console.log(`  INPUT_PATH         = ${config.inputPath}`);
  console.log(`  OUTPUT_PATH        = ${config.outputPath}`);
  console.log(`  MAIN_PIPELINE_PATH = ${config.mainPipelinePath}`);
});

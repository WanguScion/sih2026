const express = require('express');

const { loadEnv } = require('../config/env');
const { buildUploadMiddleware } = require('../middleware/upload.middleware');
const { processPipeline, getRun, getAllRuns } = require('../controllers/pipeline.controller');

const router = express.Router();

const config = loadEnv();
const upload = buildUploadMiddleware({
  uploadTmpDir: config.uploadTmpDir,
  maxUploadMb: config.maxUploadMb,
});

router.post('/process', upload, processPipeline);
router.get('/runs/:ulpin', getRun);
router.get('/query/:ulpin', getRun);
router.get('/fetchall', getAllRuns);

module.exports = router;

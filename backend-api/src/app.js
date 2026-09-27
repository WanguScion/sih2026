const express = require('express');

const pipelineRoutes = require('./routes/pipeline.routes');
const { errorHandler } = require('./middleware/errorHandler');

function createApp() {
  const app = express();

  app.use(express.json());

  app.get('/health', (req, res) => res.json({ status: 'ok' }));

  app.use('/api/pipeline', pipelineRoutes);

  app.use((req, res) => {
    res.status(404).json({ error: `Not found: ${req.method} ${req.originalUrl}` });
  });

  // Express 5 automatically forwards rejected promises from async route
  // handlers/middleware here, so no extra try/catch wrapping is needed
  // in the controllers.
  app.use(errorHandler);

  return app;
}

module.exports = { createApp };

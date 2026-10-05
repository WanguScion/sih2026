const multer = require('multer');

const EXPECTED_FIELDS = new Set(['geotiff', 'dsm']);

function errorHandler(err, req, res, next) { // eslint-disable-line no-unused-vars
  if (err instanceof multer.MulterError) {
    let message = err.message;
    if (err.code === 'LIMIT_UNEXPECTED_FILE') {
      message = EXPECTED_FIELDS.has(err.field)
        ? `Invalid file for field "${err.field}": only .tif/.tiff files are accepted.`
        : `Unexpected field "${err.field}". This endpoint expects multipart form fields named exactly "geotiff" and "dsm".`;
    }
    return res.status(400).json({ error: message, code: err.code });
  }

  if (err.statusCode) {
    return res.status(err.statusCode).json({ error: err.message, details: err.details });
  }

  console.error(err);
  return res.status(500).json({ error: 'Internal server error' });
}

class HttpError extends Error {
  constructor(statusCode, message, details) {
    super(message);
    this.statusCode = statusCode;
    this.details = details;
  }
}

module.exports = { errorHandler, HttpError };

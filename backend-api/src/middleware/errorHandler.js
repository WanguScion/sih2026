const multer = require('multer');

function errorHandler(err, req, res, next) { // eslint-disable-line no-unused-vars
  if (err instanceof multer.MulterError) {
    const message =
      err.code === 'LIMIT_UNEXPECTED_FILE'
        ? `Invalid file for field "${err.field}": only .tif/.tiff files are accepted.`
        : err.message;
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

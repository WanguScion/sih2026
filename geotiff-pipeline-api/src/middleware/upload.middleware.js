const fs = require('fs');
const path = require('path');
const multer = require('multer');

const TIF_EXTENSIONS = new Set(['.tif', '.tiff']);

function buildUploadMiddleware({ uploadTmpDir, maxUploadMb }) {
  fs.mkdirSync(uploadTmpDir, { recursive: true });

  const storage = multer.diskStorage({
    destination: (req, file, cb) => cb(null, uploadTmpDir),
    filename: (req, file, cb) => {
      const ext = path.extname(file.originalname).toLowerCase();
      cb(null, `${file.fieldname}-${Date.now()}-${Math.round(Math.random() * 1e9)}${ext}`);
    },
  });

  const fileFilter = (req, file, cb) => {
    const ext = path.extname(file.originalname).toLowerCase();
    if (!TIF_EXTENSIONS.has(ext)) {
      cb(new multer.MulterError('LIMIT_UNEXPECTED_FILE', file.fieldname), false);
      return;
    }
    cb(null, true);
  };

  const upload = multer({
    storage,
    fileFilter,
    limits: { fileSize: maxUploadMb * 1024 * 1024 },
  });

  // Requires both fields; either missing/extra fields are caught by the
  // controller (upload.fields doesn't itself enforce "required").
  return upload.fields([
    { name: 'geotiff', maxCount: 1 },
    { name: 'dsm', maxCount: 1 },
  ]);
}

module.exports = { buildUploadMiddleware, TIF_EXTENSIONS };

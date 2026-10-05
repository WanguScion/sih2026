const fs = require('fs/promises');
const path = require('path');
const crypto = require('crypto');

const ULPIN_LENGTH = 14;
const ULPIN_MODULUS = 10n ** BigInt(ULPIN_LENGTH); // 100000000000000n (14 digits)

/**
 * Deterministically derive a 14-digit ULPIN from a feature's geometry.
 * `salt` is only used to resolve collisions (same input => same output).
 */
function generateULPIN(feature, salt = 0) {
  if (!feature || feature.geometry === undefined || feature.geometry === null) {
    throw new Error('Cannot generate ULPIN: feature has no geometry');
  }

  const payload = salt === 0
    ? JSON.stringify(feature.geometry)
    : `${JSON.stringify(feature.geometry)}#${salt}`;

  const hash = crypto.createHash('sha256').update(payload).digest();

  let value = 0n;
  for (let i = 0; i < 8; i++) {
    value = (value << 8n) | BigInt(hash[i]);
  }

  return (value % ULPIN_MODULUS).toString().padStart(ULPIN_LENGTH, '0');
}

/**
 * Assign a unique ULPIN to every feature of a FeatureCollection (mutates in place).
 * Features that already carry a valid ULPIN keep it, so re-runs are idempotent.
 * Returns the ULPINs in feature order.
 */
function assignULPINs(geojson) {
  if (!geojson || geojson.type !== 'FeatureCollection' || !Array.isArray(geojson.features)) {
    throw new Error('Invalid GeoJSON: expected a FeatureCollection with a features array');
  }

  const used = new Set();

  // Reserve existing ULPINs first so new ones can never collide with them.
  for (const feature of geojson.features) {
    const existing = feature && feature.properties && feature.properties.ulpin;
    if (existing !== undefined && existing !== null) {
      const value = String(existing);
      if (!new RegExp(`^\\d{${ULPIN_LENGTH}}$`).test(value)) {
        throw new Error(`Feature has malformed existing ULPIN: ${value}`);
      }
      if (used.has(value)) {
        throw new Error(`Duplicate existing ULPIN found: ${value}`);
      }
      used.add(value);
    }
  }

  const ulpins = [];

  for (const feature of geojson.features) {
    const existing = feature.properties && feature.properties.ulpin;
    if (existing !== undefined && existing !== null) {
      ulpins.push(String(existing));
      continue;
    }

    let salt = 0;
    let ulpin = generateULPIN(feature, salt);
    while (used.has(ulpin)) {
      salt += 1;
      ulpin = generateULPIN(feature, salt);
    }

    used.add(ulpin);
    feature.properties = { ...(feature.properties || {}), ulpin };
    ulpins.push(ulpin);
  }

  return ulpins;
}

/**
 * Assign ULPINs to the given FeatureCollection and persist it to `outputPath`
 * (a single .geojson file). Mutates `geojson` in place and returns the ULPIN list.
 */
async function generateULPINs({ geojson, outputPath }) {
  if (!outputPath) {
    throw new Error('outputPath is required');
  }

  const ulpins = assignULPINs(geojson);

  const filePath = path.resolve(outputPath);
  const tmpPath = `${filePath}.${process.pid}.tmp`;
  try {
    await fs.writeFile(tmpPath, JSON.stringify(geojson, null, 2), 'utf8');
    await fs.rename(tmpPath, filePath); // atomic: never leaves a half-written file
  } catch (err) {
    await fs.unlink(tmpPath).catch(() => {});
    throw err;
  }

  return ulpins;
}

module.exports = {
  generateULPIN,
  assignULPINs,
  generateULPINs,
};

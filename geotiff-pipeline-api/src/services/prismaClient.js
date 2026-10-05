let prisma;

/**
 * Lazily requires @prisma/client AND creates the PrismaClient instance,
 * both on first use rather than at module load. This means the rest of
 * the app (e.g. GET /health) still boots even before `prisma generate`
 * has been run / before DATABASE_URL is reachable — the failure only
 * surfaces when a route actually needs the DB.
 */
function getPrismaClient() {
  if (!prisma) {
    const { PrismaClient } = require('@prisma/client');
    prisma = new PrismaClient();
  }
  return prisma;
}

module.exports = { getPrismaClient };

/**
 * H.7.1 backfill - replace openid-derived display names with the friendly
 * `匿名玩家_xxxx` handle. The fix is purely cosmetic; `username` is left
 * unchanged because it is the unique handle and may be referenced elsewhere.
 *
 * Detection: `displayName` matches /^(wx_|wxopenid_|wxunionid_|openid_|unionid_|oauth_)[a-zA-Z0-9_.\-]+$/i
 * - Built from the user UUID, suffix = first 4 hex chars (no dashes).
 *
 * Usage:
 *   DATABASE_URL=... npx ts-node scripts/backfill_anonymous_display_name.ts            # dry run
 *   DATABASE_URL=... npx ts-node scripts/backfill_anonymous_display_name.ts --apply    # mutate
 *
 * Idempotent: rows already showing `匿名玩家_xxxx` are skipped.
 */

import { PrismaClient } from '@prisma/client';

const prisma = new PrismaClient();

const APPLY = process.argv.includes('--apply');
const BATCH_SIZE = 200;
const OPEN_ID_LIKE =
  /^(wx_|wxopenid_|wxunionid_|openid_|unionid_|oauth_)[a-zA-Z0-9_.\-]+$/i;

function buildAnonymousDisplayName(userId: string): string {
  const suffix = (userId || '').replace(/-/g, '').slice(0, 4) || 'xxxx';
  return `匿名玩家_${suffix}`;
}

async function main() {
  const total = await prisma.user.count();
  console.log(`[backfill_anonymous_display_name] total users: ${total} (apply=${APPLY})`);

  let cursor: string | undefined;
  let scanned = 0;
  let updated = 0;
  let skippedClean = 0;

  while (true) {
    const batch = await prisma.user.findMany({
      take: BATCH_SIZE,
      ...(cursor ? { skip: 1, cursor: { id: cursor } } : {}),
      orderBy: { id: 'asc' },
      select: { id: true, username: true, displayName: true },
    });

    if (batch.length === 0) break;
    cursor = batch[batch.length - 1].id;

    for (const row of batch) {
      scanned += 1;
      const display = (row.displayName || '').trim();
      // displayName missing entirely is also dirty: presenter would have to
      // fall back to username, which can itself be openid-like.
      const dirty =
        !display ||
        OPEN_ID_LIKE.test(display) ||
        (!!row.username && display === row.username && OPEN_ID_LIKE.test(row.username));
      if (!dirty) {
        skippedClean += 1;
        continue;
      }
      const next = buildAnonymousDisplayName(row.id);
      if (APPLY) {
        await prisma.user.update({
          where: { id: row.id },
          data: { displayName: next },
        });
      }
      updated += 1;
      if (updated <= 5 || updated % 50 === 0) {
        console.log(
          `[backfill_anonymous_display_name] ${row.id} "${display}" -> "${next}"`,
        );
      }
    }
  }

  console.log('[backfill_anonymous_display_name] summary:');
  console.log(`  scanned          : ${scanned}`);
  console.log(`  updated          : ${updated}`);
  console.log(`  skipped (clean)  : ${skippedClean}`);
  if (!APPLY) {
    console.log('\nDry run only. Re-run with --apply to write changes.');
  }
}

main()
  .catch((err) => {
    console.error('[backfill_anonymous_display_name] failed:', err);
    process.exit(1);
  })
  .finally(async () => {
    await prisma.$disconnect();
  });

/**
 * H.5.1 backfill - populate `games.user_idea` for legacy rows.
 *
 * Strategy:
 *  - Skip rows that already have a non-empty `userIdea`.
 *  - For the rest, run `description` through the same regex as the runtime
 *    `sanitize-idea` util (frontend `sanitizeUserIdea` and backend
 *    `sanitizeUserIdea` share these patterns).
 *  - Write the cleaned string back into `userIdea`. `description` itself is
 *    kept untouched so the LLM pipeline still has the historical input.
 *
 * Usage:
 *   DATABASE_URL=... npx ts-node scripts/backfill_user_idea.ts            # dry run
 *   DATABASE_URL=... npx ts-node scripts/backfill_user_idea.ts --apply    # mutate
 *
 * Idempotent: re-running after --apply is a no-op for processed rows.
 */

import { PrismaClient } from '@prisma/client';
import { sanitizeUserIdea } from '../packages/game-service/src/common/sanitize-idea';

const prisma = new PrismaClient();

const APPLY = process.argv.includes('--apply');
const BATCH_SIZE = 200;

async function main() {
  const total = await prisma.game.count();
  console.log(`[backfill_user_idea] total games: ${total} (apply=${APPLY})`);

  let cursor: string | undefined;
  let scanned = 0;
  let updated = 0;
  let skippedAlreadyClean = 0;
  let skippedEmpty = 0;

  while (true) {
    const batch = await prisma.game.findMany({
      take: BATCH_SIZE,
      ...(cursor ? { skip: 1, cursor: { id: cursor } } : {}),
      orderBy: { id: 'asc' },
      select: { id: true, description: true, userIdea: true },
    });

    if (batch.length === 0) break;
    cursor = batch[batch.length - 1].id;

    for (const row of batch) {
      scanned += 1;
      if (row.userIdea && row.userIdea.trim().length > 0) {
        skippedAlreadyClean += 1;
        continue;
      }
      const cleaned = sanitizeUserIdea(row.description);
      if (!cleaned) {
        skippedEmpty += 1;
        continue;
      }
      if (APPLY) {
        await prisma.game.update({
          where: { id: row.id },
          data: { userIdea: cleaned },
        });
      }
      updated += 1;
      if (updated <= 5 || updated % 50 === 0) {
        console.log(
          `[backfill_user_idea] ${row.id} -> "${cleaned.slice(0, 60)}${
            cleaned.length > 60 ? '…' : ''
          }"`,
        );
      }
    }
  }

  console.log('[backfill_user_idea] summary:');
  console.log(`  scanned             : ${scanned}`);
  console.log(`  updated             : ${updated}`);
  console.log(`  skipped (clean)     : ${skippedAlreadyClean}`);
  console.log(`  skipped (no idea)   : ${skippedEmpty}`);
  if (!APPLY) {
    console.log('\nDry run only. Re-run with --apply to write changes.');
  }
}

main()
  .catch((err) => {
    console.error('[backfill_user_idea] failed:', err);
    process.exit(1);
  })
  .finally(async () => {
    await prisma.$disconnect();
  });

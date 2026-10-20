#!/usr/bin/env node
// Build/dev staging only: block snapshots are the canonical tracked sources.
import { cp, mkdir, readdir, rm } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const docsRoot = dirname(dirname(fileURLToPath(import.meta.url)));
const repoRoot = dirname(docsRoot);
const target = join(docsRoot, 'public/dashboard_demo');
const blocks = ['curator', 'terminal-curator', 'tracer', 'trainer', 'evaluator'];
await rm(target, { recursive: true, force: true });
await mkdir(target, { recursive: true });
for (const block of blocks) {
  const source = join(repoRoot, 'blocks', block, 'docs/public/dashboard_demo', block);
  try {
    await readdir(source);
  } catch (error) {
    if (error.code === 'ENOENT') continue;
    throw error;
  }
  await cp(source, join(target, block), { recursive: true });
  process.stdout.write(`Staged dashboard demo: ${block}\n`);
}

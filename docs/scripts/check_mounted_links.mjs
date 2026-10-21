#!/usr/bin/env node
// Exercise the real source adapter and installed Fumadocs loaders without
// requiring Next's custom MDX import hook or starting a server.
import assert from 'node:assert/strict';
import { readFile, readdir } from 'node:fs/promises';
import { dirname, join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';
import ts from 'typescript';

const docsRoot = dirname(dirname(fileURLToPath(import.meta.url)));
const repoRoot = dirname(docsRoot);
async function collection(directory) {
  const files = [];
  async function visit(dir) {
    for (const entry of await readdir(dir, { withFileTypes: true })) {
      const path = join(dir, entry.name);
      if (entry.isDirectory()) await visit(path);
      else if (entry.name.endsWith('.mdx')) {
        files.push({ type: 'page', path: relative(directory, path), data: { title: entry.name } });
      } else if (entry.name === 'meta.json') {
        files.push({ type: 'meta', path: relative(directory, path), data: JSON.parse(await readFile(path, 'utf8')) });
      }
    }
  }
  await visit(directory);
  return { toFumadocsSource: () => ({ files }) };
}
const blocks = ['curator', 'terminal-curator', 'tracer', 'trainer', 'evaluator'];
const collections = { docs: await collection(join(docsRoot, 'content/docs')) };
for (const block of blocks) {
  const name = block === 'terminal-curator' ? 'terminalCuratorDocs' : `${block}Docs`;
  collections[name] = await collection(join(repoRoot, 'blocks', block, 'docs/content/docs'));
}
globalThis.__legoflowDocsCollections = collections;
let input = await readFile(join(docsRoot, 'src/lib/source.ts'), 'utf8');
input = input.replace(/import\s+\{[^]*?\}\s+from ['"]@\/\.source\/server['"];\s*/,
  'const { docs, curatorDocs, terminalCuratorDocs, tracerDocs, trainerDocs, evaluatorDocs } = globalThis.__legoflowDocsCollections;\n');
let code = ts.transpileModule(input, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
for (const name of ['fumadocs-core/source', 'fumadocs-core/source/lucide-icons']) {
  code = code.replaceAll(`from '${name}'`, `from '${import.meta.resolve(name)}'`);
  code = code.replaceAll(`from "${name}"`, `from '${import.meta.resolve(name)}'`);
}
const { source } = await import(`data:text/javascript;base64,${Buffer.from(code).toString('base64')}`);
delete globalThis.__legoflowDocsCollections;

for (const block of blocks) {
  const href = `/docs/blocks/${block}/getting-started`;
  assert.equal(source.getPageByHref(href)?.page.url, href, `mounted lookup: ${block}`);
  assert.equal(source.getPageByHref(`${href}#setup`)?.hash, 'setup', `anchor lookup: ${block}`);
}
const terminalBase = '/docs/blocks/terminal-curator';
const terminalDirectory = join(repoRoot, 'blocks/terminal-curator/docs/content/docs');
const terminalMeta = JSON.parse(await readFile(join(terminalDirectory, 'meta.json'), 'utf8'));
assert.deepEqual(terminalMeta.pages, ['index', 'getting-started', 'usage', 'design', 'outputs', 'dashboard']);
const usageMeta = JSON.parse(await readFile(join(terminalDirectory, 'usage/meta.json'), 'utf8'));
assert.equal(usageMeta.title, 'Usage');
assert.deepEqual(usageMeta.pages, ['configuration', 'validation-checks', 'downstream-handoff']);
for (const slug of ['design', 'outputs', 'dashboard', ...usageMeta.pages.map((name) => `usage/${name}`)]) {
  assert.equal(source.getPageByHref(`${terminalBase}/${slug}`)?.page.url, `${terminalBase}/${slug}`);
}
const terminal = source.getPage(['blocks', 'terminal-curator', 'usage', 'validation-checks']);
assert.equal(terminal.url, `${terminalBase}/usage/validation-checks`);
assert.equal(source.resolveHref('./configuration.mdx#source-modes', terminal), `${terminalBase}/usage/configuration#source-modes`);
assert.equal(source.resolveHref('../outputs.mdx#reports-and-checksums', terminal), `${terminalBase}/outputs#reports-and-checksums`);
assert.equal(source.resolveHref('/docs/usage/configuration#source-modes', terminal), `${terminalBase}/usage/configuration#source-modes`);
assert.equal(source.resolveHref('#task-acceptance', terminal), '#task-acceptance');
assert.equal(source.getPageByHref('/docs/blocks/terminal-curator#where-to-go-next')?.page.url, '/docs/blocks/terminal-curator');
assert.equal(source.getPageByHref('/docs/blocks/terminal-curator#where-to-go-next')?.hash, 'where-to-go-next');
const cross = '/docs/blocks/tracer/getting-started#configuration';
assert.equal(source.resolveHref(cross, terminal), cross);
assert.equal(source.getPageByHref(cross)?.page.url, '/docs/blocks/tracer/getting-started');
assert.equal(source.getPageByHref(cross)?.hash, 'configuration');
const nested = source.getPage(['blocks', 'tracer', 'usage', 'configuration']);
assert.equal(source.resolveHref('../outputs.mdx#formats', nested), '/docs/blocks/tracer/outputs#formats');
assert.equal(source.getPageByHref('/docs/blocks/terminal-curator-extra/workflow'), undefined);
assert.equal(source.resolveHref('/docs/blocks/terminal-curator-extra/workflow', terminal), '/docs/blocks/terminal-curator-extra/workflow');
assert.equal(source.resolveHref('/docs/getting-started', { ...terminal, url: '/docs/blocks/terminal-curator-extra/workflow' }), '/docs/getting-started');
const redirects = await readFile(join(docsRoot, 'public/_redirects'), 'utf8');
for (const [oldSlug, newSlug] of [['configuration', 'usage/configuration'], ['workflow', 'usage/validation-checks'], ['evaluator-handoff', 'usage/downstream-handoff#evaluator']]) {
  assert.ok(redirects.split('\n').some((line) => {
    const [from, to, status] = line.trim().split(/\s+/);
    return from === `${terminalBase}/${oldSlug}` && to === `${terminalBase}/${newSlug}` && status === '301';
  }), `old terminal URL redirects: ${oldSlug}`);
}
console.log('PASS: real Fumadocs loaders — all five mounted namespaces, relative/nested links, cross-block links, anchors and strict boundaries.');

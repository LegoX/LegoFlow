import {
  defineConfig,
  defineDocs,
  frontmatterSchema,
  metaSchema,
} from 'fumadocs-mdx/config';
import path from 'node:path';

const repoRoot = path.resolve(process.cwd(), '..');

export const docs = defineDocs({
  dir: 'content/docs',
  docs: {
    schema: frontmatterSchema,
  },
  meta: {
    schema: metaSchema,
  },
});

export const curatorDocs = defineDocs({
  dir: path.join(repoRoot, 'subblock/curator/docs/content/docs'),
  docs: {
    schema: frontmatterSchema,
  },
  meta: {
    schema: metaSchema,
  },
});

export const tracerDocs = defineDocs({
  dir: path.join(repoRoot, 'subblock/tracer/docs/content/docs'),
  docs: {
    schema: frontmatterSchema,
  },
  meta: {
    schema: metaSchema,
  },
});

export const trainerDocs = defineDocs({
  dir: path.join(repoRoot, 'subblock/trainer/docs/content/docs'),
  docs: {
    schema: frontmatterSchema,
  },
  meta: {
    schema: metaSchema,
  },
});

export const evaluatorDocs = defineDocs({
  dir: path.join(repoRoot, 'subblock/evaluator/docs/content/docs'),
  docs: {
    schema: frontmatterSchema,
  },
  meta: {
    schema: metaSchema,
  },
});

export default defineConfig({
  mdxOptions: {},
});
